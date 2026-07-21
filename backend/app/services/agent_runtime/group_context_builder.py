"""Capture the immutable group-specific portion of a new Runtime Run."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models.agent import Agent
from app.models.audit import ChatMessage
from app.models.org import OrgMember
from app.models.participant import Participant
from app.models.user import User
from app.services import group_chat_service, group_file_service
from app.services.agent_runtime.context_builder import ContextBuildError
from app.services.agent_runtime.state import JsonObject
from app.services.group_chat_service import GroupChatServiceError
from app.services.group_file_service import GroupFileServiceError


_ACTIVE_AGENT_STATUSES = frozenset({"creating", "running", "idle"})


@dataclass(frozen=True, slots=True)
class GroupContextCapture:
    """Validated group input and enriched recent message snapshots."""

    initial_input: JsonObject
    pending_messages: tuple[JsonObject, ...]
    recent_messages: tuple[JsonObject, ...]


def _uuid_value(value: object, *, field: str) -> uuid.UUID:
    if not isinstance(value, str):
        raise ContextBuildError(
            "invalid_group_runtime_input",
            f"{field} must be a UUID string",
        )
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ContextBuildError(
            "invalid_group_runtime_input",
            f"{field} must be a UUID string",
        ) from exc


def _bounded_text(content: str, *, limit: int, source: str) -> JsonObject:
    truncated = len(content) > limit
    return {
        "source": source,
        "content": content[:limit],
        "truncated": truncated,
        "original_chars": len(content),
    }


def _participant_json(participant: Participant) -> JsonObject:
    return {
        "participant_id": str(participant.id),
        "participant_type": participant.type,
        "participant_ref_id": str(participant.ref_id),
        "display_name": participant.display_name,
    }


class GroupContextBuilder:
    """Resolve group facts once and freeze them in the first LangGraph checkpoint."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def _enrich_recent_messages(
        self,
        db: AsyncSession,
        messages: Sequence[Mapping[str, object]],
    ) -> tuple[JsonObject, ...]:
        participant_ids = {
            _uuid_value(message.get("participant_id"), field="recent participant_id")
            for message in messages
            if message.get("participant_id") is not None
        }
        participants: dict[uuid.UUID, Participant] = {}
        if participant_ids:
            result = await db.execute(
                select(Participant).where(Participant.id.in_(participant_ids))
            )
            participants = {
                participant.id: participant for participant in result.scalars().all()
            }

        output = []
        for message in messages:
            enriched = deepcopy(dict(message))
            raw_participant_id = message.get("participant_id")
            participant = (
                participants.get(_uuid_value(raw_participant_id, field="recent participant_id"))
                if raw_participant_id is not None
                else None
            )
            enriched["sender_name"] = (
                participant.display_name if participant is not None else None
            )
            enriched["sender_type"] = (
                participant.type if participant is not None else None
            )
            output.append(enriched)
        return tuple(output)

    async def capture(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        agent_id: uuid.UUID | None,
        initial_input: Mapping[str, object],
        pending_messages: Sequence[Mapping[str, object]] = (),
        recent_messages: Sequence[Mapping[str, object]],
    ) -> GroupContextCapture:
        """Add group context only to concrete Agent Runs, never Planning roots."""
        raw_group_id = initial_input.get("group_id")
        raw_target_participant_id = initial_input.get("target_participant_id")
        if raw_group_id is None or raw_target_participant_id is None:
            return GroupContextCapture(
                initial_input=deepcopy(dict(initial_input)),
                pending_messages=tuple(
                    deepcopy(dict(message)) for message in pending_messages
                ),
                recent_messages=tuple(deepcopy(dict(message)) for message in recent_messages),
            )
        if agent_id is None:
            raise ContextBuildError(
                "invalid_group_runtime_input",
                "A concrete group Agent Run requires agent_id",
            )

        group_id = _uuid_value(raw_group_id, field="group_id")
        target_participant_id = _uuid_value(
            raw_target_participant_id,
            field="target_participant_id",
        )
        sender_participant_id = _uuid_value(
            initial_input.get("sender_participant_id"),
            field="sender_participant_id",
        )
        message_id = _uuid_value(initial_input.get("message_id"), field="message_id")
        payload_session_id = _uuid_value(
            initial_input.get("session_id"),
            field="session_id",
        )
        if payload_session_id != session_id:
            raise ContextBuildError(
                "invalid_group_runtime_input",
                "Group payload session_id does not match the Run session",
            )

        try:
            session = await group_chat_service.authorize_group_session(
                db,
                tenant_id=tenant_id,
                group_id=group_id,
                session_id=session_id,
                participant_id=target_participant_id,
            )
            group, target_membership, target_participant = (
                await group_chat_service.authorize_group_member(
                    db,
                    tenant_id=tenant_id,
                    group_id=group_id,
                    participant_id=target_participant_id,
                )
            )
            _, _, sender_participant = await group_chat_service.authorize_group_member(
                db,
                tenant_id=tenant_id,
                group_id=group_id,
                participant_id=sender_participant_id,
            )
        except GroupChatServiceError as exc:
            raise ContextBuildError(exc.code, str(exc)) from exc

        if (
            session.session_type != "group"
            or session.group_id != group_id
            or target_participant.type != "agent"
            or target_participant.ref_id != agent_id
        ):
            raise ContextBuildError(
                "invalid_group_runtime_scope",
                "Group session, target participant, and Runtime Agent do not match",
            )

        message_result = await db.execute(
            select(ChatMessage).where(
                ChatMessage.id == message_id,
                ChatMessage.conversation_id == str(session_id),
            )
        )
        trigger_message = message_result.scalar_one_or_none()
        if trigger_message is None or trigger_message.created_at is None:
            raise ContextBuildError(
                "group_trigger_message_unavailable",
                "Group trigger message is not available in this session",
            )
        if trigger_message.participant_id != sender_participant_id:
            raise ContextBuildError(
                "invalid_group_runtime_scope",
                "Group trigger sender does not match the Runtime payload",
            )

        authoritative_mentions = trigger_message.mentions
        if not isinstance(authoritative_mentions, list) or not all(
            isinstance(mention, Mapping) for mention in authoritative_mentions
        ):
            raise ContextBuildError(
                "invalid_group_runtime_scope",
                "Group trigger has no authoritative mention snapshot",
            )
        target_mention = next(
            (
                mention
                for mention in authoritative_mentions
                if mention.get("participant_id") == str(target_participant_id)
            ),
            None,
        )
        if (
            target_mention is None
            or target_mention.get("participant_ref_id") != str(agent_id)
            or target_mention.get("participant_type") != "agent"
            or target_mention.get("valid") is not True
            or target_mention.get("triggers_agent") is not True
        ):
            raise ContextBuildError(
                "invalid_group_runtime_scope",
                "Runtime target is not an authoritative Agent mention on the trigger message",
            )

        agent_result = await db.execute(
            select(Agent).where(
                Agent.id == agent_id,
                Agent.tenant_id == tenant_id,
                Agent.status.in_(_ACTIVE_AGENT_STATUSES),
                Agent.is_expired.is_(False),
            )
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            raise ContextBuildError(
                "agent_unavailable",
                "Group Runtime Agent is unavailable",
            )

        sender_profile: JsonObject = _participant_json(sender_participant)
        if sender_participant.type == "user":
            user_result = await db.execute(
                select(User).where(
                    User.id == sender_participant.ref_id,
                    User.tenant_id == tenant_id,
                    User.is_active.is_(True),
                )
            )
            user = user_result.scalar_one_or_none()
            if user is None:
                raise ContextBuildError(
                    "group_sender_invalid",
                    "Group message sender is no longer an active tenant user",
                )
            org_result = await db.execute(
                select(OrgMember)
                .where(
                    OrgMember.user_id == user.id,
                    OrgMember.tenant_id == tenant_id,
                    OrgMember.status == "active",
                )
                .limit(1)
            )
            org_member = org_result.scalar_one_or_none()
            sender_profile["title"] = (
                org_member.title if org_member is not None else user.title
            )
            sender_profile["department"] = (
                org_member.department_path if org_member is not None else ""
            )

        try:
            announcement = await group_file_service.read_announcement(
                db,
                tenant_id=tenant_id,
                group_id=group_id,
                actor_participant_id=target_participant_id,
            )
            memory = await group_file_service.read_agent_memory(
                db,
                tenant_id=tenant_id,
                group_id=group_id,
                actor_participant_id=target_participant_id,
                agent_id=agent_id,
            )
            workspace_entries = await group_file_service.index_workspace(
                db,
                tenant_id=tenant_id,
                group_id=group_id,
                actor_participant_id=target_participant_id,
                limit=self._settings.GROUP_CONTEXT_WORKSPACE_MAX_ENTRIES,
            )
        except (GroupChatServiceError, GroupFileServiceError) as exc:
            raise ContextBuildError(exc.code, str(exc)) from exc

        planning_hint: JsonObject = {}
        raw_mode = initial_input.get("mode")
        if isinstance(raw_mode, str) and raw_mode.strip():
            planning_hint["mode"] = raw_mode.strip()
        raw_plan_prompt = initial_input.get("plan_prompt")
        if isinstance(raw_plan_prompt, str) and raw_plan_prompt.strip():
            planning_hint["plan_prompt"] = raw_plan_prompt.strip()
        raw_responsibility = initial_input.get("current_responsibility")
        if isinstance(raw_responsibility, str) and raw_responsibility.strip():
            planning_hint["current_responsibility"] = raw_responsibility.strip()

        group_context: JsonObject = {
            "trigger": {
                "message_id": str(trigger_message.id),
                "content": trigger_message.content,
                "created_at": trigger_message.created_at.isoformat(),
                "sender": sender_profile,
                "mention_targets": deepcopy(authoritative_mentions),
                "target_participant_id": str(target_participant_id),
            },
            "agent": {
                "agent_id": str(agent.id),
                "participant_id": str(target_participant.id),
                "name": agent.name,
                "membership_role": target_membership.role,
            },
            "group": {
                "group_id": str(group.id),
                "name": group.name,
                "description": group.description or "",
            },
            "session": {
                "session_id": str(session.id),
                "title": session.title,
                "is_primary": bool(session.is_primary),
            },
            "announcement": _bounded_text(
                announcement.content,
                limit=self._settings.GROUP_CONTEXT_ANNOUNCEMENT_MAX_CHARS,
                source="group announcement",
            ),
            "agent_group_memory": _bounded_text(
                memory.content,
                limit=self._settings.GROUP_CONTEXT_MEMORY_MAX_CHARS,
                source=f"group memory for Agent {agent.id}",
            ),
            "workspace_index": [
                {
                    "path": entry.path,
                    "name": entry.name,
                    "is_dir": entry.is_dir,
                    "size": entry.size,
                    "modified_at": entry.modified_at,
                }
                for entry in workspace_entries
            ],
            "workspace_index_may_be_truncated": (
                len(workspace_entries)
                >= self._settings.GROUP_CONTEXT_WORKSPACE_MAX_ENTRIES
            ),
            "planning_hint": planning_hint,
        }
        captured_input = deepcopy(dict(initial_input))
        captured_input["group_context"] = group_context
        return GroupContextCapture(
            initial_input=captured_input,
            pending_messages=await self._enrich_recent_messages(db, pending_messages),
            recent_messages=await self._enrich_recent_messages(db, recent_messages),
        )


__all__ = ["GroupContextBuilder", "GroupContextCapture"]
