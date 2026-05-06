"""S3-compatible object storage backend."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from app.services.storage_runtime.base import StorageBackend, StorageEntry
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
    ):
        self.bucket = bucket
        self.prefix = normalize_storage_key(prefix)
        self.region = region
        self.endpoint_url = endpoint_url or None
        self.access_key_id = access_key_id or None
        self.secret_access_key = secret_access_key or None
        self.presign_ttl_seconds = presign_ttl_seconds
        self._client: Any | None = None

    def _object_key(self, key: str) -> str:
        normalized = normalize_storage_key(key)
        return f"{self.prefix}/{normalized}" if self.prefix else normalized

    def _client_or_raise(self):
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError("boto3 is required for S3 storage backend") from exc
            self._client = boto3.client(
                "s3",
                region_name=self.region or None,
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
            )
        return self._client

    async def exists(self, key: str) -> bool:
        try:
            await self.stat(key)
            return True
        except FileNotFoundError:
            return False

    async def is_file(self, key: str) -> bool:
        return await self.exists(key)

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
        client = self._client_or_raise()
        kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": self._object_key(key),
            "Body": data,
        }
        if content_type:
            kwargs["ContentType"] = content_type
        await asyncio.to_thread(client.put_object, **kwargs)

    async def delete(self, key: str) -> None:
        client = self._client_or_raise()
        await asyncio.to_thread(
            client.delete_object,
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
        await asyncio.to_thread(
            client.delete_objects,
            Bucket=self.bucket,
            Delete={"Objects": objects},
        )

    async def stat(self, key: str) -> StorageEntry:
        client = self._client_or_raise()
        try:
            response = await asyncio.to_thread(
                client.head_object,
                Bucket=self.bucket,
                Key=self._object_key(key),
            )
        except Exception as exc:
            raise FileNotFoundError(key) from exc
        return StorageEntry(
            name=normalize_storage_key(key).split("/")[-1],
            key=normalize_storage_key(key),
            is_dir=False,
            size=int(response.get("ContentLength", 0)),
            modified_at=str(response.get("LastModified") or ""),
        )

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
        return await asyncio.to_thread(
            client.generate_presigned_url,
            "get_object",
            Params=params,
            ExpiresIn=self.presign_ttl_seconds,
        )


def _strip_prefix(raw_key: str, prefix: str) -> str:
    if prefix and raw_key.startswith(prefix + "/"):
        return raw_key[len(prefix) + 1:]
    return raw_key
