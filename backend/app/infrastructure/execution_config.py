"""Explicit deployment configuration for application-owned execution dependencies."""

import base64
import binascii
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator, model_validator


class _Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True, allow_inf_nan=False)


KeyVersion = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]


class KeyringSettings(_Configuration):
    active_version: KeyVersion
    keys: dict[KeyVersion, SecretStr] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_keys(self) -> Self:
        if self.active_version not in self.keys:
            raise ValueError("The active encryption key version is unavailable")
        self.decoded_keys()
        return self

    def decoded_keys(self) -> dict[str, bytes]:
        """Reveal validated key bytes only to resource construction, never diagnostics."""
        result: dict[str, bytes] = {}
        for version, secret in self.keys.items():
            encoded = secret.get_secret_value()
            if len(encoded) != 44:
                raise ValueError("Encryption keys must be base64-encoded 32-byte values")
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise ValueError("Encryption keys must be base64-encoded 32-byte values") from None
            if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != encoded:
                raise ValueError("Encryption keys must be base64-encoded 32-byte values")
            result[version] = decoded
        return result


class LocalStorageSettings(_Configuration):
    kind: Literal["local"] = "local"
    root: Path

    @field_validator("root")
    @classmethod
    def absolute_root(cls, value: Path) -> Path:
        if not value.is_absolute() or ".." in value.parts or value == Path(value.anchor):
            raise ValueError("Local storage root must be an explicit absolute directory, not a filesystem root")
        return value


class S3StorageSettings(_Configuration):
    kind: Literal["s3"] = "s3"
    bucket: str = Field(min_length=3, max_length=63, pattern=r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$")
    prefix: str = Field(min_length=1, max_length=512)
    endpoint: str | None = Field(default=None, max_length=2048)
    region: str = Field(min_length=1, max_length=128)
    authentication: Literal["static", "ambient"]
    access_key_id: SecretStr | None = None
    secret_access_key: SecretStr | None = None
    lock_database_url: SecretStr
    lock_pool_size: int = Field(strict=True, gt=0, le=100)
    lock_timeout_seconds: float = Field(strict=True, gt=0, le=120)

    @field_validator("prefix")
    @classmethod
    def target_namespace(cls, value: str) -> str:
        if (
            len(value.encode("utf-8")) > 512
            or value.strip() != value
            or "\\" in value
            or any(part in ("", ".", "..") for part in value.split("/"))
        ):
            raise ValueError("S3 prefix must be an explicit canonical target namespace")
        return value

    @field_validator("region")
    @classmethod
    def explicit_region(cls, value: str) -> str:
        if not value.strip() or value.strip() != value:
            raise ValueError("S3 region must be explicit")
        return value

    @field_validator("endpoint")
    @classmethod
    def valid_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = urlsplit(value)
            _ = parsed.port
        except ValueError:
            raise ValueError("S3 endpoint must be an HTTP URL without embedded credentials") from None
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("S3 endpoint must be an HTTP URL without embedded credentials")
        return value

    @field_validator("lock_database_url")
    @classmethod
    def explicit_lock_database(cls, value: SecretStr) -> SecretStr:
        # The composition root applies Settings' existing target PostgreSQL URL validator.
        # Keeping that authority there avoids a Settings -> ExecutionSettings -> Settings cycle.
        if not value.get_secret_value().strip():
            raise ValueError("A dedicated session-pinned lock database URL is required")
        return value

    @model_validator(mode="after")
    def explicit_authentication(self) -> Self:
        supplied = self.access_key_id is not None or self.secret_access_key is not None
        complete = (
            self.access_key_id is not None
            and bool(self.access_key_id.get_secret_value().strip())
            and self.secret_access_key is not None
            and bool(self.secret_access_key.get_secret_value().strip())
        )
        if self.authentication == "static" and not complete:
            raise ValueError("Static S3 authentication requires an explicit access key and secret key")
        if self.authentication == "ambient" and supplied:
            raise ValueError("Ambient S3 authentication cannot include static credentials")
        return self


class HTTPSettings(_Configuration):
    max_connections: int = Field(default=100, strict=True, gt=0, le=4096)
    max_keepalive_connections: int = Field(default=50, strict=True, ge=0, le=4096)

    @model_validator(mode="after")
    def valid_keepalive_capacity(self) -> Self:
        if self.max_keepalive_connections > self.max_connections:
            raise ValueError("HTTP keepalive capacity cannot exceed total connection capacity")
        return self


StorageSettings = Annotated[LocalStorageSettings | S3StorageSettings, Field(discriminator="kind")]


class ExecutionSettings(_Configuration):
    credential_keys: KeyringSettings
    continuation_keys: KeyringSettings
    storage: StorageSettings
    http: HTTPSettings = Field(default_factory=HTTPSettings)
