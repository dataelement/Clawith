"""Static guards that keep production schema changes behind Alembic."""

from pathlib import Path

from app.config import Settings


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_database_auto_create_is_disabled_by_default():
    assert Settings.model_fields["DATABASE_AUTO_CREATE_TABLES"].default is False


def test_create_all_calls_are_guarded_by_the_explicit_legacy_setting():
    main_source = (BACKEND_ROOT / "app/main.py").read_text(encoding="utf-8")
    main_guard = main_source.index("if settings.DATABASE_AUTO_CREATE_TABLES:")
    assert main_guard < main_source.index("Base.metadata.create_all", main_guard)

    bootstrap_source = (BACKEND_ROOT / "app/scripts/bootstrap_db.py").read_text(encoding="utf-8")
    bootstrap_guard = bootstrap_source.index("if not settings.DATABASE_AUTO_CREATE_TABLES:")
    bootstrap_return = bootstrap_source.index("return", bootstrap_guard)
    create_all_position = bootstrap_source.index("Base.metadata.create_all", bootstrap_guard)
    patches_position = bootstrap_source.index("for sql in PATCHES:", bootstrap_guard)
    assert bootstrap_guard < bootstrap_return < create_all_position < patches_position


def test_alembic_and_legacy_bootstrap_register_historical_baseline_models():
    env_source = (BACKEND_ROOT / "alembic/env.py").read_text(encoding="utf-8")
    bootstrap_source = (BACKEND_ROOT / "app/scripts/bootstrap_db.py").read_text(
        encoding="utf-8"
    )

    model_modules = (
        "gateway_message",
        "notification",
        "tenant_setting",
        "trigger_execution",
    )
    for module_name in model_modules:
        assert f"app.models.{module_name}" in env_source
        assert f"app.models.{module_name}" in bootstrap_source


def test_official_startup_paths_bootstrap_checkpoints_after_alembic():
    entrypoint_source = (BACKEND_ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    restart_source = (BACKEND_ROOT.parent / "restart.sh").read_text(encoding="utf-8")
    checkpoint_command = "python -m app.scripts.setup_langgraph_checkpoints"

    assert entrypoint_source.index("alembic upgrade head") < entrypoint_source.index(
        checkpoint_command
    ) < entrypoint_source.index('exec /bin/bash -lc "$START_COMMAND"')
    assert restart_source.index(".venv/bin/alembic upgrade head") < restart_source.index(
        f".venv/bin/{checkpoint_command}"
    ) < restart_source.index(".venv/bin/uvicorn app.main:app")
    assert ".venv/bin/alembic upgrade head 2>/dev/null || true" not in restart_source
    assert f".venv/bin/{checkpoint_command} || true" not in restart_source
    runtime_command = restart_source.index(".venv/bin/uvicorn app.main:app")
    for fixed_runtime_setting in (
        "AGENT_RUNTIME_V2_ENABLED=true",
        "AGENT_RUNTIME_V2_AGENT_IDS=",
        "AGENT_RUNTIME_V2_SOURCE_TYPES=",
    ):
        assert restart_source.index(fixed_runtime_setting) < runtime_command
