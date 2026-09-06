"""The explicit integration point for approved owner-private schema modules."""

from importlib import import_module

from sqlalchemy import MetaData

from app.infrastructure.database import Base

SCHEMA_OWNERS = (
    "identity_tenant", "credential", "model", "agent", "permission", "auth", "audit", "run", "context",
)


def register_schema() -> MetaData:
    """Register the complete S0/S1 graph on the existing Base; perform no DDL."""
    for owner in SCHEMA_OWNERS:
        import_module(f"app.modules.{owner}.models")
    return Base.metadata
