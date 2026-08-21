"""Regression checks for FastAPI database-session dependency injection."""

from fastapi.routing import APIRoute

from app.main import app


def test_database_session_is_never_exposed_as_a_query_parameter() -> None:
    """A missing Depends(get_db) silently turns ``db`` into an optional query parameter."""
    offenders = sorted(
        f"{','.join(sorted(route.methods or []))} {route.path}"
        for route in app.routes
        if isinstance(route, APIRoute)
        and any(parameter.name == "db" for parameter in route.dependant.query_params)
    )

    assert offenders == [], "Routes with an un-injected db parameter:\n" + "\n".join(offenders)
