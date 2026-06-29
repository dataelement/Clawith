"""Shared roster lookup for agent tools and HTTP APIs."""

import uuid
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import evaluate_roster_agent_visibility, evaluate_roster_human_visibility
from app.models.agent import Agent as AgentModel
from app.models.identity import IdentityProvider
from app.models.org import OrgDepartment, OrgMember
from app.models.user import User as UserModel

RosterMemberType = Literal["all", "agent", "human"]


class RosterQueryError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def provider_type_value(provider_type: Any) -> str | None:
    if provider_type is None:
        return None
    return getattr(provider_type, "value", provider_type)


def normalize_provider_type(provider_type: Any) -> str | None:
    value = provider_type_value(provider_type)
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized == "microsoft_teams":
        return "teams"
    return normalized or None


def channel_message_ready(provider_type: str | None, member: OrgMember) -> bool:
    """Return whether send_channel_message has the identifiers it actually uses."""
    if not provider_type:
        return False
    if provider_type == "feishu":
        return bool((member.external_id or "").strip())
    if provider_type == "dingtalk":
        return bool((member.external_id or member.unionid or member.open_id or "").strip())
    if provider_type == "wecom":
        return bool((member.external_id or member.open_id or "").strip())
    if provider_type == "slack":
        return bool((member.external_id or "").strip())
    # Teams and WeChat proactive sends require per-user inbound conversation state
    # that this pure roster formatter cannot verify, so do not advertise them here.
    return False


def query_text_match_rank(member: dict, query: str) -> int:
    if not query:
        return 4
    q = query.casefold()
    display_name = (member.get("display_name") or "").casefold()
    if display_name == q:
        return 0
    if display_name.startswith(q):
        return 1
    if q in display_name:
        return 2
    return 3


def roster_sort_key(member: dict, query: str) -> tuple:
    return (
        0 if member.get("can_contact") else 1,
        query_text_match_rank(member, query),
        0 if member.get("member_type") == "agent" else 1,
        (member.get("display_name") or "").casefold(),
        member.get("target_agent_id") or member.get("target_member_id") or "",
    )


def department_name(member: OrgMember, department: OrgDepartment | None) -> str | None:
    if department and department.name:
        return department.name
    department_path = (getattr(member, "department_path", None) or "").strip()
    if not department_path:
        return None
    for sep in ("/", ">"):
        if sep in department_path:
            return department_path.split(sep)[-1].strip() or None
    return department_path


def format_roster_agent(source_agent: AgentModel, target_agent: AgentModel) -> dict | None:
    visibility = evaluate_roster_agent_visibility(source_agent, target_agent)
    if not visibility.visible:
        return None
    return {
        "member_type": "agent",
        "target_agent_id": str(target_agent.id),
        "display_name": target_agent.name,
        "role_description": target_agent.role_description or "",
        "capabilities": [],
        "department": None,
        "skills": [],
        "access_mode": getattr(target_agent, "access_mode", None) or "company",
        "can_contact": visibility.can_contact,
        "contact_tools": ["send_message_to_agent"] if visibility.can_contact else [],
        "unavailable_reason": visibility.unavailable_reason,
    }


def format_roster_human(
    source_agent: AgentModel,
    member: OrgMember,
    provider: IdentityProvider | None,
    department: OrgDepartment | None,
    platform_user: UserModel | None = None,
) -> dict | None:
    visibility = evaluate_roster_human_visibility(source_agent, member)
    if not visibility.visible:
        return None

    provider_type = normalize_provider_type(getattr(provider, "provider_type", None))
    contact_tools: list[str] = []
    platform_user_ready = (
        platform_user is not None
        and getattr(platform_user, "tenant_id", None) == getattr(source_agent, "tenant_id", None)
        and bool(getattr(platform_user, "is_active", False))
    )
    if visibility.can_contact and member.user_id and platform_user_ready:
        contact_tools.append("send_platform_message")
    if visibility.can_contact and channel_message_ready(provider_type, member):
        contact_tools.append("send_channel_message")

    can_contact = visibility.can_contact and bool(contact_tools)
    unavailable_reason = visibility.unavailable_reason
    if visibility.can_contact and not contact_tools:
        unavailable_reason = "missing_contact_target"

    dept_name = department_name(member, department)
    department_payload = None
    if member.department_id or dept_name:
        department_payload = {
            "id": str(member.department_id) if member.department_id else None,
            "name": dept_name,
        }

    provider_payload = None
    if provider or member.provider_id or member.open_id or member.external_id:
        provider_payload = {
            "provider_id": str(member.provider_id) if member.provider_id else None,
            "provider_type": provider_type,
            "open_id": member.open_id,
            "external_id": member.external_id,
        }

    return {
        "member_type": "human",
        "target_member_id": str(member.id),
        "platform_user_id": str(member.user_id) if member.user_id else None,
        "display_name": member.name,
        "title": member.title or "",
        "department": department_payload,
        "can_contact": can_contact,
        "contact_tools": contact_tools if can_contact else [],
        "provider": provider_payload,
        "unavailable_reason": None if can_contact else unavailable_reason,
    }


