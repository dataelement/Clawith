"""Human login transport; authentication state remains Auth-owned."""

from dataclasses import asdict
from datetime import datetime
from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.errors import AccessDenied
from app.modules.auth.public import AuthenticatedSession, AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    login_name: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024, repr=False)
    tenant_id: UUID


class LoginOutput(BaseModel):
    token: str = Field(repr=False)
    expires_at: datetime


def auth_service(request: Request) -> AuthService:
    return cast(AuthService, request.app.state.auth)


def bearer_token(authorization: str | None) -> str:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    token = authorization[7:]
    if not token or len(token) > 256:
        raise HTTPException(401, "Authentication required")
    return token


async def authenticated(request: Request) -> AuthenticatedSession:
    try:
        return await auth_service(request).authenticate_session(bearer_token(request.headers.get("authorization")))
    except AccessDenied:
        raise HTTPException(401, "Login expired or invalid") from None


@router.post("/login", response_model=LoginOutput)
async def login(body: LoginInput, request: Request) -> LoginOutput:
    auth = auth_service(request)
    try:
        token, _ = await auth.login(body.login_name, body.password, body.tenant_id)
        access = await auth.authenticate_session(token)
    except AccessDenied:
        raise HTTPException(401, "Invalid login credentials") from None
    return LoginOutput(token=token, expires_at=access.expires_at)


@router.post("/logout", status_code=204)
async def logout(request: Request) -> None:
    try:
        await auth_service(request).logout(bearer_token(request.headers.get("authorization")))
    except AccessDenied:
        raise HTTPException(401, "Login expired or invalid") from None


@router.get("/me")
async def me(request: Request) -> dict[str, object]:
    access = await authenticated(request)
    return {"principal": asdict(access.principal), "expires_at": access.expires_at}
