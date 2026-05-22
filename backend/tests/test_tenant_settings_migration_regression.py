from pathlib import Path


def test_tenant_settings_table_has_forward_migration():
    repo_root = Path(__file__).resolve().parents[1]
    migration_text = (
        repo_root / "alembic" / "versions" / "add_tenant_settings.py"
    ).read_text(encoding="utf-8")

    expected_snippets = [
        'if not conn.dialect.has_table(conn, "tenant_settings")',
        '"tenant_settings"',
        'sa.ForeignKey("tenants.id", ondelete="CASCADE")',
        'postgresql.JSONB',
    ]

    missing = [snippet for snippet in expected_snippets if snippet not in migration_text]
    assert missing == []
