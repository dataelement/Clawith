"""Model-private encrypted replay items; no Run lifecycle authority."""

import json
import math
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.model.models import ProviderContinuationRecord


class ContinuationError(Exception):
    """Exact replay state cannot be read or committed safely."""


def validate_replay(payload: Any, protocol: str) -> None:
    """Validate envelopes and exact supported replay shapes without rewriting opaque fields."""
    def required_text(value: Any) -> None:
        if not isinstance(value, str) or not value:
            raise ContinuationError("continuation string is missing")

    def object_value(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ContinuationError("continuation object is invalid")
        return value

    def finite(value: Any, depth: int = 0) -> None:
        if depth > 32:
            raise ContinuationError("continuation nesting exceeds bound")
        if isinstance(value, dict):
            for nested in value.values():
                finite(nested, depth + 1)
        elif isinstance(value, list):
            for nested in value:
                finite(nested, depth + 1)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ContinuationError("continuation contains non-finite numbers")

    finite(payload)
    for identity, items in object_value(payload).items():
        required_text(identity)
        if len(identity) > 256 or not isinstance(items, list) or not items:
            raise ContinuationError("continuation interaction is invalid")
        required = False
        for raw in items:
            item = object_value(raw)
            if protocol == "openai_chat":
                if len(items) != 1:
                    raise ContinuationError("invalid chat continuation")
                required_text(item.get("reasoning_content"))
                required = True
            elif protocol == "anthropic":
                kind = item.get("type")
                if kind == "thinking":
                    if not isinstance(item.get("thinking"), str):
                        raise ContinuationError("invalid thinking content")
                    required_text(item.get("signature"))
                    required = True
                elif kind == "redacted_thinking":
                    required_text(item.get("data"))
                    required = True
                elif kind == "text":
                    if not isinstance(item.get("text"), str):
                        raise ContinuationError("invalid replay text")
                elif kind == "tool_use":
                    required_text(item.get("id"))
                    required_text(item.get("name"))
                    object_value(item.get("input"))
                else:
                    raise ContinuationError("unsupported Anthropic replay block")
            elif protocol == "openai_responses":
                kind = item.get("type")
                if kind == "reasoning":
                    required_text(item.get("id"))
                    required_text(item.get("encrypted_content"))
                    if not isinstance(item.get("summary"), list):
                        raise ContinuationError("invalid reasoning summary")
                    for raw_summary in item["summary"]:
                        summary = object_value(raw_summary)
                        if summary.get("type") != "summary_text" or not isinstance(summary.get("text"), str):
                            raise ContinuationError("invalid reasoning summary item")
                    required = True
                elif kind == "function_call":
                    required_text(item.get("call_id"))
                    required_text(item.get("name"))
                    required_text(item.get("arguments"))
                    try:
                        object_value(json.loads(item["arguments"]))
                    except (ValueError, RecursionError):
                        raise ContinuationError("invalid replay arguments") from None
                elif kind == "message":
                    if item.get("role") != "assistant" or not isinstance(item.get("content"), list):
                        raise ContinuationError("invalid assistant replay message")
                    for raw_block in item["content"]:
                        block = object_value(raw_block)
                        if block.get("type") == "output_text":
                            if not isinstance(block.get("text"), str):
                                raise ContinuationError("invalid assistant replay text")
                        elif block.get("type") == "refusal":
                            required_text(block.get("refusal"))
                        else:
                            raise ContinuationError("unsupported assistant replay content")
                else:
                    raise ContinuationError("unsupported Responses replay item")
            elif protocol == "gemini":
                if "thoughtSignature" in item:
                    required_text(item["thoughtSignature"])
                    required = True
                if "functionCall" in item:
                    function = object_value(item["functionCall"])
                    required_text(function.get("name"))
                    object_value(function.get("args"))
                    if "id" in function:
                        required_text(function["id"])
                elif "text" in item:
                    if not isinstance(item["text"], str):
                        raise ContinuationError("invalid Gemini replay text")
                else:
                    raise ContinuationError("unsupported Gemini replay part")
            else:
                raise ContinuationError("unsupported continuation protocol")
        if not required:
            raise ContinuationError("continuation has no required replay content")


class ContinuationStore:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, keys: Mapping[str, bytes],
        active_key: str, max_bytes: int,
    ) -> None:
        if active_key not in keys or not keys or any(len(key) != 32 for key in keys.values()) or max_bytes <= 0:
            raise ValueError("valid continuation keys and positive byte bound are required")
        self.sessions = sessions
        self.keys = dict(keys)
        self.active_key = active_key
        self.max_bytes = max_bytes

    @staticmethod
    def _aad(tenant_id: UUID, run_id: UUID, model_id: UUID, protocol: str) -> bytes:
        return f"model-continuation:1:{tenant_id}:{run_id}:{model_id}:{protocol}".encode()

    async def load(self, tenant_id: UUID, run_id: UUID, model_id: UUID, protocol: str) -> dict[str, Any]:
        async with self.sessions() as session:
            size = await session.scalar(select(func.octet_length(ProviderContinuationRecord.encrypted_payload)).where(
                ProviderContinuationRecord.tenant_id == tenant_id,
                ProviderContinuationRecord.run_id == run_id,
                ProviderContinuationRecord.model_id == model_id,
            ))
            if size is None:
                return {}
            if size > self.max_bytes + 28:
                raise ContinuationError("continuation state exceeds bound")
            row = await session.scalar(select(ProviderContinuationRecord).where(
                ProviderContinuationRecord.tenant_id == tenant_id,
                ProviderContinuationRecord.run_id == run_id,
                ProviderContinuationRecord.model_id == model_id,
                func.octet_length(ProviderContinuationRecord.encrypted_payload) <= self.max_bytes + 28,
            ))
            if row is None:
                raise ContinuationError("continuation changed while loading")
            if (row.payload_schema_version != 1 or row.encryption_version != 1
                    or row.payload_kind != protocol or row.key_version not in self.keys
                    or len(row.encrypted_payload) > self.max_bytes + 28):
                raise ContinuationError("unsupported continuation state")
            try:
                raw = AESGCM(self.keys[row.key_version]).decrypt(
                    row.encrypted_payload[:12], row.encrypted_payload[12:],
                    self._aad(tenant_id, run_id, model_id, protocol),
                )
                payload = json.loads(raw)
            except (InvalidTag, ValueError, UnicodeDecodeError, RecursionError):
                raise ContinuationError("invalid continuation state") from None
            validate_replay(payload, protocol)
            return payload

    async def save(
        self, tenant_id: UUID, run_id: UUID, model_id: UUID, protocol: str, payload: dict[str, Any],
    ) -> None:
        validate_replay(payload, protocol)
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > self.max_bytes:
            raise ContinuationError("continuation state exceeds bound")
        nonce = os.urandom(12)
        encrypted = nonce + AESGCM(self.keys[self.active_key]).encrypt(
            nonce, raw, self._aad(tenant_id, run_id, model_id, protocol),
        )
        now = datetime.now(UTC)
        statement = insert(ProviderContinuationRecord).values(
            id=uuid4(), tenant_id=tenant_id, run_id=run_id, model_id=model_id,
            payload_kind=protocol, payload_schema_version=1, encryption_version=1,
            key_version=self.active_key, encrypted_payload=encrypted, created_at=now, updated_at=now,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_provider_continuations_run_model",
            set_={"payload_kind": protocol, "payload_schema_version": 1, "encryption_version": 1,
                  "key_version": self.active_key, "encrypted_payload": encrypted, "updated_at": now},
        )
        async with self.sessions.begin() as session:
            await session.execute(statement)

    async def remove(self, tenant_id: UUID, run_id: UUID, model_id: UUID) -> None:
        async with self.sessions.begin() as session:
            await session.execute(delete(ProviderContinuationRecord).where(
                ProviderContinuationRecord.tenant_id == tenant_id,
                ProviderContinuationRecord.run_id == run_id,
                ProviderContinuationRecord.model_id == model_id,
            ))
