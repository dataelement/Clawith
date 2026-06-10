"""S3-compatible object storage backend."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from loguru import logger

from app.services.storage_runtime.base import (
    ConditionalWriteResult,
    StorageBackend,
    StorageEntry,
    StorageVersion,
    WriteCondition,
)
from app.services.storage_runtime.utils import normalize_storage_key


class S3StorageBackend(StorageBackend):
    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "",
        region: str = "",
        endpoint_url: str = "",
        access_key_id: str = "",
        secret_access_key: str = "",
        presign_ttl_seconds: int = 3600,
        max_pool_connections: int = 50,
        write_workers: int = 32,
    ):
        self.bucket = bucket
        self.prefix = normalize_storage_key(prefix)
        self.region = region
        self.endpoint_url = endpoint_url or None
        self.access_key_id = access_key_id or None
        self.secret_access_key = secret_access_key or None
        self.presign_ttl_seconds = presign_ttl_seconds
        self.max_pool_connections = max_pool_connections
        self._client: Any | None = None
        self._aioboto3_session: Any | None = None

    def _object_key(self, key: str) -> str:
        normalized = normalize_storage_key(key)
        return f"{self.prefix}/{normalized}" if self.prefix else normalized

    def _is_gcs(self) -> bool:
        """Return True if the endpoint targets Google Cloud Storage."""
        if not self.endpoint_url:
            return False
        return "storage.googleapis.com" in self.endpoint_url

    def _boto_config(self):
        """Build a botocore Config appropriate for the target endpoint."""
        from botocore.config import Config

        if self._is_gcs():
            # GCS S3-compatible API requires virtual-hosted-style addressing
            # and an explicit region of "auto" for V4 signatures to verify.
            addressing = "virtual"
            region = "auto"
        else:
            addressing = "path"
            region = self.region or None
        return Config(
            max_pool_connections=self.max_pool_connections,
            proxies={},
            s3={"addressing_style": addressing},
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=30,
            tcp_keepalive=True,
            region_name=region,
        )

    def _client_or_raise(self):
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError("boto3 is required for S3 storage backend") from exc
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                config=self._boto_config(),
            )
        return self._client

    @asynccontextmanager
    async def _async_client(self):
        """Shared aioboto3 session with aiohttp connection pool — reuses connections but detects stale ones correctly."""
        try:
            import aioboto3
        except ImportError as exc:
            raise RuntimeError("aioboto3 is required for async S3 writes") from exc
        if self._aioboto3_session is None:
            self._aioboto3_session = aioboto3.Session()
        async with self._aioboto3_session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            config=self._boto_config(),
        ) as client:
            yield client

    async def exists(self, key: str) -> bool:
        return await self._object_exists(key)

    async def is_file(self, key: str) -> bool:
        return await self._object_exists(key)

    async def _object_exists(self, key: str) -> bool:
        object_key = self._object_key(key)
        client = self._client_or_raise()
        response = await asyncio.to_thread(
            client.list_objects_v2,
            Bucket=self.bucket,
            Prefix=object_key,
            MaxKeys=1,
        )
        return any(item.get("Key") == object_key for item in response.get("Contents", []))

    async def is_dir(self, key: str) -> bool:
        prefix = self._object_key(key).rstrip("/") + "/"
        client = self._client_or_raise()
        response = await asyncio.to_thread(
            client.list_objects_v2,
            Bucket=self.bucket,
            Prefix=prefix,
            Delimiter="/",
            MaxKeys=1,
        )
        return bool(response.get("Contents") or response.get("CommonPrefixes"))

    async def list_dir(self, key: str) -> list[StorageEntry]:
        prefix = self._object_key(key).rstrip("/")
        if prefix:
            prefix += "/"
        client = self._client_or_raise()
        response = await asyncio.to_thread(
            client.list_objects_v2,
            Bucket=self.bucket,
            Prefix=prefix,
            Delimiter="/",
        )
        entries: list[StorageEntry] = []
        for item in response.get("CommonPrefixes", []):
            raw = item.get("Prefix", "").rstrip("/")
            rel = _strip_prefix(raw, self.prefix)
            name = rel.split("/")[-1]
            entries.append(StorageEntry(name=name, key=rel, is_dir=True))
        for item in response.get("Contents", []):
            raw = item.get("Key", "")
            if not raw or raw == prefix:
                continue
            rel = _strip_prefix(raw, self.prefix)
            name = rel.split("/")[-1]
            entries.append(
                StorageEntry(
                    name=name,
                    key=rel,
                    is_dir=False,
                    size=int(item.get("Size", 0)),
                    modified_at=str(item.get("LastModified") or ""),
                    etag=_clean_etag(item.get("ETag")),
                )
            )
        return sorted(entries, key=lambda entry: (not entry.is_dir, entry.name))

    async def read_bytes(self, key: str) -> bytes:
        client = self._client_or_raise()
        response = await asyncio.to_thread(
            client.get_object,
            Bucket=self.bucket,
            Key=self._object_key(key),
        )
        body = response["Body"]
        return await asyncio.to_thread(body.read)

    async def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        # GCS S3-compatible API requires an explicit Content-Type; without it
        # the V4 signature body-hash is calculated on an empty content-type,
        # but GCS applies a different default — causing SignatureDoesNotMatch.
        resolved_ct = content_type or "application/octet-stream"
        kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": self._object_key(key),
            "Body": data,
            "ContentType": resolved_ct,
        }
        async with self._async_client() as client:
            await client.put_object(**kwargs)

    async def delete(self, key: str) -> None:
        async with self._async_client() as client:
            await client.delete_object(
                Bucket=self.bucket,
                Key=self._object_key(key),
            )

    async def delete_tree(self, key: str) -> None:
        client = self._client_or_raise()
        prefix = self._object_key(key).rstrip("/") + "/"
        response = await asyncio.to_thread(
            client.list_objects_v2,
            Bucket=self.bucket,
            Prefix=prefix,
        )
        contents = response.get("Contents", [])
        if not contents:
            return
        objects = [{"Key": item["Key"]} for item in contents]
        async with self._async_client() as client:
            await client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": objects},
            )

    async def stat(self, key: str) -> StorageEntry:
        version = await self.get_version(key)
        if not version.exists:
            raise FileNotFoundError(key)
        return StorageEntry(
            name=normalize_storage_key(key).split("/")[-1],
            key=normalize_storage_key(key),
            is_dir=version.is_dir,
            size=version.size,
            modified_at=version.modified_at,
            etag=version.etag,
            version_id=version.version_id,
            content_hash=version.content_hash,
        )

    async def get_version(self, key: str) -> StorageVersion:
        client = self._client_or_raise()
        object_key = self._object_key(key)
        try:
            response = await asyncio.to_thread(
                client.head_object,
                Bucket=self.bucket,
                Key=object_key,
            )
        except Exception:
            return StorageVersion(key=normalize_storage_key(key), exists=False, is_dir=False)
        return StorageVersion(
            key=normalize_storage_key(key),
            exists=True,
            is_dir=False,
            size=int(response.get("ContentLength", 0)),
            modified_at=str(response.get("LastModified") or ""),
            etag=_clean_etag(response.get("ETag")),
            version_id=str(response.get("VersionId") or ""),
            content_hash=_clean_etag(response.get("ETag")),
        )

    async def write_bytes_if_match(
        self,
        key: str,
        data: bytes,
        *,
        condition: WriteCondition | None = None,
        content_type: str | None = None,
    ) -> ConditionalWriteResult:
        current = await self.get_version(key)
        if condition:
            if condition.require_absent and current.exists:
                return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
            if condition.version_token is not None and current.token != condition.version_token:
                return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
        await self.write_bytes(key, data, content_type=content_type)
        return ConditionalWriteResult(ok=True, current_version=await self.get_version(key))

    async def _put_succeeded(self, key: str, expected_size: int) -> bool:
        try:
            entry = await self.stat(key)
        except Exception:
            return False
        return entry.size == expected_size

    async def local_path_for(self, key: str) -> Path | None:
        suffix = Path(normalize_storage_key(key)).suffix
        tmp = NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.close()
        path = Path(tmp.name)
        await self.write_local_copy(key, path)
        return path

    async def write_local_copy(self, key: str, path: Path) -> None:
        data = await self.read_bytes(key)
        await asyncio.to_thread(path.write_bytes, data)

    async def presign_download_url(self, key: str, filename: str | None = None, inline: bool = False) -> str | None:
        client = self._client_or_raise()
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": self._object_key(key)}
        if filename:
            disposition = "inline" if inline else "attachment"
            params["ResponseContentDisposition"] = f'{disposition}; filename="{filename}"'
        url = await asyncio.to_thread(
            client.generate_presigned_url,
            "get_object",
            Params=params,
            ExpiresIn=self.presign_ttl_seconds,
        )
        if url and self.endpoint_url:
            from urllib.parse import urlparse, urlunparse
            parsed_url = urlparse(url)
            parsed_endpoint = urlparse(self.endpoint_url)
            if parsed_url.netloc == parsed_endpoint.netloc:
                # MinIO-style endpoint: rewrite path with /minio prefix
                new_path = "/minio" + parsed_url.path
                url = urlunparse(("", "", new_path, parsed_url.params, parsed_url.query, parsed_url.fragment))
            # GCS (storage.googleapis.com): presigned URLs are already correct, no rewrite needed
        return url


def _strip_prefix(raw_key: str, prefix: str) -> str:
    if prefix and raw_key.startswith(prefix + "/"):
        return raw_key[len(prefix) + 1:]
    return raw_key


def _is_header_parsing_error(exc: Exception) -> bool:
    try:
        from urllib3.exceptions import HeaderParsingError
    except Exception:
        return False
    return isinstance(exc, HeaderParsingError)


def _clean_etag(raw: Any) -> str:
    if raw is None:
        return ""
    text = str(raw)
    return text.strip('"')
