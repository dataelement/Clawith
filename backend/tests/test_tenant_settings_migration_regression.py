import importlib.util
from pathlib import Path
from types import SimpleNamespace


def test_tenant_settings_table_has_forward_migration():
    repo_root = Path(__file__).resolve().parents[2]
    versions_dir = repo_root / "backend" / "alembic" / "versions"
    migration_path = versions_dir / "add_tenant_settings.py"

    assert migration_path.exists(), "Expected a tenant_settings migration file for legacy installs"

    spec = importlib.util.spec_from_file_location("tenant_settings_migration", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "add_tenant_settings"
    assert module.down_revision == "add_notifications_table"

    class _Dialect:
        def __init__(self, exists: bool):
            self._exists = exists

        def has_table(self, _conn, table_name: str) -> bool:
            assert table_name == "tenant_settings"
            return self._exists

    class _Conn:
        def __init__(self, exists: bool):
            self.dialect = _Dialect(exists)

    create_calls = []
    setattr(
        module,
        "op",
        SimpleNamespace(
            get_bind=lambda: _Conn(False),
            create_table=lambda *args, **kwargs: create_calls.append((args, kwargs)),
        ),
    )
    module.upgrade()
    assert [call[0][0] for call in create_calls] == ["tenant_settings"]

    create_calls.clear()
    setattr(
        module,
        "op",
        SimpleNamespace(
            get_bind=lambda: _Conn(True),
            create_table=lambda *args, **kwargs: create_calls.append((args, kwargs)),
        ),
    )
    module.upgrade()
    assert create_calls == []

    drop_calls = []
    setattr(
        module,
        "op",
        SimpleNamespace(
            drop_table=lambda *args, **kwargs: drop_calls.append((args, kwargs)),
        ),
    )
    assert module.downgrade() is None
    assert drop_calls == []
