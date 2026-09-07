"""Final application composition root."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

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
        resources = await database.create_database_resources(application_settings)
        try:
            audit = AsyncAuditSink(
                resources.execution_sessions,
                capacity=AUDIT_QUEUE_CAPACITY,
                shutdown_timeout=AUDIT_SHUTDOWN_TIMEOUT_SECONDS,
            )
            try:
                audit.start()
                application.state.database = resources
                application.state.audit = audit
                yield
            finally:
                await audit.close()
        finally:
            try:
                await resources.aclose()
            finally:
                for name in ("audit", "database"):
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
