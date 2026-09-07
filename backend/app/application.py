"""Final application composition root."""

import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from app.execution_dependencies.resources import open_execution_resources
from app.infrastructure import database
from app.infrastructure.config import Settings, get_settings
from app.modules.audit.public import AsyncAuditSink

AUDIT_QUEUE_CAPACITY = 256
AUDIT_SHUTDOWN_TIMEOUT_SECONDS = 2.0


def create_app(settings: Settings | None = None) -> FastAPI:
    """Compose the target application without legacy routes or lifecycle work."""
    application_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if application_settings.EXECUTION is None:
            raise ValueError("EXECUTION configuration with explicit encryption keys and storage is required for startup")
        async with AsyncExitStack() as cleanup:
            resources = await database.create_database_resources(application_settings)
            cleanup.push_async_callback(resources.aclose)
            audit = AsyncAuditSink(
                resources.execution_sessions,
                capacity=AUDIT_QUEUE_CAPACITY,
                shutdown_timeout=AUDIT_SHUTDOWN_TIMEOUT_SECONDS,
            )
            cleanup.push_async_callback(audit.close)
            audit.start()
            execution = await cleanup.enter_async_context(open_execution_resources(application_settings.EXECUTION, resources, audit))
            try:
                application.state.database = resources
                application.state.audit = audit
                application.state.execution = execution
                yield
            finally:
                for name in ("execution", "audit", "database"):
                    if hasattr(application.state, name):
                        delattr(application.state, name)

    application = FastAPI(
        title=application_settings.APP_NAME,
        version=application_settings.APP_VERSION,
        debug=application_settings.DEBUG,
        lifespan=lifespan,
    )

    @application.get("/api/health", tags=["health"])
    async def health_check() -> dict[str, str | int]:
        return {
            "status": "ok",
            "version": application_settings.APP_VERSION,
            "process_pid": os.getpid(),
            "startup_id": application_settings.STARTUP_INSTANCE_ID,
        }

    return application
