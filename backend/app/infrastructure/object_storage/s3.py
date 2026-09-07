"""S3-compatible object storage backend."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from functools import wraps
from types import CoroutineType
from typing import Any, ParamSpec, TypeVar

from botocore.exceptions import BotoCoreError, ClientError

from app.infrastructure.object_storage.base import (
    ConditionalWriteResult,
    StorageBackend,
    StorageEntry,
    StorageError,
    StorageVersion,
    WriteCondition,
)
from app.infrastructure.object_storage.utils import normalize_storage_key

P = ParamSpec("P")
T = TypeVar("T")


def _storage_errors(function: Callable[P, Awaitable[T]]) -> Callable[P, CoroutineType[object, object, T]]:
    @wraps(function)
    async def normalized(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await function(*args, **kwargs)
        except (ClientError, BotoCoreError) as exc:
            raise StorageError("Object storage request failed") from exc
    return normalized


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
        lock_provider: Callable[[str], AbstractAsyncContextManager[None]] | None = None,
    ):
        self.bucket = bucket
        self.prefix = normalize_storage_key(prefix)
        self.region = region
        self.endpoint_url = endpoint_url or None
        self.access_key_id = access_key_id or None
        self.secret_access_key = secret_access_key or None
        self.presign_ttl_seconds = presign_ttl_seconds
        self.max_pool_connections = max_pool_connections
        self._lock_provider = lock_provider
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

    @_storage_errors
    async def exists(self, key: str) -> bool:
        return await self._object_exists(key)

    def resource_lock(self, key: str) -> AbstractAsyncContextManager[None]:
        if self._lock_provider is None:
            raise RuntimeError("S3 resource locks require a cross-process lock provider")
        return self._lock_provider(f"{self.endpoint_url or 'aws'}:{self.bucket}:{self._object_key(key)}")

    @_storage_errors
    async def mkdir(self, key: str) -> None:
        async with self._async_client() as client:
            await client.put_object(Bucket=self.bucket, Key=self._object_key(key).rstrip("/") + "/", Body=b"")

    @_storage_errors
    async def read_versioned(self, key: str, *, max_bytes: int) -> tuple[bytes, StorageVersion]:
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        def read():
            try:
                response = self._client_or_raise().get_object(Bucket=self.bucket, Key=self._object_key(key))
            except Exception as exc:
                if _is_missing_object_error(exc):
                    raise FileNotFoundError(key) from exc
                raise
            body = response["Body"]
            try:
                size = int(response["ContentLength"])
                if size > max_bytes:
                    raise ValueError("Storage object exceeds max_bytes")
                data = body.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError("Storage object exceeds max_bytes")
                if len(data) != size:
                    raise StorageError("Incomplete storage object body")
                etag = _clean_etag(response.get("ETag"))
                if not etag:
                    raise StorageError("S3 read response requires an ETag")
                return data, StorageVersion(key=normalize_storage_key(key), exists=True, is_dir=False, size=size, etag=etag, version_id=str(response.get("VersionId") or ""), modified_at=str(response.get("LastModified") or ""))
            finally:
                body.close()
        return await asyncio.to_thread(read)

    @_storage_errors
    async def list_dir_page(self, key: str, *, limit: int, cursor: str | None = None) -> tuple[list[StorageEntry], str | None]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        prefix = self._object_key(key).rstrip("/")
        prefix = prefix + "/" if prefix else ""
        request: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix, "Delimiter": "/", "MaxKeys": limit}
        if cursor is not None:
            request["ContinuationToken"] = cursor
        response = await asyncio.to_thread(self._client_or_raise().list_objects_v2, **request)
        entries: list[StorageEntry] = []
        for item in response.get("CommonPrefixes", []):
            rel = _strip_prefix(item["Prefix"].rstrip("/"), self.prefix)
            entries.append(StorageEntry(name=rel.rsplit("/", 1)[-1], key=rel, is_dir=True))
        for item in response.get("Contents", []):
            if item["Key"] == prefix:
                continue
            rel = _strip_prefix(item["Key"], self.prefix)
            entries.append(StorageEntry(name=rel.rsplit("/", 1)[-1], key=rel, is_dir=False, size=int(item.get("Size", 0)), modified_at=str(item.get("LastModified") or ""), etag=_clean_etag(item.get("ETag"))))
        if len(entries) > limit:
            raise StorageError("S3 listing exceeded requested page size")
        next_cursor = response.get("NextContinuationToken") if response.get("IsTruncated") else None
        if response.get("IsTruncated") and not next_cursor:
            raise StorageError("S3 listing omitted continuation token")
        return entries, next_cursor

    @_storage_errors
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

    @_storage_errors
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

    @_storage_errors
    async def list_dir(self, key: str) -> list[StorageEntry]:
        prefix = self._object_key(key).rstrip("/")
        if prefix:
            prefix += "/"
        client = self._client_or_raise()
        entries: list[StorageEntry] = []
        continuation_token: str | None = None
        while True:
            request: dict[str, Any] = {
                "Bucket": self.bucket,
                "Prefix": prefix,
                "Delimiter": "/",
            }
            if continuation_token:
                request["ContinuationToken"] = continuation_token
            response = await asyncio.to_thread(client.list_objects_v2, **request)
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
            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")
            if not continuation_token:
                break
        return sorted(entries, key=lambda entry: (not entry.is_dir, entry.name))

    @_storage_errors
    async def read_bytes(self, key: str) -> bytes:
        client = self._client_or_raise()
        try:
            response = await asyncio.to_thread(
                client.get_object,
                Bucket=self.bucket,
                Key=self._object_key(key),
            )
        except Exception as exc:
            if _is_missing_object_error(exc):
                raise FileNotFoundError(key) from exc
            raise
        body = response["Body"]
        return await asyncio.to_thread(body.read)

    @_storage_errors
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

    @_storage_errors
    async def delete(self, key: str) -> None:
        async with self._async_client() as client:
            await client.delete_object(
                Bucket=self.bucket,
                Key=self._object_key(key),
            )

    @_storage_errors
    async def rmdir_if_empty(self, key: str) -> bool:
        if not normalize_storage_key(key):
            raise ValueError("The storage root cannot be removed")
        prefix = self._object_key(key).rstrip("/") + "/"
        async with self._async_client() as client:
            before = await client.list_objects_v2(Bucket=self.bucket, Prefix=prefix, MaxKeys=2)
            if len(before.get("Contents", [])) > 2:
                raise StorageError("S3 empty-directory listing exceeded its bound")
            if before.get("IsTruncated") or any(item.get("Key") != prefix for item in before.get("Contents", [])):
                return False
            # Only remove the marker. A child arriving after the check is never deleted.
            if before.get("Contents"):
                await client.delete_object(Bucket=self.bucket, Key=prefix)
            after = await client.list_objects_v2(Bucket=self.bucket, Prefix=prefix, MaxKeys=1)
            return not after.get("Contents") and not after.get("IsTruncated")

    @_storage_errors
    async def delete_tree(self, key: str) -> None:
        client = self._client_or_raise()
        prefix = self._object_key(key).rstrip("/") + "/"
        cursor: str | None = None
        while True:
            request: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": 1000}
            if cursor is not None:
                request["ContinuationToken"] = cursor
            response = await asyncio.to_thread(client.list_objects_v2, **request)
            contents = response.get("Contents", [])
            if len(contents) > 1000:
                raise StorageError("S3 listing exceeded requested page size")
            if contents:
                async with self._async_client() as writer:
                    result = await writer.delete_objects(Bucket=self.bucket, Delete={"Objects": [{"Key": item["Key"]} for item in contents]})
                if result.get("Errors"):
                    raise StorageError("Object storage deletion was incomplete")
            if not response.get("IsTruncated"):
                return
            next_cursor = response.get("NextContinuationToken")
            if not next_cursor or next_cursor == cursor:
                raise StorageError("S3 deletion listing omitted a usable continuation token")
            cursor = next_cursor

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

    @_storage_errors
    async def get_version(self, key: str) -> StorageVersion:
        client = self._client_or_raise()
        object_key = self._object_key(key)
        try:
            response = await asyncio.to_thread(
                client.head_object,
                Bucket=self.bucket,
                Key=object_key,
            )
        except Exception as exc:
            if _is_missing_object_error(exc):
                return StorageVersion(key=normalize_storage_key(key), exists=False, is_dir=False)
            raise
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

    @_storage_errors
    async def write_bytes_if_match(
        self,
        key: str,
        data: bytes,
        *,
        condition: WriteCondition | None = None,
        content_type: str | None = None,
    ) -> ConditionalWriteResult:
        if condition is None or (
            not condition.require_absent and condition.version_token is None
        ):
            await self.write_bytes(key, data, content_type=content_type)
            return ConditionalWriteResult(
                ok=True,
                current_version=await self.get_version(key),
            )

        kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": self._object_key(key),
            "Body": data,
            "ContentType": content_type or "application/octet-stream",
        }
        if condition.require_absent:
            if condition.version_token is not None:
                current = await self.get_version(key)
                return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
            kwargs["IfNoneMatch"] = "*"
        else:
            current = await self.get_version(key)
            if not current.exists or current.token != condition.version_token:
                return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
            if not current.etag:
                raise StorageError("S3 conditional write requires an ETag from HEAD")
            kwargs["IfMatch"] = _etag_condition_header(current.etag)

        try:
            async with self._async_client() as client:
                response = await client.put_object(**kwargs)
        except Exception as exc:
            if _is_conditional_conflict(exc):
                return ConditionalWriteResult(ok=False, conflict=True)
            raise
        current_version = _version_from_put_response(key, data, response)
        if current_version is None:
            raise StorageError(
                "S3 conditional write response did not include an ETag or VersionId"
            )
        return ConditionalWriteResult(ok=True, current_version=current_version)

    @_storage_errors
    async def delete_if_match(
        self,
        key: str,
        *,
        condition: WriteCondition | None = None,
    ) -> ConditionalWriteResult:
        if condition is None or (
            not condition.require_absent and condition.version_token is None
        ):
            await self.delete(key)
            return ConditionalWriteResult(
                ok=True,
                current_version=StorageVersion(
                    key=normalize_storage_key(key),
                    exists=False,
                    is_dir=False,
                ),
            )
        current = await self.get_version(key)
        if condition.require_absent:
            if current.exists:
                return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
            return ConditionalWriteResult(ok=True, current_version=current)
        if not current.exists or current.token != condition.version_token:
            return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
        if not current.etag:
            raise StorageError("S3 conditional delete requires an ETag from HEAD")

        try:
            async with self._async_client() as client:
                await client.delete_object(
                    Bucket=self.bucket,
                    Key=self._object_key(key),
                    IfMatch=_etag_condition_header(current.etag),
                )
        except Exception as exc:
            if _is_conditional_conflict(exc):
                return ConditionalWriteResult(ok=False, conflict=True)
            raise
        return ConditionalWriteResult(
            ok=True,
            current_version=StorageVersion(
                key=normalize_storage_key(key),
                exists=False,
                is_dir=False,
            ),
        )

    @_storage_errors
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


def _clean_etag(raw: Any) -> str:
    if raw is None:
        return ""
    text = str(raw)
    return text.strip('"')


def _etag_condition_header(etag: str) -> str:
    return f'"{_clean_etag(etag)}"'


def _version_from_put_response(
    key: str,
    data: bytes,
    response: dict[str, Any],
) -> StorageVersion | None:
    etag = _clean_etag(response.get("ETag"))
    version_id = str(response.get("VersionId") or "")
    if not etag and not version_id:
        return None
    return StorageVersion(
        key=normalize_storage_key(key),
        exists=True,
        is_dir=False,
        size=len(data),
        etag=etag,
        version_id=version_id,
        content_hash=etag,
    )


def _is_missing_object_error(exc: Exception) -> bool:
    status_code, error_code = _s3_error_details(exc)
    missing_codes = {"404", "NoSuchKey", "NotFound"}
    if error_code in missing_codes:
        return True
    return status_code == 404 and not error_code


def _is_conditional_conflict(exc: Exception) -> bool:
    status_code, error_code = _s3_error_details(exc)
    return status_code in {409, 412} or error_code in {
        "409",
        "412",
        "ConditionalRequestConflict",
        "PreconditionFailed",
    }


def _s3_error_details(exc: Exception) -> tuple[int | None, str]:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None, ""
    metadata = response.get("ResponseMetadata")
    raw_status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
    try:
        status_code = int(raw_status) if raw_status is not None else None
    except (TypeError, ValueError):
        status_code = None
    error = response.get("Error")
    error_code = str(error.get("Code") or "") if isinstance(error, dict) else ""
    return status_code, error_code
