"""Shared directory lookup for agent tools and HTTP APIs."""

import uuid
from typing import Any, Literal

from sqlalchemy import case, exists, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import evaluate_roster_agent_visibility, evaluate_roster_human_visibility
from app.models.agent import Agent as AgentModel, AgentPermission
from app.models.identity import IdentityProvider
from app.models.org import AgentAgentRelationship, OrgDepartment, OrgMember
from app.models.user import User as UserModel

DirectoryMemberType = Literal["all", "agent", "human"]


class DirectoryQueryError(ValueError):
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


def format_roster_agent(
    source_agent: AgentModel,
    target_agent: AgentModel,
    *,
    authorized_custom_target: bool = False,
) -> dict | None:
    visibility = evaluate_roster_agent_visibility(
        source_agent,
        target_agent,
        authorized_custom_target=authorized_custom_target,
    )
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
    *,
    authorized_custom_human: bool = False,
) -> dict | None:
    visibility = evaluate_roster_human_visibility(
        source_agent,
        member,
        authorized_custom_human=authorized_custom_human,
    )
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
        raise DirectoryQueryError("invalid_target_member_id", "target_member_id must be a valid UUID") from exc


def _validate_member_type(member_type: str) -> DirectoryMemberType:
    normalized = (member_type or "all").strip().lower()
    if normalized not in {"all", "agent", "human"}:
        raise DirectoryQueryError("invalid_member_type", "member_type must be all, agent, or human")
    return normalized  # type: ignore[return-value]


def _validate_pagination(limit: int, offset: int, max_limit: int) -> None:
    if limit < 1 or limit > max_limit:
        raise DirectoryQueryError("invalid_limit", f"limit must be between 1 and {max_limit}")
    if offset < 0:
        raise DirectoryQueryError("invalid_offset", "offset must be greater than or equal to 0")


def _custom_agent_authorized_condition(source_agent_id: uuid.UUID):
    return exists().where(
        AgentAgentRelationship.agent_id == source_agent_id,
        AgentAgentRelationship.target_agent_id == AgentModel.id,
    )


def _custom_human_authorized_condition(source: AgentModel):
    return or_(
        OrgMember.user_id == source.creator_id,
        exists().where(
            UserModel.id == OrgMember.user_id,
            UserModel.tenant_id == source.tenant_id,
            UserModel.is_active == True,  # noqa: E712
            UserModel.role.in_(["platform_admin", "org_admin"]),
        ),
        exists().where(
            AgentPermission.agent_id == source.id,
            AgentPermission.scope_type == "user",
            AgentPermission.scope_id == OrgMember.user_id,
            AgentPermission.access_level.in_(["use", "manage"]),
        ),
    )


def _agent_directory_conditions(
    source: AgentModel,
    *,
    source_mode: str,
    query: str,
    include_uncontactable: bool,
) -> list:
    conditions = [
        AgentModel.tenant_id == source.tenant_id,
        AgentModel.id != source.id,
    ]
    if source_mode == "private":
        conditions.extend([
            AgentModel.access_mode == "private",
            AgentModel.creator_id == source.creator_id,
        ])
    else:
        conditions.append(or_(
            AgentModel.access_mode == "company",
            (
                (AgentModel.access_mode == "custom")
                & _custom_agent_authorized_condition(source.id)
            ),
        ))
    if query:
        conditions.append(or_(
            AgentModel.name.ilike(f"%{query}%"),
            AgentModel.role_description.ilike(f"%{query}%"),
        ))
    if not include_uncontactable:
        conditions.extend([
            AgentModel.status.in_(["running", "idle"]),
            AgentModel.is_expired == False,  # noqa: E712
        ])
    return conditions


def _human_directory_conditions(
    source: AgentModel,
    *,
    source_mode: str,
    query: str,
    target_member_uuid: uuid.UUID | None,
    include_uncontactable: bool,
) -> list:
    conditions = [OrgMember.tenant_id == source.tenant_id]
    if target_member_uuid:
        conditions.append(OrgMember.id == target_member_uuid)
    if source_mode == "private":
        conditions.append(OrgMember.user_id == source.creator_id)
    elif source_mode == "custom":
        conditions.append(_custom_human_authorized_condition(source))
    if query and not target_member_uuid:
        conditions.append(or_(
            OrgMember.name.ilike(f"%{query}%"),
            OrgMember.title.ilike(f"%{query}%"),
            OrgMember.department_path.ilike(f"%{query}%"),
        ))
    if not include_uncontactable:
        conditions.append(OrgMember.status == "active")
    return conditions


