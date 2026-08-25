"""Runtime scope for reusing one local sandbox during an Agent loop."""

from contextvars import ContextVar


sandbox_run_scope_id: ContextVar[str] = ContextVar(
    "sandbox_run_scope_id",
    default="",
)
