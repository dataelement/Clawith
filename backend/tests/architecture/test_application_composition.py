from __future__ import annotations

import ast
import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app import application
from app.infrastructure import config, database
from app.infrastructure.config import (
    DATABASE_IDENTITY_QUERY_KEYS,
    TARGET_DATABASE_NAME,
    Settings,
    reveal_database_url,
)
from app.infrastructure.database import Base, DatabaseResources
from app.main import app as asgi_app

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
COMPLETE_DATABASE_URL = "postgresql+asyncpg://clawith:secret@localhost:5432/clawith_target"


@dataclass
class FakeEngine:
    dispose_error: Exception | None = None
    dispose_calls: int = 0

    async def dispose(self) -> None:
        self.dispose_calls += 1
        if self.dispose_error is not None:
            raise self.dispose_error


@dataclass
class FakeDatabaseResources:
    close_calls: int = 0
    execution_sessions: async_sessionmaker[AsyncSession] = field(default_factory=async_sessionmaker)

    async def aclose(self) -> None:
        self.close_calls += 1


class RouteWithPath(Protocol):
    path: str


class SettingsFactory(Protocol):
    def __call__(self, **values: object) -> Settings: ...


settings_factory = cast(SettingsFactory, Settings)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "APP_VERSION": "test-version",
        "DATABASE_URL": COMPLETE_DATABASE_URL,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def test_main_exposes_only_the_minimal_health_route(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = FakeDatabaseResources()
    async def create_resources(_settings: Settings) -> DatabaseResources:
        return cast(DatabaseResources, resources)

    monkeypatch.setattr(database, "create_database_resources", create_resources)

    with TestClient(asgi_app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == asgi_app.version
    assert body["process_pid"] == os.getpid()
    assert len(body["startup_id"]) == 32
    assert set(body["startup_id"]) <= set("0123456789abcdef")
    route_paths = [cast(RouteWithPath, route).path for route in asgi_app.routes]
    assert {path for path in route_paths if path.startswith("/api/")} == {
        "/api/health"
    }
    assert resources.close_calls == 1


def test_create_app_owns_database_resources_for_its_complete_lifespan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = FakeDatabaseResources()
    observed_settings: list[Settings] = []

    async def create_resources(settings: Settings) -> DatabaseResources:
        observed_settings.append(settings)
        return cast(DatabaseResources, resources)

    monkeypatch.setattr(database, "create_database_resources", create_resources)
    settings = _settings()
    app = application.create_app(settings)

    assert resources.close_calls == 0
    with TestClient(app) as client:
        assert cast(FastAPI, client.app).state.database is resources
        assert client.get("/api/health").json() == {
            "status": "ok",
            "version": "test-version",
            "process_pid": os.getpid(),
            "startup_id": settings.STARTUP_INSTANCE_ID,
        }
        assert resources.close_calls == 0

    assert observed_settings == [settings]
    assert resources.close_calls == 1
    assert not hasattr(app.state, "database")
    assert not hasattr(app.state, "audit")


@pytest.mark.asyncio
async def test_audit_consumer_stops_before_database_disposal(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = FakeDatabaseResources()
    observed: list[asyncio.Task[object]] = []

    async def create_resources(_settings: Settings) -> DatabaseResources:
        return cast(DatabaseResources, resources)

    async def close_resources() -> None:
        assert observed and all(task.done() for task in observed)
        resources.close_calls += 1

    monkeypatch.setattr(database, "create_database_resources", create_resources)
    monkeypatch.setattr(resources, "aclose", close_resources)
    app = application.create_app(_settings())
    with pytest.raises(ValueError, match="application failure"):
        async with app.router.lifespan_context(app):
            observed.extend(task for task in asyncio.all_tasks() if task.get_name() == "audit-observation-consumer")
            assert len(observed) == 1
            assert app.state.audit.statistics.persisted == 0
            raise ValueError("application failure")
    assert resources.close_calls == 1
    assert not hasattr(app.state, "audit")
    assert not hasattr(app.state, "database")


def test_database_disposed_if_audit_initialization_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = FakeDatabaseResources()

    async def create_resources(_settings: Settings) -> DatabaseResources:
        return cast(DatabaseResources, resources)

    def invalid_sink(*args: object, **kwargs: object) -> None:
        raise ValueError("invalid Audit configuration")

    monkeypatch.setattr(database, "create_database_resources", create_resources)
    monkeypatch.setattr(application, "AsyncAuditSink", invalid_sink)
    app = application.create_app(_settings())
    with pytest.raises(ValueError, match="invalid Audit configuration"), TestClient(app):
        pass
    assert resources.close_calls == 1
    assert not hasattr(app.state, "database")
    assert not hasattr(app.state, "audit")


@pytest.mark.parametrize(
    "database_url",
    [
        "not-a-url",
        "sqlite+aiosqlite:///target.db",
        "postgresql+asyncpg://:secret@localhost:5432/clawith_target",
        "postgresql+asyncpg://clawith@localhost:5432/clawith_target",
        "postgresql+asyncpg://clawith:secret@:5432/clawith_target",
        "postgresql+asyncpg://clawith:secret@localhost/clawith_target",
        "postgresql+asyncpg://clawith:secret@localhost:5432",
        "postgresql+asyncpg://clawith:secret@localhost:65536/clawith_target",
    ],
)
def test_target_configuration_rejects_incomplete_database_urls(database_url: str) -> None:
    with pytest.raises(ValidationError):
        _settings(DATABASE_URL=database_url)


@pytest.mark.parametrize("database_name", ["clawith", "postgres", "clawith_shadow"])
def test_target_configuration_rejects_non_target_database_names(
    database_name: str,
) -> None:
    password = "database-name-secret"
    database_url = (
        f"postgresql+asyncpg://clawith:{password}@localhost:5432/{database_name}"
    )

    with pytest.raises(ValidationError, match=TARGET_DATABASE_NAME) as captured:
        _settings(DATABASE_URL=database_url)

    diagnostic = f"{captured.value!s}\n{captured.value!r}"
    assert password not in diagnostic
    assert database_url not in diagnostic


@pytest.mark.parametrize("query_key", sorted(DATABASE_IDENTITY_QUERY_KEYS))
def test_target_configuration_rejects_database_identity_query_overrides(
    query_key: str,
) -> None:
    password = "query-override-secret"
    database_url = (
        f"postgresql+asyncpg://clawith:{password}@localhost:5432/clawith_target"
        f"?{query_key}=clawith"
    )

    with pytest.raises(ValidationError, match="connection identity") as captured:
        _settings(DATABASE_URL=database_url)

    diagnostic = f"{captured.value!s}\n{captured.value!r}"
    assert password not in diagnostic
    assert database_url not in diagnostic


def test_os_environment_database_name_is_validated_before_app_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "os-environment-secret"
    monkeypatch.setenv(
        "DATABASE_URL",
        f"postgresql+asyncpg://clawith:{password}@localhost:5432/clawith",
    )
    config.get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match=TARGET_DATABASE_NAME) as captured:
            application.create_app()
    finally:
        config.get_settings.cache_clear()

    assert password not in str(captured.value)


def test_os_environment_query_override_is_rejected_before_app_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "os-query-secret"
    monkeypatch.setenv(
        "DATABASE_URL",
        f"postgresql+asyncpg://clawith:{password}@localhost:5432/"
        "clawith_target?database=clawith",
    )
    config.get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="connection identity") as captured:
            application.create_app()
    finally:
        config.get_settings.cache_clear()

    assert password not in str(captured.value)


def test_dotenv_database_name_is_validated_without_secret_disclosure(
    tmp_path: Path,
) -> None:
    password = "dotenv-database-secret"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL="
        f"postgresql+asyncpg://clawith:{password}@localhost:5432/clawith\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match=TARGET_DATABASE_NAME) as captured:
        settings_factory(_env_file=env_file, APP_VERSION="test-version")

    assert password not in str(captured.value)


def test_dotenv_query_override_is_rejected_without_secret_disclosure(
    tmp_path: Path,
) -> None:
    password = "dotenv-query-secret"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL="
        f"postgresql+asyncpg://clawith:{password}@localhost:5432/"
        "clawith_target?database=clawith\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="connection identity") as captured:
        settings_factory(_env_file=env_file, APP_VERSION="test-version")

    assert password not in str(captured.value)


@pytest.mark.parametrize(
    ("password", "database_url"),
    [
        (
            "alpha-secret-value",
            "postgresql+asyncpg://clawith:alpha-secret-value@localhost:65536/clawith_target",
        ),
        (
            "bravo-secret-value",
            "mysql+asyncmy://clawith:bravo-secret-value@localhost:3306/clawith_target",
        ),
        (
            "charlie-secret-value",
            "postgresql+asyncpg://clawith:charlie-secret-value@localhost:5432",
        ),
    ],
)
def test_invalid_database_url_errors_never_expose_password(
    password: str,
    database_url: str,
) -> None:
    with pytest.raises(ValidationError) as captured:
        _settings(DATABASE_URL=database_url)

    rendered_errors = (
        str(captured.value),
        repr(captured.value),
    )
    assert all(password not in rendered for rendered in rendered_errors)


@pytest.mark.parametrize("password", ["alpha-valid-secret", "bravo-valid-secret"])
def test_database_url_is_masked_in_settings_representations_and_dumps(password: str) -> None:
    database_url = f"postgresql+asyncpg://clawith:{password}@localhost:5432/clawith_target"
    settings = _settings(DATABASE_URL=database_url)

    rendered_settings = (
        str(settings),
        repr(settings),
        str(settings.model_dump()),
        repr(settings.model_dump()),
        settings.model_dump_json(),
    )
    assert all(password not in rendered for rendered in rendered_settings)
    revealed_url = reveal_database_url(settings.DATABASE_URL)
    assert isinstance(revealed_url, URL)
    assert str(revealed_url) == database_url.replace(password, "***")
    assert revealed_url == make_url(database_url)


def test_target_configuration_uses_role_isolated_20_connection_pools() -> None:
    settings = _settings()

    assert settings.CONTROL_DATABASE_POOL_SIZE == 20
    assert settings.EXECUTION_DATABASE_POOL_SIZE == 20
    assert settings.DATABASE_POOL_MAX_OVERFLOW == 0


def test_target_configuration_rejects_unknown_dotenv_fields(tmp_path: Path) -> None:
    assert Settings.model_config.get("env_file") == config.ENV_FILE_PATH
    env_file = tmp_path / ".env"
    env_file.write_text("UNKNOWN_TARGET_SETTING=unowned\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="UNKNOWN_TARGET_SETTING"):
        settings_factory(_env_file=env_file, APP_VERSION="test-version")


def test_target_configuration_does_not_silently_replace_a_missing_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "VERSION_PATH", tmp_path / "missing-version")

    with pytest.raises(FileNotFoundError):
        settings_factory(_env_file=None)


@pytest.mark.asyncio
async def test_database_resources_create_and_dispose_both_role_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engines: list[FakeEngine] = []
    engine_calls: list[tuple[str, bool, int, int]] = []

    def create_engine(
        database_url: URL,
        *,
        echo: bool,
        pool_size: int,
        max_overflow: int,
    ) -> AsyncEngine:
        engine_calls.append(
            (
                database_url.render_as_string(hide_password=False),
                echo,
                pool_size,
                max_overflow,
            )
        )
        engine = FakeEngine()
        engines.append(engine)
        return cast(AsyncEngine, engine)

    monkeypatch.setattr(database, "create_async_engine", create_engine)

    resources = await database.create_database_resources(_settings())
    await resources.aclose()

    assert engine_calls == [
        (COMPLETE_DATABASE_URL, False, 20, 0),
        (COMPLETE_DATABASE_URL, False, 20, 0),
    ]
    assert [engine.dispose_calls for engine in engines] == [1, 1]
    assert resources.control_sessions.kw["bind"] is resources.control_engine
    assert resources.execution_sessions.kw["bind"] is resources.execution_engine


@pytest.mark.asyncio
async def test_database_resources_dispose_execution_pool_when_control_disposal_fails() -> None:
    control = FakeEngine(dispose_error=RuntimeError("control dispose failed"))
    execution = FakeEngine()
    resources = DatabaseResources(
        control_engine=cast(AsyncEngine, control),
        execution_engine=cast(AsyncEngine, execution),
        control_sessions=database.create_session_factory(cast(AsyncEngine, control)),
        execution_sessions=database.create_session_factory(cast(AsyncEngine, execution)),
    )

    with pytest.raises(RuntimeError, match="control dispose failed"):
        await resources.aclose()

    assert control.dispose_calls == 1
    assert execution.dispose_calls == 1


@pytest.mark.asyncio
async def test_database_resources_dispose_control_pool_when_execution_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = FakeEngine()
    calls = 0

    def create_engine(
        _database_url: URL,
        *,
        echo: bool,
        pool_size: int,
        max_overflow: int,
    ) -> AsyncEngine:
        nonlocal calls
        del echo, pool_size, max_overflow
        calls += 1
        if calls == 2:
            raise RuntimeError("execution engine creation failed")
        return cast(AsyncEngine, control)

    monkeypatch.setattr(database, "_create_role_engine", create_engine)

    with pytest.raises(RuntimeError, match="execution engine creation failed"):
        await database.create_database_resources(_settings())

    assert control.dispose_calls == 1


def test_target_has_one_application_factory_and_metadata_registry() -> None:
    fastapi_calls: list[Path] = []
    declarative_bases: list[Path] = []
    for path in APP_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _qualified_name(node.func) == "FastAPI":
                fastapi_calls.append(path)
            if isinstance(node, ast.ClassDef) and any(
                (_qualified_name(base) or "").endswith("DeclarativeBase") for base in node.bases
            ):
                declarative_bases.append(path)

    assert fastapi_calls == [APP_ROOT / "application.py"]
    assert declarative_bases == [APP_ROOT / "infrastructure/database.py"]
    assert issubclass(Base, DeclarativeBase)
    assert Base.metadata is not None


def test_legacy_composition_entries_are_removed() -> None:
    assert not (APP_ROOT / "config.py").exists()
    assert not (APP_ROOT / "database.py").exists()
