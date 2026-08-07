"""Ephemeral, receipt-bound authorization for Feishu approval creation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import hmac
import json
import secrets


_AUTHORIZATION_KEY = secrets.token_bytes(32)


@dataclass(frozen=True, slots=True)
class FeishuApprovalCreateAuthorization:
    """One Runtime confirmation bound to one live Tool Ledger receipt."""

    run_id: str
    tool_call_id: str
    execution_id: str
    lease_owner: str
    tenant_id: str
    agent_id: str
    actor_user_id: str
    arguments_hash: str
    signature: str


def feishu_approval_create_arguments_hash(
    arguments: Mapping[str, object],
) -> str:
    encoded = json.dumps(
        dict(arguments),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _signature(
    *,
    run_id: str,
    tool_call_id: str,
    execution_id: str,
    lease_owner: str,
    tenant_id: str,
    agent_id: str,
    actor_user_id: str,
    arguments_hash: str,
) -> str:
    payload = "\n".join(
        (
            run_id,
            tool_call_id,
            execution_id,
            lease_owner,
            tenant_id,
            agent_id,
            actor_user_id,
            arguments_hash,
        )
    ).encode("utf-8")
    return hmac.new(_AUTHORIZATION_KEY, payload, hashlib.sha256).hexdigest()


def issue_feishu_approval_create_authorization(
    *,
    run_id: str,
    tool_call_id: str,
    execution_id: str,
    lease_owner: str,
    tenant_id: str,
    agent_id: str,
    actor_user_id: str,
    arguments: Mapping[str, object],
) -> FeishuApprovalCreateAuthorization:
    """Issue a process-local proof after exact consent and reservation."""
    arguments_hash = feishu_approval_create_arguments_hash(arguments)
    signature = _signature(
        run_id=run_id,
        tool_call_id=tool_call_id,
        execution_id=execution_id,
        lease_owner=lease_owner,
        tenant_id=tenant_id,
        agent_id=agent_id,
        actor_user_id=actor_user_id,
        arguments_hash=arguments_hash,
    )
    return FeishuApprovalCreateAuthorization(
        run_id=run_id,
        tool_call_id=tool_call_id,
        execution_id=execution_id,
        lease_owner=lease_owner,
        tenant_id=tenant_id,
        agent_id=agent_id,
        actor_user_id=actor_user_id,
        arguments_hash=arguments_hash,
        signature=signature,
    )


def verify_feishu_approval_create_authorization(
    authorization: FeishuApprovalCreateAuthorization | None,
    *,
    run_id: str,
    tool_call_id: str,
    execution_id: str,
    lease_owner: str,
    tenant_id: str,
    agent_id: str,
    actor_user_id: str,
    arguments: Mapping[str, object],
) -> bool:
    """Verify a proof against independently supplied current Runtime facts."""
    if authorization is None:
        return False
    arguments_hash = feishu_approval_create_arguments_hash(arguments)
    expected_fields = (
        run_id,
        tool_call_id,
        execution_id,
        lease_owner,
        tenant_id,
        agent_id,
        actor_user_id,
        arguments_hash,
    )
    actual_fields = (
        authorization.run_id,
        authorization.tool_call_id,
        authorization.execution_id,
        authorization.lease_owner,
        authorization.tenant_id,
        authorization.agent_id,
        authorization.actor_user_id,
        authorization.arguments_hash,
    )
    if actual_fields != expected_fields:
        return False
    expected_signature = _signature(
        run_id=run_id,
        tool_call_id=tool_call_id,
        execution_id=execution_id,
        lease_owner=lease_owner,
        tenant_id=tenant_id,
        agent_id=agent_id,
        actor_user_id=actor_user_id,
        arguments_hash=arguments_hash,
    )
    return hmac.compare_digest(authorization.signature, expected_signature)
