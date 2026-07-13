"""Pure configuration tests for LangGraph PostgreSQL checkpoint wiring."""

from unittest.mock import AsyncMock, patch
import uuid

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
import pytest
from psycopg.conninfo import conninfo_to_dict

from app.config import Settings
from app.services.agent_runtime.checkpointer import (
    CheckpointerConfigurationError,
    checkpoint_database_url,
    checkpoint_serializer,
    create_checkpointer,
    runtime_thread_config,
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "DATABASE_URL": "postgresql+asyncpg://app:secret@db.example/clawith",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_runtime_thread_id_is_exactly_the_run_id() -> None:
    run_id = uuid.uuid4()

    assert runtime_thread_config(run_id) == {"configurable": {"thread_id": str(run_id)}}


def test_dedicated_checkpoint_url_wins_and_is_normalized_for_psycopg() -> None:
    settings = _settings(
        LANGGRAPH_CHECKPOINT_DATABASE_URL=("postgresql+psycopg://checkpoint:secret@db.example/checkpoints")
    )

    assert checkpoint_database_url(settings) == (
        "postgresql://checkpoint:secret@db.example/checkpoints?options=-csearch_path%3Dlanggraph_checkpoint"
    )


def test_primary_asyncpg_url_is_the_checkpoint_fallback() -> None:
    assert checkpoint_database_url(_settings()) == (
        "postgresql://app:secret@db.example/clawith?options=-csearch_path%3Dlanggraph_checkpoint"
    )


def test_checkpoint_url_preserves_existing_options_and_forces_isolated_schema() -> None:
    settings = _settings(
        LANGGRAPH_CHECKPOINT_DATABASE_URL=(
            "postgresql://checkpoint:secret@db.example/checkpoints?sslmode=require&options=-cstatement_timeout%3D5000"
        )
    )

    assert checkpoint_database_url(settings) == (
        "postgresql://checkpoint:secret@db.example/checkpoints?sslmode=require&"
        "options=-cstatement_timeout%3D5000%20-csearch_path%3Dlanggraph_checkpoint"
    )


def test_psycopg_parses_search_path_as_a_separate_server_option() -> None:
    settings = _settings(
        LANGGRAPH_CHECKPOINT_DATABASE_URL=(
            "postgresql://checkpoint:secret@db.example/checkpoints?options=-cstatement_timeout%3D5000"
        )
    )

    parsed = conninfo_to_dict(checkpoint_database_url(settings))

    assert parsed["options"] == ("-cstatement_timeout=5000 -csearch_path=langgraph_checkpoint")


def test_installed_saver_uses_unqualified_checkpoint_tables() -> None:
    migration_sql = "\n".join(AsyncPostgresSaver.MIGRATIONS)

    assert "CREATE TABLE IF NOT EXISTS checkpoint_migrations" in migration_sql
    assert "CREATE TABLE IF NOT EXISTS checkpoints" in migration_sql
    assert "CREATE TABLE IF NOT EXISTS checkpoint_blobs" in migration_sql
    assert "CREATE TABLE IF NOT EXISTS checkpoint_writes" in migration_sql
    assert "langgraph_checkpoint." not in migration_sql


@pytest.mark.parametrize("database_url", ["sqlite:///tmp.db", "", "not-a-url"])
def test_non_postgres_or_invalid_checkpoint_url_fails_closed(
    database_url: str,
) -> None:
    with pytest.raises(CheckpointerConfigurationError):
        checkpoint_database_url(_settings(DATABASE_URL=database_url))


def test_aes_serializer_round_trips_checkpoint_values() -> None:
    serializer = checkpoint_serializer(_settings(LANGGRAPH_AES_KEY="k" * 32))

    assert serializer is not None
    encoded = serializer.dumps_typed({"secret": "checkpoint-value"})

    assert b"checkpoint-value" not in encoded[1]
    assert serializer.loads_typed(encoded) == {"secret": "checkpoint-value"}


def test_aes_key_length_is_validated_as_encoded_bytes() -> None:
    with pytest.raises(CheckpointerConfigurationError, match="16, 24, or 32 bytes"):
        checkpoint_serializer(_settings(LANGGRAPH_AES_KEY="too-short"))


@pytest.mark.asyncio
async def test_factory_is_lazy_and_never_runs_checkpointer_setup() -> None:
    saver = AsyncMock()

    class FakeManager:
        async def __aenter__(self) -> AsyncMock:
            return saver

        async def __aexit__(self, *args: object) -> None:
            return None

    manager = FakeManager()
    with patch(
        "app.services.agent_runtime.checkpointer.AsyncPostgresSaver.from_conn_string",
        return_value=manager,
    ) as factory:
        created = create_checkpointer(_settings())
        async with created as yielded:
            assert yielded is saver

    factory.assert_called_once_with(
        "postgresql://app:secret@db.example/clawith?options=-csearch_path%3Dlanggraph_checkpoint",
        serde=None,
    )
    saver.setup.assert_not_awaited()
