"""Normalize model limits and calculate per-request token budgets.

Provider discovery belongs outside this module. The request path consumes only
the semantic fields cached on :class:`LLMModel`. When both input capabilities
are unknown, it uses the configured shared-context fallback instead of blocking
otherwise valid model requests.
"""

from dataclasses import dataclass
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models.llm import LLMModel
from app.services.agent_runtime.runtime_model_settings import resolve_runtime_model_settings


class ModelCapabilityError(RuntimeError):
    """A model cannot provide a safe input budget for the requested call."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PlatformModelConfigurationError(RuntimeError):
    """A global Runtime model setting does not identify a usable platform model."""

    def __init__(self, setting_name: str, reason: str) -> None:
        super().__init__(f"{setting_name}: {reason}")
        self.setting_name = setting_name
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ResolvedModelCapabilities:
    """Cached limits after applying same-semantic administrator overrides."""

    context_window_tokens: int | None
    max_input_tokens: int | None
    max_output_tokens: int | None
    capability_source: str | None


@dataclass(frozen=True, slots=True)
class RuntimeTokenBudget:
    """Token limits for one concrete model request."""

    requested_max_output_tokens: int | None
    request_input_limit: int
    effective_runtime_budget: int
    compact_threshold: int


def _positive_optional(value: int | None, field_name: str) -> int | None:
    if value is not None and value <= 0:
        raise ModelCapabilityError(
            "invalid_capability",
            f"{field_name} must be greater than zero when configured",
        )
    return value


def _minimum_defined(*values: int | None) -> int | None:
    defined = [value for value in values if value is not None]
    return min(defined) if defined else None


def _legacy_output_limit(value: int | None) -> int | None:
    """Preserve caller compatibility: non-positive DB values mean unset."""
    return value if isinstance(value, int) and value > 0 else None


class ModelCapabilityResolver:
    """Resolve model semantics without performing provider I/O."""

    @staticmethod
    def require_native_tool_calling(model: LLMModel) -> None:
        """Require a concrete model row to be safe for an Agent tool Runtime."""
        model_label = getattr(model, "label", None) or model.model
        if model.supports_tool_calling is True:
            return
        if model.supports_tool_calling is False:
            raise ModelCapabilityError(
                "model_tool_calling_unsupported",
                (
                    f"Model {model_label!r} did not produce a valid "
                    "native tool call during its capability test."
                ),
            )
        raise ModelCapabilityError(
            "model_tool_calling_unverified",
            (
                f"Model {model_label!r} has not passed the native "
                "tool-calling capability test required by Agent Runtime."
            ),
        )

    @staticmethod
    def capabilities(
        model: LLMModel,
        *,
        settings: Settings | None = None,
    ) -> ResolvedModelCapabilities:
        """Apply same-semantic overrides, then the unknown-model fallback."""
        context_window_tokens = _positive_optional(
            model.context_window_tokens_override
            if model.context_window_tokens_override is not None
            else model.context_window_tokens,
            "context_window_tokens",
        )
        max_input_tokens = _positive_optional(
            model.max_input_tokens_override
            if model.max_input_tokens_override is not None
            else model.max_input_tokens,
            "max_input_tokens",
        )
        max_output_tokens = _legacy_output_limit(model.max_output_tokens)
        capability_source = model.capability_source
        if context_window_tokens is None and max_input_tokens is None:
            runtime_settings = settings or get_settings()
            context_window_tokens = runtime_settings.AGENT_RUNTIME_FALLBACK_CONTEXT_WINDOW_TOKENS
            capability_source = "runtime_config"
        return ResolvedModelCapabilities(
            context_window_tokens=context_window_tokens,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            capability_source=capability_source,
        )

    @classmethod
    def request_input_limit(
        cls,
        model: LLMModel,
        *,
        requested_max_output_tokens: int | None,
        settings: Settings | None = None,
    ) -> tuple[int, int | None]:
        """Return the safe input limit and effective output reservation.

        An independent input limit is never reduced by output tokens. A shared
        context window is reduced by the output limit for this request. If a
        shared window is the only available input capability, an unknown output
        reservation is unsafe and therefore rejected.
        """
        capabilities = cls.capabilities(model, settings=settings)
        request_output = _positive_optional(
            requested_max_output_tokens,
            "requested_max_output_tokens",
        )
        effective_output = _minimum_defined(request_output, capabilities.max_output_tokens)

        shared_input_limit: int | None = None
        if capabilities.context_window_tokens is not None:
            if effective_output is None:
                raise ModelCapabilityError(
                    "unknown_output_limit",
                    "a shared context window requires a request or model output limit",
                )
            shared_input_limit = capabilities.context_window_tokens - effective_output
            if shared_input_limit <= 0:
                raise ModelCapabilityError(
                    "invalid_request_budget",
                    "requested output tokens leave no room in the shared context window",
                )

        input_limit = _minimum_defined(capabilities.max_input_tokens, shared_input_limit)
        if input_limit is None:
            raise ModelCapabilityError(
                "unknown_input_limit",
                "model has neither an independent input limit nor a shared context window",
            )
        return input_limit, effective_output

    @classmethod
    def runtime_budget(
        cls,
        model: LLMModel,
        *,
        requested_max_output_tokens: int | None,
        static_prompt_tokens: int = 0,
        tool_schema_tokens: int = 0,
        reserved_runtime_tokens: int = 0,
        safety_margin_tokens: int = 0,
        compact_threshold_ratio: float = 0.85,
        settings: Settings | None = None,
    ) -> RuntimeTokenBudget:
        """Calculate the remaining Runtime budget for one model request."""
        components = {
            "static_prompt_tokens": static_prompt_tokens,
            "tool_schema_tokens": tool_schema_tokens,
            "reserved_runtime_tokens": reserved_runtime_tokens,
            "safety_margin_tokens": safety_margin_tokens,
        }
        for field_name, value in components.items():
            if value < 0:
                raise ModelCapabilityError(
                    "invalid_budget_component",
                    f"{field_name} must not be negative",
                )
        if not 0 < compact_threshold_ratio <= 1:
            raise ModelCapabilityError(
                "invalid_compact_threshold_ratio",
                "compact_threshold_ratio must be greater than zero and at most one",
            )

        input_limit, effective_output = cls.request_input_limit(
            model,
            requested_max_output_tokens=requested_max_output_tokens,
            settings=settings,
        )
        effective_budget = input_limit - sum(components.values())
        if effective_budget <= 0:
            raise ModelCapabilityError(
                "insufficient_runtime_budget",
                "static, tool, reserved, and safety budgets consume the model input limit",
            )
        return RuntimeTokenBudget(
            requested_max_output_tokens=effective_output,
            request_input_limit=input_limit,
            effective_runtime_budget=effective_budget,
            compact_threshold=int(effective_budget * compact_threshold_ratio),
        )


async def resolve_platform_model(
    db: AsyncSession,
    model_id: uuid.UUID | None,
    *,
    setting_name: str,
) -> LLMModel:
    """Resolve one enabled global platform model without any fallback."""
    if model_id is None:
        raise PlatformModelConfigurationError(setting_name, "is not configured")

    result = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
    model = result.scalar_one_or_none()
    if model is None:
        raise PlatformModelConfigurationError(setting_name, f"model {model_id} does not exist")
    if not model.enabled:
        raise PlatformModelConfigurationError(setting_name, f"model {model_id} is disabled")
    if model.tenant_id is not None:
        raise PlatformModelConfigurationError(
            setting_name,
            f"model {model_id} is tenant-scoped; a platform model is required",
        )
    return model


async def resolve_group_model(
    db: AsyncSession,
    model_id: uuid.UUID | None,
    *,
    tenant_id: uuid.UUID,
    setting_name: str,
) -> LLMModel:
    """Resolve one enabled model owned by the Group tenant or the platform."""
    if model_id is None:
        raise PlatformModelConfigurationError(setting_name, "is not configured")

    result = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
    model = result.scalar_one_or_none()
    if model is None:
        raise PlatformModelConfigurationError(setting_name, f"model {model_id} does not exist")
    if not model.enabled:
        raise PlatformModelConfigurationError(setting_name, f"model {model_id} is disabled")
    if model.tenant_id not in {None, tenant_id}:
        raise PlatformModelConfigurationError(
            setting_name,
            f"model {model_id} belongs to another tenant",
        )
    return model


async def resolve_multi_agent_compact_model(
    db: AsyncSession,
    settings: Settings | None = None,
    *,
    tenant_id: uuid.UUID,
) -> LLMModel:
    """Resolve the Group tenant's context model with environment fallback."""
    runtime_settings = settings or get_settings()
    configured = await resolve_runtime_model_settings(
        db,
        tenant_id=tenant_id,
        environment_planning_model_id=runtime_settings.MULTI_AGENT_PLANNING_MODEL_ID,
        environment_compact_model_id=runtime_settings.MULTI_AGENT_COMPACT_MODEL_ID,
    )
    return await resolve_group_model(
        db,
        configured.compact_model_id,
        tenant_id=tenant_id,
        setting_name="MULTI_AGENT_COMPACT_MODEL_ID",
    )


async def resolve_multi_agent_planning_model(
    db: AsyncSession,
    settings: Settings | None = None,
    *,
    tenant_id: uuid.UUID,
) -> LLMModel:
    """Resolve the Group tenant's planning model with environment fallback."""
    runtime_settings = settings or get_settings()
    configured = await resolve_runtime_model_settings(
        db,
        tenant_id=tenant_id,
        environment_planning_model_id=runtime_settings.MULTI_AGENT_PLANNING_MODEL_ID,
        environment_compact_model_id=runtime_settings.MULTI_AGENT_COMPACT_MODEL_ID,
    )
    return await resolve_group_model(
        db,
        configured.planning_model_id,
        tenant_id=tenant_id,
        setting_name="MULTI_AGENT_PLANNING_MODEL_ID",
    )
