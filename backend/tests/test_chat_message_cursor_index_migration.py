"""Static contracts for the composite group-history cursor index."""

from importlib import util
from pathlib import Path

from app.models.audit import ChatMessage


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "202607141530_add_chat_message_cursor_index.py"
)


def _load_migration():
    spec = util.spec_from_file_location("add_chat_message_cursor_index", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cursor_index_follows_current_head_without_name_collision() -> None:
    migration = _load_migration()
    existing_indexes = {
        index.name: tuple(index.columns.keys())
        for index in ChatMessage.__table__.indexes
    }

    assert migration.revision == "add_chat_message_cursor_index"
    assert migration.down_revision == "create_channel_delivery_outbox"
    assert existing_indexes["ix_chat_messages_conversation_id"] == (
        "conversation_id",
    )
    assert existing_indexes["ix_chat_messages_created_at"] == ("created_at",)
    assert migration.INDEX_NAME not in existing_indexes


def test_cursor_index_upgrade_and_downgrade_are_symmetric(monkeypatch) -> None:
    migration = _load_migration()
    created = []
    dropped = []
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda *args, **kwargs: created.append((args, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_index",
        lambda *args, **kwargs: dropped.append((args, kwargs)),
    )

    migration.upgrade()
    migration.downgrade()

    assert created == [
        (
            (
                migration.INDEX_NAME,
                "chat_messages",
                ["conversation_id", "created_at", "id"],
            ),
            {"unique": False},
        )
    ]
    assert dropped == [
        ((migration.INDEX_NAME,), {"table_name": "chat_messages"})
    ]
