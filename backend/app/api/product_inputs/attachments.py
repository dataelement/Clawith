"""Bounded raw-body attachment upload/download over authenticated product owners."""

from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import cast
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.api.product_inputs.auth import authenticated
from app.execution_dependencies.attachment_inputs import MAX_BYTES, AttachmentInputs, OwnerKind, View
from app.modules.identity_tenant.public import TenantPrincipal

router = APIRouter(prefix="/api", tags=["attachments"])


def _inputs(request: Request) -> AttachmentInputs:
    return cast(AttachmentInputs, request.app.state.attachment_inputs)


async def _body(request: Request) -> AsyncIterator[bytes]:
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdigit() or len(length) > 12:
            raise HTTPException(400, "Invalid attachment body length")
        if int(length) > MAX_BYTES:
            raise HTTPException(413, "Attachment exceeds four MiB")
    consumed = 0
    async for chunk in request.stream():
        consumed += len(chunk)
        if consumed > MAX_BYTES:
            raise HTTPException(413, "Attachment exceeds four MiB")
        yield chunk


def _view(value: View) -> dict[str, object]:
    return {**asdict(value), "reference": value.reference}


@router.post("/sessions/{session_id}/attachments", status_code=201)
async def upload_session(session_id: UUID, request: Request, upload_source_key: str = Query(min_length=1, max_length=512),
        filename: str = Query(min_length=1, max_length=512), media_type: str = Query("application/octet-stream", max_length=256)) -> dict[str, object]:
    access = await authenticated(request)
    result = await _inputs(request).upload_session(access.principal, session_id=session_id, upload_source_key=upload_source_key,
        filename=filename, media_type=media_type, chunks=_body(request))
    return _view(result)


@router.post("/groups/{group_id}/attachments", status_code=201)
async def upload_group(group_id: UUID, request: Request, upload_source_key: str = Query(min_length=1, max_length=512),
        filename: str = Query(min_length=1, max_length=512), media_type: str = Query("application/octet-stream", max_length=256)) -> dict[str, object]:
    access = await authenticated(request)
    result = await _inputs(request).upload_group(access.principal, group_id=group_id, upload_source_key=upload_source_key,
        filename=filename, media_type=media_type, chunks=_body(request))
    return _view(result)


async def _download(request: Request, principal: TenantPrincipal, kind: OwnerKind, owner_id: UUID, attachment_id: UUID) -> Response:
    blob = await _inputs(request).read_human(principal, kind=kind, owner_id=owner_id, attachment_id=attachment_id)
    return Response(blob.content, media_type=blob.media_type, headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(blob.name, safe=""),
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.get("/sessions/{session_id}/attachments/{attachment_id}")
async def download_session(session_id: UUID, attachment_id: UUID, request: Request) -> Response:
    access = await authenticated(request)
    return await _download(request, access.principal, "session", session_id, attachment_id)


@router.get("/groups/{group_id}/attachments/{attachment_id}")
async def download_group(group_id: UUID, attachment_id: UUID, request: Request) -> Response:
    access = await authenticated(request)
    return await _download(request, access.principal, "group", group_id, attachment_id)
