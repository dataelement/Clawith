from unittest.mock import Mock

import pytest

from app.services.storage_runtime.s3 import S3StorageBackend


def test_s3_backend_passes_max_pool_connections(monkeypatch):
    config_instances: list[object] = []
    client_calls: list[dict] = []

    class FakeConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            config_instances.append(self)

    fake_boto3 = Mock()
    fake_boto3.client.side_effect = lambda *args, **kwargs: client_calls.append(kwargs) or object()

    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "boto3":
            return fake_boto3
        if name == "botocore.config":
            return type("FakeBotocoreConfigModule", (), {"Config": FakeConfig})()
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    backend = S3StorageBackend(
        bucket="bucket",
        endpoint_url="http://minio:9000",
        access_key_id="key",
        secret_access_key="secret",
        max_pool_connections=64,
    )

    backend._client_or_raise()

    assert len(config_instances) == 1
    assert config_instances[0].kwargs["max_pool_connections"] == 64
    assert len(client_calls) == 1
    assert client_calls[0]["config"] is config_instances[0]


@pytest.mark.asyncio
async def test_s3_list_dir_returns_entries_from_every_page(monkeypatch):
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def list_objects_v2(self, **kwargs):
            self.calls.append(kwargs)
            if "ContinuationToken" not in kwargs:
                return {
                    "CommonPrefixes": [{"Prefix": "workspace/reports/"}],
                    "Contents": [
                        {
                            "Key": "workspace/first.md",
                            "Size": 3,
                            "ETag": '"first"',
                        }
                    ],
                    "IsTruncated": True,
                    "NextContinuationToken": "page-2",
                }
            return {
                "Contents": [
                    {
                        "Key": "workspace/second.md",
                        "Size": 5,
                        "ETag": '"second"',
                    }
                ],
                "IsTruncated": False,
            }

    client = FakeClient()
    backend = S3StorageBackend(bucket="bucket")
    monkeypatch.setattr(backend, "_client_or_raise", lambda: client)

    entries = await backend.list_dir("workspace")

    assert [(entry.name, entry.is_dir, entry.size) for entry in entries] == [
        ("reports", True, 0),
        ("first.md", False, 3),
        ("second.md", False, 5),
    ]
    assert len(client.calls) == 2
    assert client.calls[1]["ContinuationToken"] == "page-2"
