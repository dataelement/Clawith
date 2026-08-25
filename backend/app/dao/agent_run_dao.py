"""DAO for AgentRun, AgentRunCommand, and AgentRunEvent models."""

import uuid
from collections.abc import Sequence

from sqlalchemy import select

from app.dao.base import TenantScopedBaseDAO
from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.models.agent_run_event import AgentRunEvent


class AgentRunDAO(TenantScopedBaseDAO[AgentRun]):
    """Tenant-scoped DAO for AgentRun, AgentRunCommand, and AgentRunEvent.

    C1 INVARIANT: This DAO manages product-side run records only.
    Execution lifecycle state (graph checkpoints) must NEVER be read or
    written here — it belongs exclusively to LangGraph checkpointers.
    """

    def __init__(self) -> None:
        super().__init__(AgentRun)

    # ------------------------------------------------------------------
    # AgentRun queries
    # ------------------------------------------------------------------

    async def get_run(self, run_id: uuid.UUID) -> AgentRun | None:
        """Fetch a run record by ID, scoped to current tenant."""
        tenant_id = self._require_tenant_id()
        async with self.session(readonly=True) as db:
            stmt = select(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.tenant_id == tenant_id,
            )
            return (await db.execute(stmt)).scalar_one_or_none()

    async def get_run_by_thread(self, runtime_thread_id: str) -> AgentRun | None:
        """Fetch a run by LangGraph thread_id, scoped to current tenant."""
        tenant_id = self._require_tenant_id()
        async with self.session(readonly=True) as db:
            stmt = select(AgentRun).where(
                AgentRun.runtime_thread_id == runtime_thread_id,
                AgentRun.tenant_id == tenant_id,
            ).order_by(AgentRun.created_at.desc()).limit(1)
            return (await db.execute(stmt)).scalar_one_or_none()

    async def list_runs_by_agent(
        self,
        agent_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 50,
    ) -> Sequence[AgentRun]:
        """List runs for an agent in the current tenant."""
        tenant_id = self._require_tenant_id()
        async with self.session(readonly=True) as db:
            stmt = (
                select(AgentRun)
                .where(
                    AgentRun.agent_id == agent_id,
                    AgentRun.tenant_id == tenant_id,
                )
                .order_by(AgentRun.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return (await db.execute(stmt)).scalars().all()

    async def list_runs_by_session(
        self,
        session_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 50,
    ) -> Sequence[AgentRun]:
        """List runs for a chat session in the current tenant."""
        tenant_id = self._require_tenant_id()
        async with self.session(readonly=True) as db:
            stmt = (
                select(AgentRun)
                .where(
                    AgentRun.session_id == session_id,
                    AgentRun.tenant_id == tenant_id,
                )
                .order_by(AgentRun.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return (await db.execute(stmt)).scalars().all()

    async def get_run_by_source_execution(
        self, source_type: str, source_execution_id: str
    ) -> AgentRun | None:
        """Fetch a run by its idempotency source_execution_id (global unique)."""
        async with self.session(readonly=True) as db:
            stmt = select(AgentRun).where(
                AgentRun.source_type == source_type,
                AgentRun.source_execution_id == source_execution_id,
            ).limit(1)
            return (await db.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------
    # AgentRunCommand queries
    # ------------------------------------------------------------------

    async def get_pending_command(
        self, run_id: uuid.UUID, command_type: str | None = None
    ) -> AgentRunCommand | None:
        """Return the oldest pending command for a run."""
        async with self.session(readonly=True) as db:
            stmt = select(AgentRunCommand).where(
                AgentRunCommand.run_id == run_id,
                AgentRunCommand.status == "pending",
            )
            if command_type is not None:
                stmt = stmt.where(AgentRunCommand.command_type == command_type)
            stmt = stmt.order_by(AgentRunCommand.created_at.asc()).limit(1)
            return (await db.execute(stmt)).scalar_one_or_none()

    async def list_commands_for_run(
        self, run_id: uuid.UUID, *, skip: int = 0, limit: int = 50
    ) -> Sequence[AgentRunCommand]:
        """List all commands for a given run."""
        async with self.session(readonly=True) as db:
            stmt = (
                select(AgentRunCommand)
                .where(AgentRunCommand.run_id == run_id)
                .order_by(AgentRunCommand.created_at.asc())
                .offset(skip)
                .limit(limit)
            )
            return (await db.execute(stmt)).scalars().all()

    async def create_command(
        self,
        *,
        run_id: uuid.UUID,
        tenant_id: uuid.UUID,
        command_type: str,
        payload: dict,
        idempotency_key: str,
        actor_user_id: uuid.UUID | None = None,
        actor_agent_id: uuid.UUID | None = None,
    ) -> AgentRunCommand:
        """Insert a new command for a run (idempotency_key guards duplicates)."""
        async with self.session() as db:
            cmd = AgentRunCommand(
                run_id=run_id,
                tenant_id=tenant_id,
                command_type=command_type,
                payload=payload,
                idempotency_key=idempotency_key,
                actor_user_id=actor_user_id,
                actor_agent_id=actor_agent_id,
                status="pending",
            )
            db.add(cmd)
            await db.flush()
            return cmd

    async def get_command_by_idempotency_key(
        self, run_id: uuid.UUID, idempotency_key: str
    ) -> AgentRunCommand | None:
        """Check if a command with the given idempotency key already exists."""
        async with self.session(readonly=True) as db:
            stmt = select(AgentRunCommand).where(
                AgentRunCommand.run_id == run_id,
                AgentRunCommand.idempotency_key == idempotency_key,
            ).limit(1)
            return (await db.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------
    # AgentRunEvent queries
    # ------------------------------------------------------------------

    async def list_events_for_run(
        self,
        run_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[AgentRunEvent]:
        """List product-side delivery events for a run."""
        tenant_id = self._require_tenant_id()
        async with self.session(readonly=True) as db:
            stmt = (
                select(AgentRunEvent)
                .where(
                    AgentRunEvent.run_id == run_id,
                    AgentRunEvent.tenant_id == tenant_id,
                )
                .order_by(AgentRunEvent.created_at.asc())
                .offset(skip)
                .limit(limit)
            )
            return (await db.execute(stmt)).scalars().all()


agent_run_dao = AgentRunDAO()