def _coerce_target_member_id(target_member_id: uuid.UUID | str | None) -> uuid.UUID | None:
    if not target_member_id:
        return None
    if isinstance(target_member_id, uuid.UUID):
        return target_member_id
    try:
        return uuid.UUID(str(target_member_id))
    except ValueError as exc:
        raise RosterQueryError("invalid_target_member_id", "target_member_id must be a valid UUID") from exc


def _validate_member_type(member_type: str) -> RosterMemberType:
    normalized = (member_type or "all").strip().lower()
    if normalized not in {"all", "agent", "human"}:
        raise RosterQueryError("invalid_member_type", "member_type must be all, agent, or human")
    return normalized  # type: ignore[return-value]


def _validate_pagination(limit: int, offset: int, max_limit: int) -> None:
    if limit < 1 or limit > max_limit:
        raise RosterQueryError("invalid_limit", f"limit must be between 1 and {max_limit}")
    if offset < 0:
        raise RosterQueryError("invalid_offset", "offset must be greater than or equal to 0")


async def query_agent_roster(
    db: AsyncSession,
    *,
    source_agent_id: uuid.UUID,
    query: str = "",
    target_member_id: uuid.UUID | str | None = None,
    member_type: str = "all",
    include_uncontactable: bool = False,
    limit: int = 50,
    offset: int = 0,
    max_limit: int = 100,
) -> dict:
    query = (query or "").strip()
    member_type = _validate_member_type(member_type)
    target_member_uuid = _coerce_target_member_id(target_member_id)
    _validate_pagination(limit, offset, max_limit)
    if target_member_uuid and member_type == "agent":
        raise RosterQueryError(
            "invalid_member_type",
            "target_member_id can only be used with member_type human or all",
        )

    fetch_size = offset + limit + 1
    members: list[dict] = []

    source = (await db.execute(select(AgentModel).where(AgentModel.id == source_agent_id))).scalar_one_or_none()
    if not source:
        raise RosterQueryError("source_agent_not_found", "Source agent was not found.", status_code=404)

    source_mode = getattr(source, "access_mode", None) or "company"

    if member_type in {"all", "agent"} and not target_member_uuid:
        agent_conditions = [
            AgentModel.tenant_id == source.tenant_id,
            AgentModel.id != source.id,
        ]
        if source_mode == "private":
            agent_conditions.extend([
                AgentModel.access_mode == "private",
                AgentModel.creator_id == source.creator_id,
            ])
        else:
            agent_conditions.append(AgentModel.access_mode.in_(["company", "custom"]))
        if query:
            agent_conditions.append(or_(
                AgentModel.name.ilike(f"%{query}%"),
                AgentModel.role_description.ilike(f"%{query}%"),
            ))
        if not include_uncontactable:
            agent_conditions.extend([
                AgentModel.status.in_(["running", "idle"]),
                AgentModel.is_expired == False,  # noqa: E712
            ])

        agent_result = await db.execute(
            select(AgentModel)
            .where(*agent_conditions)
            .order_by(AgentModel.name.asc(), AgentModel.created_at.asc())
            .limit(fetch_size)
        )
        for target_agent in agent_result.scalars().all():
            payload = format_roster_agent(source, target_agent)
            if payload and (include_uncontactable or payload["can_contact"]):
                members.append(payload)

    if member_type in {"all", "human"}:
        human_conditions = [OrgMember.tenant_id == source.tenant_id]
        if target_member_uuid:
            human_conditions.append(OrgMember.id == target_member_uuid)
        if source_mode == "private":
            human_conditions.append(OrgMember.user_id == source.creator_id)
        if query and not target_member_uuid:
            human_conditions.append(or_(
                OrgMember.name.ilike(f"%{query}%"),
                OrgMember.title.ilike(f"%{query}%"),
                OrgMember.department_path.ilike(f"%{query}%"),
            ))

        human_result = await db.execute(
            select(OrgMember, IdentityProvider, OrgDepartment, UserModel)
            .outerjoin(IdentityProvider, OrgMember.provider_id == IdentityProvider.id)
            .outerjoin(OrgDepartment, OrgMember.department_id == OrgDepartment.id)
            .outerjoin(UserModel, OrgMember.user_id == UserModel.id)
            .where(*human_conditions)
            .order_by(OrgMember.name.asc(), OrgMember.synced_at.asc())
            .limit(fetch_size)
        )
        for member, provider, department, platform_user in human_result.all():
            payload = format_roster_human(source, member, provider, department, platform_user)
            if payload and (include_uncontactable or payload["can_contact"]):
                members.append(payload)

    members.sort(key=lambda member: roster_sort_key(member, query))
    page = members[offset:offset + limit]
    return {
        "ok": True,
        "source_agent_id": str(source_agent_id),
        "query": query,
        "member_type": member_type,
        "include_uncontactable": include_uncontactable,
        "returned_count": len(page),
        "limit": limit,
        "offset": offset,
        "has_more": len(members) > offset + limit,
        "members": page,
    }
