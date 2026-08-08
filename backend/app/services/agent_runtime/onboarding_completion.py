"""Durably advance Web onboarding from completed Runtime checkpoints."""

from __future__ import annotations

import uuid

from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
    RuntimeSessionFactory,
)
from app.services.onboarding import (
    PHASE_COMPLETED,
    PHASE_CUSTOM_BOUNDARIES,
    PHASE_CUSTOM_STYLE,
    PHASE_GREETED,
    PHASE_TEMPLATE_FOCUS,
    mark_onboarding_phase,
)


_PHASES = frozenset(
    {
        PHASE_GREETED,
        PHASE_CUSTOM_STYLE,
        PHASE_CUSTOM_BOUNDARIES,
        PHASE_TEMPLATE_FOCUS,
        PHASE_COMPLETED,
    }
)


class OnboardingRuntimeCompletionError(RuntimeError):
    """A completed onboarding Run contains invalid durable metadata."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class OnboardingRuntimeCompletionHandler:
    """Advance onboarding even when the initiating WebSocket disconnected."""

    def __init__(self, *, session_factory: RuntimeSessionFactory) -> None:
        self._session_factory = session_factory

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        checkpoint: CheckpointObservation,
    ) -> None:
        initial_input = checkpoint.state["snapshots"].initial_input
        target_phase = initial_input.get("onboarding_target_phase")
        if target_phase is None:
            return
        if run.source_type != "chat":
            raise OnboardingRuntimeCompletionError(
                "invalid_onboarding_source",
                "onboarding metadata is only valid on Chat Runs",
            )
        if checkpoint.state["lifecycle"]["status"] != "completed":
            return
        if target_phase not in _PHASES:
            raise OnboardingRuntimeCompletionError(
                "invalid_onboarding_phase",
                "completed onboarding Run has an invalid target phase",
            )
        try:
            agent_id = uuid.UUID(run.agent_id or "")
            user_id = uuid.UUID(str(initial_input.get("user_id", "")))
        except ValueError as exc:
            raise OnboardingRuntimeCompletionError(
                "invalid_onboarding_identity",
                "completed onboarding Run has invalid Agent or user identity",
            ) from exc

        async with self._session_factory() as db:
            await mark_onboarding_phase(
                db,
                agent_id,
                user_id,
                str(target_phase),
            )


__all__ = [
    "OnboardingRuntimeCompletionError",
    "OnboardingRuntimeCompletionHandler",
]
