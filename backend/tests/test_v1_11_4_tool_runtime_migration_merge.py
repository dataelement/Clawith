"""Release migration topology for the Tool Runtime integration."""

from __future__ import annotations

import importlib.util
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "v1_11_4_f063_merge_tool_runtime_heads.py"
)


def test_release_merge_revision_joins_both_migration_heads() -> None:
    spec = importlib.util.spec_from_file_location(
        "v1_11_4_tool_runtime_migration_merge",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.revision == "f063_merge_v1_11_4_heads"
    assert set(migration.down_revision) == {
        "f061_default_tenant_timezone",
        "f062_tool_execution_identity",
    }
