"""Clawith Backend — FastAPI Application Entry Point."""

from contextlib import asynccontextmanager
from pathlib import Path
import shutil

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.config import get_settings
from app.core.events import close_redis
from app.core.logging_config import configure_logging, intercept_standard_logging
from app.core.middleware import TraceIdMiddleware
from app.schemas.schemas import HealthResponse

settings = get_settings()


def _log_bwrap_startup_status() -> None:
    """Emit a startup diagnostic for bubblewrap availability.

    We only warn when bwrap is missing so deployments can still start in
    degraded mode. The subprocess sandbox will fall back to the hardened local
    execution path in that case.
    """
    in_container = Path("/.dockerenv").exists()
    bwrap_path = shutil.which("bwrap")

    if bwrap_path:
        location = "container" if in_container else "host"
        logger.info(f"[startup] bubblewrap detected at {bwrap_path} ({location})")
        return

    if in_container:
        logger.warning(
            "[startup] bubblewrap (bwrap) is not installed in the backend container. "
            "The service will still start, but execute_code will run without bwrap filesystem isolation."
        )
        return

    logger.warning(
        "[startup] bubblewrap (bwrap) is not installed on the host. "
        "The service will still start, but execute_code will run without bwrap filesystem isolation."
    )


async def _start_ss_local() -> None:
    """Start ss-local SOCKS5 proxy for Discord API calls. Tries nodes in priority order."""
    import asyncio, json, os, shutil, tempfile
    if not shutil.which("ss-local"):
        logger.info("[Proxy] ss-local not found — Discord proxy disabled")
        return
    # Load proxy nodes from config file (gitignored, mounted as Docker volume)
    import json as _json
    cfg_file = os.environ.get("SS_CONFIG_FILE", "/data/ss-nodes.json")
    if os.path.exists(cfg_file):
        # Guard against empty or malformed config file — both produce a clear
        # warning and a clean exit rather than an unhandled JSONDecodeError.
        try:
            raw = open(cfg_file).read().strip()
            if not raw:
                logger.warning(f"[Proxy] {cfg_file} exists but is empty — skipping proxy")
                return
            nodes = _json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(f"[Proxy] Failed to parse {cfg_file}: {exc} — skipping proxy")
            return
        logger.info(f"[Proxy] Loaded {len(nodes)} node(s) from {cfg_file}")
    elif os.environ.get("SS_SERVER") and os.environ.get("SS_PASSWORD"):
        nodes = [{"server": os.environ["SS_SERVER"], "port": int(os.environ.get("SS_PORT", "1080")),
                  "password": os.environ["SS_PASSWORD"], "method": os.environ.get("SS_METHOD", "chacha20-ietf-poly1305"), "label": "env"}]
    else:
        logger.info(f"[Proxy] {cfg_file} not found and SS_SERVER not set — skipping proxy")
        return
    for node in nodes:
        cfg = {"server": node["server"], "server_port": node["port"], "local_address": "127.0.0.1",
               "local_port": 1080, "password": node["password"], "method": node["method"], "timeout": 10}
        tf = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        json.dump(cfg, tf); tf.close()
        try:
            proc = await asyncio.create_subprocess_exec(
                "ss-local", "-c", tf.name,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            await asyncio.sleep(2)
            if proc.returncode is None:
                os.environ["DISCORD_PROXY"] = "socks5h://127.0.0.1:1080"
                logger.info(f"[Proxy] ss-local → {node['label']} ({node['server']}:{node['port']})")
                return
            err = (await proc.stderr.read()).decode()[:120]
            logger.warning(f"[Proxy] {node['label']} failed: {err}")
        except Exception as e:
            logger.error(f"[Proxy] {node['label']} error: {e}")
    logger.warning("[Proxy] All SS nodes failed — Discord API calls will run without proxy")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    # Configure logging first
    configure_logging()
    intercept_standard_logging()
    logger.info("[startup] Logging configured")
    _log_bwrap_startup_status()

    # Warn about default JWT secrets in production
    if "change-me" in settings.SECRET_KEY.lower() or "change-me" in settings.JWT_SECRET_KEY.lower():
        logger.warning(
            "[startup] WARNING: SECRET_KEY or JWT_SECRET_KEY contains default 'change-me' value. "
            "This is insecure for production. Set unique secrets in your .env file."
        )

    import asyncio
    import sys
    import os
    from app.services.trigger_daemon import start_trigger_daemon
    from app.services.tool_seeder import seed_builtin_tools
    from app.services.template_seeder import seed_agent_templates
    from app.services.feishu_ws import feishu_ws_manager
    from app.services.dingtalk_stream import dingtalk_stream_manager
    from app.services.wecom_stream import wecom_stream_manager
    from app.services.wechat_channel import wechat_poll_manager
    from app.services.discord_gateway import discord_gateway_manager

    # Startup: seed data — each step isolated so one failure doesn't block others
    logger.info("[startup] seeding...")

    # Seed default company (Tenant) — required before users can register
    try:
        from app.models.tenant import Tenant
        from app.database import async_session as _session
        from sqlalchemy import select as _select
        async with _session() as _db:
            _existing = await _db.execute(_select(Tenant).where(Tenant.slug == "default"))
            if not _existing.scalar_one_or_none():
                _db.add(Tenant(name="Default", slug="default", im_provider="web_only"))
                await _db.commit()
                logger.info("[startup] Default company created")
    except Exception as e:
        logger.warning(f"[startup] Default company seed failed: {e}")

    # Migrate old shared enterprise_info/ → enterprise_info_{first_tenant_id}/
    try:
        import shutil
        from pathlib import Path as _Path
        from app.config import get_settings as _gs
        from app.models.tenant import Tenant as _T
        from app.database import async_session as _ses
        from sqlalchemy import select as _sel
        _data_dir = _Path(_gs().AGENT_DATA_DIR)
        _old_dir = _data_dir / "enterprise_info"
        if _old_dir.exists() and any(_old_dir.iterdir()):
            async with _ses() as _db:
                _first = await _db.execute(_sel(_T).order_by(_T.created_at).limit(1))
                _tenant = _first.scalar_one_or_none()
                if _tenant:
                    _new_dir = _data_dir / f"enterprise_info_{_tenant.id}"
                    if not _new_dir.exists():
                        shutil.copytree(str(_old_dir), str(_new_dir))
                        print(f"[startup] ✅ Migrated enterprise_info → enterprise_info_{_tenant.id}", flush=True)
                    else:
                        print(f"[startup] ℹ️ enterprise_info_{_tenant.id} already exists, skipping migration", flush=True)
    except Exception as e:
        print(f"[startup] ⚠️ enterprise_info migration failed: {e}", flush=True)

    try:
        from app.services.tool_seeder import seed_builtin_tools, clean_orphaned_mcp_tools
        await seed_builtin_tools()
        await clean_orphaned_mcp_tools()
    except Exception as e:
        logger.warning(f"[startup] Builtin tools seed or cleanup failed: {e}")

    try:
        from app.services.tool_seeder import seed_atlassian_rovo_config, get_atlassian_api_key
        await seed_atlassian_rovo_config()
        # Auto-import Atlassian Rovo tools if an API key is already configured
        _rovo_key = await get_atlassian_api_key()
        if _rovo_key:
            from app.services.resource_discovery import seed_atlassian_rovo_tools
            await seed_atlassian_rovo_tools(_rovo_key)
    except Exception as e:
        logger.warning(f"[startup] Atlassian tools seed failed: {e}")

    try:
        await seed_agent_templates()
    except Exception as e:
        logger.warning(f"[startup] Agent templates seed failed: {e}")

    try:
        from app.services.skill_seeder import seed_skills, push_default_skills_to_existing_agents
        await seed_skills()
        await push_default_skills_to_existing_agents()
    except Exception as e:
        logger.warning(f"[startup] Skills seed failed: {e}")

    try:
        from app.services.agent_seeder import seed_default_agents
        await seed_default_agents()
    except Exception as e:
        logger.warning(f"[startup] Default agents seed failed: {e}")

    try:
        # Seed OKR Agent independently (supports retroactive creation on existing deployments)
        from app.services.agent_seeder import seed_okr_agent
        await seed_okr_agent()
    except Exception as e:
        logger.warning(f"[startup] OKR Agent seed failed: {e}")

    try:
        # Patch existing OKR Agent with new fields/tools/triggers added in later versions
        from app.services.agent_seeder import patch_existing_okr_agent
        await patch_existing_okr_agent()
    except Exception as e:
        logger.warning(f"[startup] OKR Agent patch failed: {e}")

    # Start background tasks (always, even if seeding failed)
    try:
        logger.info("[startup] starting background tasks...")
        from app.services.audit_logger import write_audit_log
        await write_audit_log("server_startup", {"pid": os.getpid()})

        def _bg_task_error(t):
            """Callback to surface background task exceptions."""
            try:
                exc = t.exception()
            except asyncio.CancelledError:
                return
            if exc:
                logger.error(f"[startup] Background task {t.get_name()} CRASHED: {exc}")
                import traceback
                traceback.print_exception(type(exc), exc, exc.__traceback__)

        for name, coro in [
            ("trigger_daemon", start_trigger_daemon()),
            ("feishu_ws", feishu_ws_manager.start_all()),
            ("dingtalk_stream", dingtalk_stream_manager.start_all()),
            ("wecom_stream", wecom_stream_manager.start_all()),
            ("wechat_poll", wechat_poll_manager.start_all()),
            ("discord_gw", discord_gateway_manager.start_all()),
        ]:
            task = asyncio.create_task(coro, name=name)
            task.add_done_callback(_bg_task_error)
            logger.info(f"[startup] created bg task: {name}")
        logger.info("[startup] all background tasks created!")
    except Exception as e:
        logger.error(f"[startup] Background tasks failed: {e}")
        import traceback
        traceback.print_exc()

    # Start ss-local SOCKS5 proxy for Discord API calls (non-fatal)
    ss_task = asyncio.create_task(_start_ss_local(), name="ss-local-proxy")
    ss_task.add_done_callback(_bg_task_error)

    yield

    # Shutdown
    await close_redis()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# Add TraceIdMiddleware first so it's executed for all requests
app.add_middleware(TraceIdMiddleware)

# CORS
_cors_origins = settings.CORS_ORIGINS
_allow_creds = "*" not in _cors_origins  # CORS spec forbids credentials with wildcard
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_allow_creds,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routes
from app.api.auth import router as auth_router
from app.api.agents import router as agents_router
from app.api.tasks import router as tasks_router
from app.api.files import router as files_router
from app.api.websocket import router as ws_router
from app.api.feishu import router as feishu_router
from app.api.sso import router as sso_router
from app.api.organization import router as org_router
from app.api.enterprise import router as enterprise_router
from app.api.advanced import router as advanced_router
from app.api.upload import router as upload_router
from app.api.relationships import router as relationships_router
from app.api.files import upload_router as files_upload_router, enterprise_kb_router
from app.api.activity import router as activity_router
from app.api.messages import router as messages_router
from app.api.tenants import router as tenants_router
from app.api.schedules import router as schedules_router
from app.api.tools import router as tools_router
from app.api.plaza import router as plaza_router
from app.api.skills import router as skills_router
from app.api.users import router as users_router
from app.api.chat_sessions import router as chat_sessions_router
from app.api.slack import router as slack_router
from app.api.discord_bot import router as discord_router
from app.api.dingtalk import router as dingtalk_router
from app.api.google_workspace import router as google_workspace_router
from app.api.wecom import router as wecom_router
from app.api.wechat import router as wechat_router
from app.api.teams import router as teams_router
from app.api.triggers import router as triggers_router

from app.api.atlassian import router as atlassian_router

from app.api.webhooks import router as webhooks_router
from app.api.notification import router as notification_router
from app.api.gateway import router as gateway_router
from app.api.admin import router as admin_router
from app.api.pages import router as pages_router, public_router as pages_public_router
from app.api.agent_credentials import router as credentials_router
from app.api.agentbay_control import router as agentbay_control_router
from app.api.okr import router as okr_router

app.include_router(auth_router, prefix=settings.API_PREFIX)
app.include_router(agents_router, prefix=settings.API_PREFIX)
app.include_router(tasks_router, prefix=settings.API_PREFIX)
app.include_router(files_router, prefix=settings.API_PREFIX)
app.include_router(feishu_router, prefix=settings.API_PREFIX)
app.include_router(sso_router, prefix=settings.API_PREFIX)
app.include_router(org_router, prefix=settings.API_PREFIX)
app.include_router(enterprise_router, prefix=settings.API_PREFIX)
app.include_router(advanced_router, prefix=settings.API_PREFIX)
app.include_router(upload_router, prefix=settings.API_PREFIX)
app.include_router(relationships_router, prefix=settings.API_PREFIX)
app.include_router(activity_router, prefix=settings.API_PREFIX)
app.include_router(messages_router, prefix=settings.API_PREFIX)
app.include_router(tenants_router, prefix=settings.API_PREFIX)
app.include_router(schedules_router, prefix=settings.API_PREFIX)
app.include_router(tools_router, prefix=settings.API_PREFIX)
app.include_router(files_upload_router, prefix=settings.API_PREFIX)
app.include_router(enterprise_kb_router, prefix=settings.API_PREFIX)
app.include_router(skills_router, prefix=settings.API_PREFIX)
app.include_router(users_router, prefix=settings.API_PREFIX)
app.include_router(slack_router, prefix=settings.API_PREFIX)
app.include_router(discord_router, prefix=settings.API_PREFIX)
app.include_router(dingtalk_router, prefix=settings.API_PREFIX)
app.include_router(google_workspace_router, prefix=settings.API_PREFIX)
app.include_router(wecom_router, prefix=settings.API_PREFIX)
app.include_router(wechat_router, prefix=settings.API_PREFIX)
app.include_router(teams_router, prefix=settings.API_PREFIX)

app.include_router(atlassian_router, prefix=settings.API_PREFIX)

app.include_router(triggers_router)
app.include_router(chat_sessions_router)
app.include_router(plaza_router)
app.include_router(notification_router, prefix=settings.API_PREFIX)
app.include_router(webhooks_router)  # Public endpoint, no API prefix
app.include_router(ws_router)
app.include_router(gateway_router, prefix=settings.API_PREFIX)
app.include_router(admin_router, prefix=settings.API_PREFIX)
app.include_router(pages_router, prefix=settings.API_PREFIX)
app.include_router(pages_public_router)  # Public endpoint for /p/{short_id}, no API prefix
app.include_router(credentials_router, prefix=settings.API_PREFIX)
app.include_router(agentbay_control_router, prefix=settings.API_PREFIX)
app.include_router(okr_router)  # OKR — self-prefixed at /api/okr


@app.get("/api/health", response_model=HealthResponse, tags=["health"])
async def health_check():
    """Health check endpoint."""
    return HealthResponse(status="ok", version=settings.APP_VERSION)


# ── Version endpoint (public, no auth required) ──
def _load_version_info() -> dict[str, str]:
    """Read version + commit hash once at startup."""
    import os, subprocess
    version = "unknown"
    for candidate in ["../frontend/VERSION", "frontend/VERSION", "VERSION"]:
        try:
            version = open(candidate).read().strip()
            break
        except FileNotFoundError:
            continue
    commit = ""
    for commit_file in ["../COMMIT", "COMMIT", "../frontend/COMMIT"]:
        try:
            commit = open(commit_file).read().strip()
            break
        except FileNotFoundError:
            continue
    if not commit:
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL, timeout=3,
            ).decode().strip()
        except Exception:
            pass
    return {"version": version, "commit": commit}

_version_cache = _load_version_info()

@app.get("/api/version", tags=["system"])
async def get_version():
    """Return current Clawith version and commit hash."""
    return _version_cache
