"""Session-owned Goal iteration intake; no recovery of old execution."""

import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from app.execution_dependencies.resources import ExecutionResources
from app.execution_dependencies.runtime import capture_snapshot
from app.infrastructure.database import DatabaseResources
from app.infrastructure.errors import DomainError
from app.infrastructure.transactions import transaction
from app.modules.agent.public import AgentService
from app.modules.identity_tenant.public import IdentityService
from app.modules.run.public import RunRuntime, SourceIdentity, SourceSection
from app.modules.session.public import GoalView, SessionService
from app.modules.tool.public import AgentToolResolutionScope
from app.modules.workspace.public import WorkspaceScope, WorkspaceSubject

logger = logging.getLogger(__name__)


def goal_instructions(goal: GoalView) -> str:
    return ("[Goal-mode execution]\n" + json.dumps({"objective": goal.objective, "progress": goal.progress}, ensure_ascii=False)
        + '\nSend user-visible messages with send_message. Finish execution with JSON '
          '{"goal":{"disposition":"continue","progress":"committed progress","wake_at":null}}. '
          'Choose disposition continue, wait, or achieved. '
          'For wait, wake_at must be a future timezone-qualified ISO timestamp. '
          'For missing essential human input use need_input and preserve this Run. '
          'Continue and wait request a new Run; achieved stops the Goal.')


class GoalInputs:
    def __init__(self, database: DatabaseResources, execution: ExecutionResources) -> None:
        self.database, self.execution = database, execution
        self.runtime: RunRuntime | None = None
        self.not_before = datetime.now(UTC)
        self._task: asyncio.Task[None] | None = None
        self.failures = 0

    async def start(self) -> None:
        if self._task is not None or self.runtime is None:
            raise RuntimeError("Goal intake requires one ready Runtime")
        self._task = asyncio.create_task(self._loop(), name="session-goal-intake")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except (DomainError, SQLAlchemyError) as error:
                self.failures += 1
                logger.warning("Goal intake failed: %s", type(error).__name__)
            await asyncio.sleep(0.25)

    async def tick(self) -> None:
        cursor = None
        while True:
            async with transaction(self.database.control_sessions) as tx:
                page = await SessionService(tx).goal_due(now=datetime.now(UTC), not_before=self.not_before,
                    after_session_id=cursor, limit=100)
                tenants = await IdentityService(tx).filter_enabled_tenant_ids(tenant_ids=tuple({g.tenant_id for g in page.goals}))
            if page.invalid_session_ids:
                self.failures += len(page.invalid_session_ids)
                logger.warning("Goal intake rejected %d invalid configurations", len(page.invalid_session_ids))
            for goal in page.goals:
                if goal.tenant_id in tenants:
                    await self._start_goal(goal)
            if not page.has_more:
                return
            cursor = page.next_after_id

    async def _start_goal(self, goal: GoalView) -> None:
        assert self.runtime is not None
        try:
            async with transaction(self.database.control_sessions) as tx:
                service = SessionService(tx)
                link = await service.prepare_goal_admission(tenant_id=goal.tenant_id, session_id=goal.session_id,
                    expected_link_id=goal.current_link_id, now=datetime.now(UTC))
                if link is None:
                    return
                context = await service.get_goal_context(tenant_id=goal.tenant_id, session_id=goal.session_id,
                    expected_link_id=link.id)
                accounts = await service.goal_accounts(tenant_id=goal.tenant_id, session_id=goal.session_id,
                    expected_link_id=link.id)
            async with transaction(self.database.control_sessions) as tx:
                agent = await AgentService(tx).get_for_agent_execution(tenant_id=goal.tenant_id, agent_id=goal.agent_id)
            model = await self.execution.model.resolve_configured_policy(tenant_id=goal.tenant_id, model_id=agent.model_id)
            scope = WorkspaceScope(goal.tenant_id, goal.agent_id, WorkspaceSubject("membership", goal.membership_id),
                run_id=uuid4(), allow_shared_memory_writes=False)
            snapshot = await capture_snapshot(self.execution, self.database, agent=agent, model=model, workspace=scope,
                tools=AgentToolResolutionScope(goal.tenant_id, goal.agent_id, "main", frozenset(accounts), accounts))
            snapshot = replace(snapshot, sources=snapshot.sources + (SourceSection("product_context", scope.output,
                f"goal:{goal.input_id}:link:{link.id}", goal_instructions(context.goal)),))
            await self.runtime.start(snapshot=snapshot, input=context.input.content,
                source=SourceIdentity("session", goal.session_id, str(link.id)))
        except (DomainError, SQLAlchemyError) as error:
            reason = error.code if isinstance(error, DomainError) else "persistence_failure"
            async with transaction(self.database.control_sessions) as tx:
                await SessionService(tx).fail_goal_admission(tenant_id=goal.tenant_id, session_id=goal.session_id,
                    expected_link_id=goal.current_link_id, reason=reason)
