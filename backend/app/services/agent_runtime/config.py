"""Typed rollout policy for the durable Agent Runtime."""

from dataclasses import dataclass
from typing import Literal
import uuid

from app.config import Settings, get_settings


SUPPORTED_RUNTIME_SOURCE_TYPES = frozenset(
    {"chat", "trigger", "task", "a2a", "heartbeat"}
)
SUPPORTED_RUNTIME_TYPES = frozenset({"legacy", "langgraph"})

RuntimeGateReason = Literal[
    "existing_langgraph_run",
    "existing_legacy_run",
    "agent_allowlist",
    "source_type",
    "global_flag",
]


class RuntimeConfigurationError(ValueError):
    """Runtime rollout configuration cannot be interpreted safely."""


@dataclass(frozen=True, slots=True)
class RuntimeGateDecision:
    """One auditable Runtime routing decision."""

    use_v2: bool
    reason: RuntimeGateReason


def _split_csv(raw_value: str, *, setting_name: str) -> tuple[str, ...]:
    value = raw_value.strip()
    if not value:
        return ()
    parts = tuple(part.strip() for part in value.split(","))
    if any(not part for part in parts):
        raise RuntimeConfigurationError(
            f"{setting_name} contains an empty comma-separated value"
        )
    return parts


def _parse_agent_ids(raw_value: str) -> frozenset[uuid.UUID]:
    parsed: set[uuid.UUID] = set()
    for value in _split_csv(
        raw_value,
        setting_name="AGENT_RUNTIME_V2_AGENT_IDS",
    ):
        try:
            parsed.add(uuid.UUID(value))
        except ValueError as exc:
            raise RuntimeConfigurationError(
                f"AGENT_RUNTIME_V2_AGENT_IDS contains invalid UUID {value!r}"
            ) from exc
    return frozenset(parsed)


def _parse_source_types(raw_value: str) -> frozenset[str]:
    parsed = frozenset(
        value.lower()
        for value in _split_csv(
            raw_value,
            setting_name="AGENT_RUNTIME_V2_SOURCE_TYPES",
        )
    )
    unknown = parsed - SUPPORTED_RUNTIME_SOURCE_TYPES
    if unknown:
        raise RuntimeConfigurationError(
            "AGENT_RUNTIME_V2_SOURCE_TYPES contains unsupported values: "
            + ", ".join(sorted(unknown))
        )
    return parsed


@dataclass(frozen=True, slots=True)
class RuntimeRolloutPolicy:
    """Parsed v2 rollout gates with deterministic precedence."""

    globally_enabled: bool
    agent_ids: frozenset[uuid.UUID]
    source_types: frozenset[str]

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "RuntimeRolloutPolicy":
        runtime_settings = settings or get_settings()
        return cls(
            globally_enabled=runtime_settings.AGENT_RUNTIME_V2_ENABLED,
            agent_ids=_parse_agent_ids(runtime_settings.AGENT_RUNTIME_V2_AGENT_IDS),
            source_types=_parse_source_types(
                runtime_settings.AGENT_RUNTIME_V2_SOURCE_TYPES
            ),
        )

    def decide(
        self,
        *,
        agent_id: uuid.UUID | None,
        source_type: str,
        existing_runtime_type: str | None = None,
    ) -> RuntimeGateDecision:
        """Choose v2 for a new Run or preserve an existing Run's runtime.

        Existing LangGraph Runs must remain resumable even after rollout flags
        are disabled. Existing legacy Runs are never switched mid-execution.
        For a new Run the precedence is Agent allowlist, source type, then the
        global flag.
        """
        if existing_runtime_type is not None:
            if existing_runtime_type not in SUPPORTED_RUNTIME_TYPES:
                raise RuntimeConfigurationError(
                    f"Unsupported existing runtime_type {existing_runtime_type!r}"
                )
            if existing_runtime_type == "langgraph":
                return RuntimeGateDecision(True, "existing_langgraph_run")
            return RuntimeGateDecision(False, "existing_legacy_run")

        normalized_source_type = source_type.strip().lower()
        if normalized_source_type not in SUPPORTED_RUNTIME_SOURCE_TYPES:
            raise RuntimeConfigurationError(
                f"Unsupported Runtime source_type {source_type!r}"
            )
        if agent_id is not None and agent_id in self.agent_ids:
            return RuntimeGateDecision(True, "agent_allowlist")
        if normalized_source_type in self.source_types:
            return RuntimeGateDecision(True, "source_type")
        return RuntimeGateDecision(self.globally_enabled, "global_flag")


def decide_runtime_v2(
    *,
    agent_id: uuid.UUID | None,
    source_type: str,
    existing_runtime_type: str | None = None,
    settings: Settings | None = None,
) -> RuntimeGateDecision:
    """Resolve settings and return one Runtime gate decision."""
    return RuntimeRolloutPolicy.from_settings(settings).decide(
        agent_id=agent_id,
        source_type=source_type,
        existing_runtime_type=existing_runtime_type,
    )
