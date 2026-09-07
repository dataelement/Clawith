import base64

import pytest
from pydantic import ValidationError

from app.infrastructure.execution_config import (
    ExecutionSettings,
    HTTPSettings,
    KeyringSettings,
    LocalStorageSettings,
    S3StorageSettings,
)


def keys():
    return {"active_version": "v1", "keys": {"v1": base64.b64encode(b"a" * 32).decode()}}


def s3(**changes):
    values = {
        "kind": "s3",
        "bucket": "target-bucket",
        "prefix": "target/workspaces",
        "region": "us-east-1",
        "authentication": "static",
        "access_key_id": "static-key",
        "secret_access_key": "static-secret",
        "lock_database_url": "postgresql+asyncpg://target:db-secret@localhost:5432/clawith_target",
        "lock_pool_size": 4,
        "lock_timeout_seconds": 10,
    }
    values.update(changes)
    return values


def test_keyring_decodes_explicit_independent_keys_and_redacts_representation():
    raw = keys()
    config = KeyringSettings.model_validate(raw)
    assert config.decoded_keys() == {"v1": b"a" * 32}
    assert raw["keys"]["v1"] not in repr(config)
    assert raw["keys"]["v1"] not in config.model_dump_json()
    execution = ExecutionSettings.model_validate(
        {
            "credential_keys": raw,
            "continuation_keys": keys(),
            "storage": {"kind": "local", "root": "/tmp/clawith-target"},
        }
    )
    assert isinstance(execution.storage, LocalStorageSettings)
    assert execution.credential_keys is not execution.continuation_keys
    assert execution.http.max_connections == 100


@pytest.mark.parametrize(
    "value", ["", "bad-secret", base64.b64encode(b"a" * 31).decode(), base64.b64encode(b"a" * 33).decode(), "x" * 44]
)
def test_invalid_keys_fail_without_exposing_the_value(value):
    with pytest.raises(ValidationError) as error:
        KeyringSettings.model_validate({"active_version": "v1", "keys": {"v1": value}})
    if value:
        assert value not in str(error.value)


def test_keyring_requires_available_active_version_and_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        KeyringSettings.model_validate({"active_version": "v2", "keys": keys()["keys"]})
    with pytest.raises(ValidationError):
        KeyringSettings.model_validate({**keys(), "generate_default": True})
    with pytest.raises(ValidationError):
        ExecutionSettings.model_validate({"storage": {"kind": "local", "root": "/tmp/target"}})


@pytest.mark.parametrize("root", ["relative", "~/target", "/", "/tmp/../target"])
def test_local_root_must_be_explicit_and_not_broad(root):
    with pytest.raises(ValidationError):
        LocalStorageSettings(root=root)


def test_s3_static_and_ambient_are_explicit_and_do_not_expose_credentials():
    config = S3StorageSettings.model_validate(s3())
    for secret in ("static-key", "static-secret", "db-secret"):
        assert secret not in repr(config)
        assert secret not in config.model_dump_json()
    assert config.lock_pool_size == 4
    ambient = S3StorageSettings.model_validate(s3(authentication="ambient", access_key_id=None, secret_access_key=None))
    assert ambient.authentication == "ambient"
    assert ambient.endpoint is None


@pytest.mark.parametrize(
    "changes",
    [
        {"authentication": "static", "secret_access_key": None},
        {"authentication": "static", "access_key_id": ""},
        {"authentication": "ambient"},
        {"authentication": "automatic"},
        {"prefix": ""},
        {"prefix": "/"},
        {"prefix": "target/../legacy"},
        {"prefix": "target//files"},
        {"endpoint": "ftp://s3.test"},
        {"endpoint": "https://user:secret@s3.test"},
        {"endpoint": "https://s3.test?token=secret"},
        {"endpoint": "https://[bad"},
        {"region": " "},
        {"lock_database_url": " "},
        {"lock_pool_size": 0},
        {"lock_pool_size": True},
        {"lock_timeout_seconds": 0},
        {"lock_timeout_seconds": float("inf")},
    ],
)
def test_s3_rejects_incomplete_authentication_namespace_url_and_pool_values(changes):
    with pytest.raises(ValidationError):
        S3StorageSettings.model_validate(s3(**changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"max_connections": 0},
        {"max_connections": True},
        {"max_connections": 1, "max_keepalive_connections": 2},
        {"max_keepalive_connections": -1},
        {"new_policy": True},
    ],
)
def test_http_bounds_are_explicit_and_validated(changes):
    with pytest.raises(ValidationError):
        HTTPSettings.model_validate(changes)


def test_storage_discriminator_and_required_authentication():
    for storage in ({"root": "/tmp/target"}, {"kind": "legacy", "root": "/tmp/target"}):
        with pytest.raises(ValidationError):
            ExecutionSettings.model_validate(
                {"credential_keys": keys(), "continuation_keys": keys(), "storage": storage}
            )
    values = s3()
    del values["authentication"]
    with pytest.raises(ValidationError):
        S3StorageSettings.model_validate(values)
@pytest.mark.parametrize("field", ["timeout_seconds", "pool_timeout_seconds"])
def test_http_configuration_rejects_unimplemented_deadline_options(field):
    with pytest.raises(ValidationError, match="Extra inputs"):
        HTTPSettings.model_validate({field: 5})