async def is_custom_agent_target_authorized(
    db: AsyncSession,
    *,
    source_agent_id: uuid.UUID,
    target_agent_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        select(AgentAgentRelationship.id)
        .where(
            AgentAgentRelationship.agent_id == source_agent_id,
            AgentAgentRelationship.target_agent_id == target_agent_id,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def is_custom_human_authorized(
    db: AsyncSession,
    *,
    source: AgentModel,
    member: OrgMember,
) -> bool:
    user_id = getattr(member, "user_id", None)
    if not user_id:
        return False
    if user_id == getattr(source, "creator_id", None):
        return True
    result = await db.execute(
        select(UserModel.role, AgentPermission.id)
        .outerjoin(
            AgentPermission,
            (AgentPermission.agent_id == source.id)
            & (AgentPermission.scope_type == "user")
            & (AgentPermission.scope_id == UserModel.id)
            & (AgentPermission.access_level.in_(["use", "manage"])),
        )
        .where(
            UserModel.id == user_id,
            UserModel.tenant_id == source.tenant_id,
            UserModel.is_active == True,  # noqa: E712
        )
        .limit(1)
    )
    row = result.first()
    if not row:
        return False
    role, permission_id = row
    return role in ("platform_admin", "org_admin") or permission_id is not None


async def query_agent_directory(
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
        raise DirectoryQueryError(
            "invalid_member_type",
            "target_member_id can only be used with member_type human or all",
        )

    fetch_size = limit + 1
    members: list[dict] = []

    source = (await db.execute(select(AgentModel).where(AgentModel.id == source_agent_id))).scalar_one_or_none()
    if not source:
        raise DirectoryQueryError("source_agent_not_found", "Source agent was not found.", status_code=404)

    source_mode = getattr(source, "access_mode", None) or "company"

    if member_type == "all" and not target_member_uuid:
        agent_conditions = _agent_directory_conditions(
            source,
            source_mode=source_mode,
            query=query,
            include_uncontactable=include_uncontactable,
        )
        human_conditions = _human_directory_conditions(
            source,
            source_mode=source_mode,
            query=query,
            target_member_uuid=None,
            include_uncontactable=include_uncontactable,
        )
        agent_contact_rank = case(
            (
                (AgentModel.status.not_in(["running", "idle"])) | (AgentModel.is_expired == True),  # noqa: E712
                1,
            ),
            else_=0,
        )
        human_contact_rank = case((OrgMember.status != "active", 1), else_=0)
        directory_rows = union_all(
            select(
                literal("agent").label("directory_member_type"),
                AgentModel.id.label("directory_member_id"),
                agent_contact_rank.label("contact_rank"),
                literal(0).label("type_rank"),
                AgentModel.name.label("sort_name"),
                AgentModel.created_at.label("sort_time"),
            ).where(*agent_conditions),
            select(
                literal("human").label("directory_member_type"),
                OrgMember.id.label("directory_member_id"),
                human_contact_rank.label("contact_rank"),
                literal(1).label("type_rank"),
                OrgMember.name.label("sort_name"),
                OrgMember.synced_at.label("sort_time"),
            ).where(*human_conditions),
        ).subquery()

        page_rows = (await db.execute(
            select(
                directory_rows.c.directory_member_type,
                directory_rows.c.directory_member_id,
            )
            .order_by(
                directory_rows.c.contact_rank.asc(),
                directory_rows.c.sort_name.asc(),
                directory_rows.c.type_rank.asc(),
                directory_rows.c.sort_time.asc(),
            )
            .offset(offset)
            .limit(fetch_size)
        )).all()
        page_entries = page_rows[:limit]
        agent_ids = [member_id for member_type_value, member_id in page_entries if member_type_value == "agent"]
        human_ids = [member_id for member_type_value, member_id in page_entries if member_type_value == "human"]

        agents_by_id: dict[uuid.UUID, AgentModel] = {}
        if agent_ids:
            agent_detail_result = await db.execute(select(AgentModel).where(AgentModel.id.in_(agent_ids)))
            agents_by_id = {agent.id: agent for agent in agent_detail_result.scalars().all()}

        humans_by_id: dict[uuid.UUID, tuple[OrgMember, IdentityProvider | None, OrgDepartment | None, UserModel | None]] = {}
        if human_ids:
            human_detail_result = await db.execute(
                select(OrgMember, IdentityProvider, OrgDepartment, UserModel)
                .outerjoin(IdentityProvider, OrgMember.provider_id == IdentityProvider.id)
                .outerjoin(OrgDepartment, OrgMember.department_id == OrgDepartment.id)
                .outerjoin(UserModel, OrgMember.user_id == UserModel.id)
                .where(OrgMember.id.in_(human_ids))
            )
            humans_by_id = {member.id: (member, provider, department, platform_user) for member, provider, department, platform_user in human_detail_result.all()}

        for member_type_value, member_id in page_entries:
            if member_type_value == "agent":
                target_agent = agents_by_id.get(member_id)
                if not target_agent:
                    continue
                payload = format_roster_agent(
                    source,
                    target_agent,
                    authorized_custom_target=(getattr(target_agent, "access_mode", None) == "custom"),
                )
            else:
                human_row = humans_by_id.get(member_id)
                if not human_row:
                    continue
                member, provider, department, platform_user = human_row
                payload = format_roster_human(
                    source,
                    member,
                    provider,
                    department,
                    platform_user,
                    authorized_custom_human=(source_mode == "custom"),
                )
            if payload and (include_uncontactable or payload["can_contact"]):
                members.append(payload)

        return {
            "ok": True,
            "source_agent_id": str(source_agent_id),
            "query": query,
            "member_type": member_type,
            "include_uncontactable": include_uncontactable,
            "returned_count": len(members),
            "limit": limit,
            "offset": offset,
            "has_more": len(page_rows) > limit,
            "members": members,
        }

    if member_type == "agent" and not target_member_uuid:
        agent_conditions = _agent_directory_conditions(
            source,
            source_mode=source_mode,
            query=query,
            include_uncontactable=include_uncontactable,
        )

        agent_result = await db.execute(
            select(AgentModel)
            .where(*agent_conditions)
            .order_by(AgentModel.name.asc(), AgentModel.created_at.asc())
            .offset(offset)
            .limit(fetch_size)
        )
        agent_rows = agent_result.scalars().all()
        for target_agent in agent_rows[:limit]:
            payload = format_roster_agent(
                source,
                target_agent,
                authorized_custom_target=(getattr(target_agent, "access_mode", None) == "custom"),
            )
            if payload and (include_uncontactable or payload["can_contact"]):
                members.append(payload)
        return {
            "ok": True,
            "source_agent_id": str(source_agent_id),
            "query": query,
            "member_type": member_type,
            "include_uncontactable": include_uncontactable,
            "returned_count": len(members),
            "limit": limit,
            "offset": offset,
            "has_more": len(agent_rows) > limit,
            "members": members,
        }

    if member_type in {"all", "human"}:
        human_conditions = _human_directory_conditions(
            source,
            source_mode=source_mode,
            query=query,
            target_member_uuid=target_member_uuid,
            include_uncontactable=include_uncontactable,
        )

        human_result = await db.execute(
            select(OrgMember, IdentityProvider, OrgDepartment, UserModel)
            .outerjoin(IdentityProvider, OrgMember.provider_id == IdentityProvider.id)
            .outerjoin(OrgDepartment, OrgMember.department_id == OrgDepartment.id)
            .outerjoin(UserModel, OrgMember.user_id == UserModel.id)
            .where(*human_conditions)
            .order_by(OrgMember.name.asc(), OrgMember.synced_at.asc())
            .offset(0 if target_member_uuid else offset)
            .limit(fetch_size)
        )
        human_rows = human_result.all()
        for member, provider, department, platform_user in human_rows[:limit]:
            payload = format_roster_human(
                source,
                member,
                provider,
                department,
                platform_user,
                authorized_custom_human=(source_mode == "custom"),
            )
            if payload and (include_uncontactable or payload["can_contact"]):
                members.append(payload)
    return {
        "ok": True,
        "source_agent_id": str(source_agent_id),
        "query": query,
        "member_type": member_type,
        "include_uncontactable": include_uncontactable,
        "returned_count": len(members),
        "limit": limit,
        "offset": offset,
        "has_more": len(human_rows) > limit,
        "members": members,
    }
