"""Final application composition root."""

import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.product_inputs.attachments import router as attachment_router
from app.api.product_inputs.auth import router as auth_router
from app.api.product_inputs.channels import router as channel_router
from app.api.product_inputs.groups import router as group_router
from app.api.product_inputs.schedules import router as schedule_router
from app.api.product_inputs.sessions import router as session_router
from app.execution_dependencies.channel_inputs import ChannelInputs
from app.execution_dependencies.product_inputs import ProductInputs
from app.execution_dependencies.resources import open_execution_resources
from app.execution_dependencies.runtime import compose_runtime
from app.infrastructure import database
from app.infrastructure.config import Settings, get_settings
from app.infrastructure.errors import AccessDenied, Conflict, DomainError, InvalidInput, NotFound
from app.modules.audit.public import AsyncAuditSink
from app.modules.auth.public import AuthService
from app.modules.channel.public import ChannelContextCodec
from app.modules.run.public import OutcomeConsumer

AUDIT_QUEUE_CAPACITY = 256
AUDIT_SHUTDOWN_TIMEOUT_SECONDS = 2.0


def create_app(settings: Settings | None = None, *, outcome_consumer: OutcomeConsumer | None = None) -> FastAPI:
    """Compose target owner services and their application-owned lifecycles."""
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
            products = ProductInputs(resources, execution, outcome_consumer)
            cleanup.push_async_callback(products.documents.close)
            cleanup.push_async_callback(products.streams.close)
            runtime = compose_runtime(resources, execution, outcome_consumer=products,
                start_consumer=products, waiting_consumer=products, extra_bindings=products.bindings,
                observer=products.streams.observe)
            products.runtime = runtime
            products.other.runtime = runtime
            products.goal.runtime = runtime
            products.scheduled.runtime = runtime
            products.attachments.start_cleanup()
            cleanup.push_async_callback(products.attachments.close)
            cleanup.push_async_callback(products.a2a_files.close)
            await products.a2a_files.start_cleanup()
            channels = ChannelInputs(resources, execution, products, context_codec=ChannelContextCodec(
                active_key_version=application_settings.EXECUTION.continuation_keys.active_version,
                keys=application_settings.EXECUTION.continuation_keys.decoded_keys()))
            cleanup.push_async_callback(runtime.close)
            await runtime.startup()
            cleanup.push_async_callback(products.other.close)
            await products.other.startup()
            cleanup.push_async_callback(channels.close)
            await channels.startup()
            await products.goal.start()
            cleanup.push_async_callback(products.goal.close)
            await products.scheduled.start()
            cleanup.push_async_callback(products.scheduled.close)
            try:
                application.state.database = resources
                application.state.audit = audit
                application.state.execution = execution
                application.state.runtime = runtime
                application.state.auth = AuthService(resources.control_sessions, session_ttl=timedelta(hours=24))
                application.state.products = products
                application.state.scheduled = products.scheduled
                application.state.channel_inputs = channels
                application.state.attachment_inputs = products.attachments
                yield
            finally:
                for name in ("attachment_inputs", "channel_inputs", "scheduled", "products", "auth", "runtime", "execution", "audit", "database"):
                    if hasattr(application.state, name):
                        delattr(application.state, name)

    application = FastAPI(
        title=application_settings.APP_NAME,
        version=application_settings.APP_VERSION,
        debug=application_settings.DEBUG,
        lifespan=lifespan,
    )
    application.include_router(auth_router)
    application.include_router(session_router)
    application.include_router(schedule_router)
    application.include_router(channel_router)
    application.include_router(group_router)
    application.include_router(attachment_router)

    @application.exception_handler(DomainError)
    async def domain_error(_request: Request, error: DomainError) -> JSONResponse:
        status = {AccessDenied: 403, NotFound: 404, Conflict: 409, InvalidInput: 400}.get(type(error), 400)
        return JSONResponse({"error": error.code, "detail": str(error)[:1024]}, status_code=status)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return JSONResponse({"error": "invalid_input", "detail": "Request fields are invalid"}, status_code=422)

    @application.get("/api/health", tags=["health"])
    async def health_check() -> dict[str, str | int]:
        return {
            "status": "ok",
            "version": application_settings.APP_VERSION,
            "process_pid": os.getpid(),
            "startup_id": application_settings.STARTUP_INSTANCE_ID,
        }

    return application
