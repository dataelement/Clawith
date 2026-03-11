"""Clawith Backend — FastAPI Application Entry Point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.core.events import close_redis
from app.schemas.schemas import HealthResponse

settings = get_settings()


async def _start_ss_local() -> None:
    """Start ss-local SOCKS5 proxy for Discord API calls. Tries nodes in priority order."""
    import asyncio, json, os, shutil, tempfile
    if not shutil.which("ss-local"):
        print("[Proxy] ss-local not found — Discord proxy disabled", flush=True)
        return
    # Load proxy nodes from config file (gitignored, mounted as Docker volume)
    import json as _json
    cfg_file = os.environ.get("SS_CONFIG_FILE", "/data/ss-nodes.json")
    if os.path.exists(cfg_file):
        nodes = _json.load(open(cfg_file))
        print(f"[Proxy] Loaded {len(nodes)} node(s) from {cfg_file}", flush=True)
    elif os.environ.get("SS_SERVER") and os.environ.get("SS_PASSWORD"):
        nodes = [{"server": os.environ["SS_SERVER"], "port": int(os.environ.get("SS_PORT", "1080")),
                  "password": os.environ["SS_PASSWORD"], "method": os.environ.get("SS_METHOD", "chacha20-ietf-poly1305"), "label": "env"}]
    else:
        print(f"[Proxy] {cfg_file} not found and SS_SERVER not set — skipping proxy", flush=True)
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
                print(f"[Proxy] ss-local → {node['label']} ({node['server']}:{node['port']})", flush=True)
                return
            err = (await proc.stderr.read()).decode()[:120]
            print(f"[Proxy] {node['label']} failed: {err}", flush=True)
        except Exception as e:
            print(f"[Proxy] {node['label']} error: {e}", flush=True)
    print("[Proxy] All SS nodes failed — Discord API calls will run without proxy", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown events."""
    import asyncio
    import sys
    import os
    from app.services.trigger_daemon import start_trigger_daemon
    from app.services.tool_seeder import seed_builtin_tools
    from app.services.template_seeder import seed_agent_templates

    # ── Step 0: Ensure all DB tables exist (idempotent, safe to run on every startup) ──
    try:
        from app.database import Base, engine
        # Import all models so Base.metadata is fully populated
        import app.models.user           # noqa
        import app.models.agent          # noqa
        import app.models.task           # noqa
        import app.models.llm            # noqa
        import app.models.tool           # noqa
        import app.models.audit          # noqa
        import app.models.skill          # noqa
        import app.models.channel_config  # noqa
        import app.models.schedule       # noqa
        import app.models.plaza          # noqa
        import app.models.activity_log   # noqa
        import app.models.org            # noqa
        import app.models.system_settings  # noqa
        import app.models.invitation_code  # noqa
        import app.models.tenant         # noqa
        import app.models.participant    # noqa
        import app.models.chat_session   # noqa
        import app.models.trigger        # noqa
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("[startup] ✅ Database tables ready", flush=True)
    except Exception as e:
        print(f"[startup] ⚠️ create_all failed: {e}", flush=True)

    # Startup: seed data (non-fatal)
    try:
        print("[startup] seeding...", flush=True)

        # Seed default company (Tenant) — required before users can register
        from app.models.tenant import Tenant
        from app.database import async_session as _session
        from sqlalchemy import select as _select
        async with _session() as _db:
            _existing = await _db.execute(_select(Tenant).where(Tenant.slug == "default"))
            if not _existing.scalar_one_or_none():
                _db.add(Tenant(name="Default", slug="default", im_provider="web_only"))
                await _db.commit()
                print("[startup] ✅ Default company created", flush=True)

        await seed_builtin_tools()
        await seed_agent_templates()
        from app.services.skill_seeder import seed_skills, push_default_skills_to_existing_agents
        await seed_skills()
        await push_default_skills_to_existing_agents()
        from app.services.agent_seeder import seed_default_agents
        await seed_default_agents()
    except Exception as e:
        print(f"[startup] ⚠️ Seeding failed (non-fatal): {e}", flush=True)

    # Start background tasks (always, even if seeding failed)
    try:
        print("[startup] starting background tasks...", flush=True)
        from app.services.audit_logger import write_audit_log
        await write_audit_log("server_startup", {"pid": os.getpid()})

        def _bg_task_error(t):
            """Callback to surface background task exceptions."""
            try:
                exc = t.exception()
            except asyncio.CancelledError:
                return
            if exc:
                print(f"[startup] ⚠️ Background task {t.get_name()} CRASHED: {exc}", flush=True)
                import traceback
                traceback.print_exception(type(exc), exc, exc.__traceback__)

        for name, coro in [
            ("trigger_daemon", start_trigger_daemon()),
        ]:
            task = asyncio.create_task(coro, name=name)
            task.add_done_callback(_bg_task_error)
            print(f"[startup] created bg task: {name}", flush=True)
        print("[startup] all background tasks created!", flush=True)
    except Exception as e:
        print(f"[startup] ⛔ Background tasks failed: {e}", flush=True)
        import traceback
        traceback.print_exc()

    # Start ss-local SOCKS5 proxy for Discord API calls (non-fatal)
    asyncio.create_task(_start_ss_local(), name="ss-local-proxy")

    yield

    # Shutdown
    await close_redis()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

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
from app.api.teams import router as teams_router
from app.api.triggers import router as triggers_router
from app.api.webhooks import router as webhooks_router

app.include_router(auth_router, prefix=settings.API_PREFIX)
app.include_router(agents_router, prefix=settings.API_PREFIX)
app.include_router(tasks_router, prefix=settings.API_PREFIX)
app.include_router(files_router, prefix=settings.API_PREFIX)
app.include_router(feishu_router, prefix=settings.API_PREFIX)
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
app.include_router(teams_router, prefix=settings.API_PREFIX)
app.include_router(triggers_router)
app.include_router(chat_sessions_router)
app.include_router(plaza_router)
app.include_router(webhooks_router)  # Public endpoint, no API prefix
app.include_router(ws_router)


@app.get("/api/health", response_model=HealthResponse, tags=["health"])
async def health_check():
    """Health check endpoint."""
    return HealthResponse(status="ok", version=settings.APP_VERSION)
