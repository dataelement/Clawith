"""Pure tests for cached model capability and platform-model resolution."""

import uuid

import pytest

from app.config import Settings
from app.models.llm import LLMModel
from app.models.system_settings import SystemSetting
from app.services.agent_runtime.model_capabilities import (
    ModelCapabilityError,
    ModelCapabilityResolver,
    PlatformModelConfigurationError,
    resolve_multi_agent_compact_model,
    resolve_multi_agent_planning_model,
    resolve_platform_model,
)


def _model(**overrides: object) -> LLMModel:
    values: dict[str, object] = {
        "provider": "test",
        "model": "test-model",
        "api_key_encrypted": "secret",
        "label": "Test model",
        "enabled": True,
    }
    values.update(overrides)
    return LLMModel(**values)


class _Result:
    def __init__(self, model: LLMModel | SystemSetting | None) -> None:
        self.model = model

    def scalar_one_or_none(self) -> LLMModel | SystemSetting | None:
        return self.model

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[LLMModel | SystemSetting]:
        return [self.model] if self.model is not None else []


class _Session:
    def __init__(
        self,
        model: LLMModel | None,
        runtime_setting: SystemSetting | None = None,
    ) -> None:
        self.model = model
        self.runtime_setting = runtime_setting
        self.statements: list[object] = []

    async def execute(self, statement: object) -> _Result:
        self.statements.append(statement)
        if "system_settings" in str(statement):
            return _Result(self.runtime_setting)
        return _Result(self.model)


def test_llm_capability_columns_and_checks_are_declared() -> None:
    table = LLMModel.__table__
    for column_name in (
        "context_window_tokens",
        "context_window_tokens_override",
        "max_input_tokens",
        "max_input_tokens_override",
        "capability_source",
        "capability_checked_at",
        "supports_tool_calling",
        "tool_calling_capability_source",
        "tool_calling_checked_at",
        "tool_calling_error",
    ):
        assert table.c[column_name].nullable is True

    constraints = {constraint.name: str(constraint.sqltext) for constraint in table.constraints if constraint.name}
    assert "ck_llm_models_context_window_tokens_positive" in constraints
    assert "ck_llm_models_context_window_tokens_override_positive" in constraints
    assert "ck_llm_models_max_input_tokens_positive" in constraints
    assert "ck_llm_models_max_input_tokens_override_positive" in constraints
    assert "ck_llm_models_max_output_tokens_positive" not in constraints
    capability_source_check = constraints["ck_llm_models_capability_source"]
    for source in ("manual", "provider_api", "builtin_registry", "runtime_config"):
        assert source in capability_source_check


@pytest.mark.parametrize(
    ("supports_tool_calling", "error_code"),
    [
        (None, "model_tool_calling_unverified"),
        (False, "model_tool_calling_unsupported"),
    ],
)
def test_agent_runtime_requires_verified_native_tool_calling(
    supports_tool_calling: bool | None,
    error_code: str,
) -> None:
    model = _model(supports_tool_calling=supports_tool_calling)

    with pytest.raises(ModelCapabilityError) as exc_info:
        ModelCapabilityResolver.require_native_tool_calling(model)

    assert exc_info.value.code == error_code


def test_verified_native_tool_calling_is_accepted() -> None:
    ModelCapabilityResolver.require_native_tool_calling(
        _model(supports_tool_calling=True)
    )


def test_matching_overrides_win_without_changing_limit_semantics() -> None:
    model = _model(
        context_window_tokens=100_000,
        context_window_tokens_override=80_000,
        max_input_tokens=90_000,
        max_input_tokens_override=70_000,
        max_output_tokens=10_000,
        capability_source="provider_api",
    )

    capabilities = ModelCapabilityResolver.capabilities(model)
    budget = ModelCapabilityResolver.runtime_budget(
        model,
        requested_max_output_tokens=8_000,
        static_prompt_tokens=1_000,
        tool_schema_tokens=2_000,
        reserved_runtime_tokens=3_000,
        safety_margin_tokens=4_000,
    )

    assert capabilities.context_window_tokens == 80_000
    assert capabilities.max_input_tokens == 70_000
    assert capabilities.capability_source == "provider_api"
    assert budget.requested_max_output_tokens == 8_000
    assert budget.request_input_limit == 70_000
    assert budget.effective_runtime_budget == 60_000
    assert budget.compact_threshold == 51_000


def test_independent_input_limit_does_not_reserve_output_again() -> None:
    model = _model(max_input_tokens=100_000, max_output_tokens=16_000)

    budget = ModelCapabilityResolver.runtime_budget(
        model,
        requested_max_output_tokens=8_000,
    )

    assert budget.requested_max_output_tokens == 8_000
    assert budget.request_input_limit == 100_000


def test_shared_context_uses_smaller_request_and_model_output_limit() -> None:
    model = _model(context_window_tokens=100_000, max_output_tokens=4_096)

    budget = ModelCapabilityResolver.runtime_budget(
        model,
        requested_max_output_tokens=8_192,
    )

    assert budget.requested_max_output_tokens == 4_096
    assert budget.request_input_limit == 95_904


def test_non_positive_legacy_model_output_limit_is_treated_as_unset() -> None:
    model = _model(max_input_tokens=100_000, max_output_tokens=0)

    capabilities = ModelCapabilityResolver.capabilities(model)
    budget = ModelCapabilityResolver.runtime_budget(
        model,
        requested_max_output_tokens=8_000,
    )

    assert capabilities.max_output_tokens is None
    assert budget.requested_max_output_tokens == 8_000
    assert budget.request_input_limit == 100_000


def test_both_input_capabilities_use_the_smaller_effective_limit() -> None:
    model = _model(
        context_window_tokens=50_000,
        max_input_tokens=48_000,
        max_output_tokens=4_000,
    )

    input_limit, _ = ModelCapabilityResolver.request_input_limit(
        model,
        requested_max_output_tokens=2_000,
    )

    assert input_limit == 48_000


def test_unknown_input_capabilities_use_runtime_config_fallback() -> None:
    model = _model(max_output_tokens=4_000)
    settings = Settings(
        _env_file=None,
        AGENT_RUNTIME_FALLBACK_CONTEXT_WINDOW_TOKENS=131_072,
    )

    capabilities = ModelCapabilityResolver.capabilities(model, settings=settings)
    budget = ModelCapabilityResolver.runtime_budget(
        model,
        requested_max_output_tokens=1_000,
        settings=settings,
    )

    assert capabilities.context_window_tokens == 131_072
    assert capabilities.max_input_tokens is None
    assert capabilities.capability_source == "runtime_config"
    assert budget.requested_max_output_tokens == 1_000
    assert budget.request_input_limit == 130_072


def test_shared_context_without_output_reservation_fails_closed() -> None:
    model = _model(context_window_tokens=100_000)

    with pytest.raises(ModelCapabilityError, match="requires a request or model output limit") as exc_info:
        ModelCapabilityResolver.runtime_budget(
            model,
            requested_max_output_tokens=None,
        )

    assert exc_info.value.code == "unknown_output_limit"


@pytest.mark.parametrize(
    ("component", "value", "error_code"),
    [
        ("static_prompt_tokens", -1, "invalid_budget_component"),
        ("compact_threshold_ratio", 0, "invalid_compact_threshold_ratio"),
        ("compact_threshold_ratio", 1.01, "invalid_compact_threshold_ratio"),
    ],
)
def test_invalid_budget_inputs_are_rejected(component: str, value: int | float, error_code: str) -> None:
    kwargs: dict[str, int | float | None] = {
        "requested_max_output_tokens": 1_000,
        component: value,
    }

    with pytest.raises(ModelCapabilityError) as exc_info:
        ModelCapabilityResolver.runtime_budget(_model(max_input_tokens=10_000), **kwargs)

    assert exc_info.value.code == error_code


@pytest.mark.asyncio
async def test_platform_model_resolution_requires_configuration() -> None:
    with pytest.raises(PlatformModelConfigurationError, match="is not configured") as exc_info:
        await resolve_platform_model(
            _Session(None),  # type: ignore[arg-type]
            None,
            setting_name="MULTI_AGENT_COMPACT_MODEL_ID",
        )

    assert exc_info.value.setting_name == "MULTI_AGENT_COMPACT_MODEL_ID"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "expected_reason"),
    [
        (None, "does not exist"),
        (_model(enabled=False), "is disabled"),
        (_model(tenant_id=uuid.uuid4()), "is tenant-scoped"),
    ],
)
async def test_platform_model_resolution_rejects_unusable_models(
    model: LLMModel | None,
    expected_reason: str,
) -> None:
    with pytest.raises(PlatformModelConfigurationError, match=expected_reason):
        await resolve_platform_model(
            _Session(model),  # type: ignore[arg-type]
            uuid.uuid4(),
            setting_name="TEST_MODEL_ID",
        )


@pytest.mark.asyncio
async def test_global_runtime_model_resolvers_accept_only_enabled_platform_models() -> None:
    tenant_id = uuid.uuid4()
    compact_id = uuid.uuid4()
    planning_id = uuid.uuid4()
    model = _model(tenant_id=None, enabled=True)
    settings = Settings(
        _env_file=None,
        MULTI_AGENT_COMPACT_MODEL_ID=compact_id,
        MULTI_AGENT_PLANNING_MODEL_ID=planning_id,
    )
    session = _Session(model)

    assert await resolve_multi_agent_compact_model(session, settings, tenant_id=tenant_id) is model  # type: ignore[arg-type]
    assert await resolve_multi_agent_planning_model(session, settings, tenant_id=tenant_id) is model  # type: ignore[arg-type]
    assert len(session.statements) == 4
    assert settings.AGENT_RUNTIME_SUMMARY_THRESHOLD_RATIO == 0.85
    assert settings.AGENT_RUNTIME_MODEL_CAPABILITY_REFRESH_SECONDS == 86400
    assert settings.MULTI_AGENT_COMPACT_MODEL_ID == compact_id
    assert settings.MULTI_AGENT_PLANNING_MODEL_ID == planning_id


@pytest.mark.asyncio
async def test_database_runtime_model_choices_override_environment_fallbacks() -> None:
    tenant_id = uuid.uuid4()
    environment_compact_id = uuid.uuid4()
    environment_planning_id = uuid.uuid4()
    database_model_id = uuid.uuid4()
    model = _model(id=database_model_id, tenant_id=None, enabled=True)
    setting = SystemSetting(
        key="multi_agent_runtime_models",
        value={
            "compact_model_id": str(database_model_id),
            "planning_model_id": str(database_model_id),
        },
    )
    settings = Settings(
        _env_file=None,
        MULTI_AGENT_COMPACT_MODEL_ID=environment_compact_id,
        MULTI_AGENT_PLANNING_MODEL_ID=environment_planning_id,
    )
    session = _Session(model, setting)

    assert await resolve_multi_agent_compact_model(session, settings, tenant_id=tenant_id) is model  # type: ignore[arg-type]
    assert await resolve_multi_agent_planning_model(session, settings, tenant_id=tenant_id) is model  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_group_runtime_model_resolvers_accept_same_tenant_models() -> None:
    tenant_id = uuid.uuid4()
    model_id = uuid.uuid4()
    model = _model(id=model_id, tenant_id=tenant_id, enabled=True)
    setting = SystemSetting(
        key=f"multi_agent_runtime_models:{tenant_id}",
        value={
            "compact_model_id": str(model_id),
            "planning_model_id": str(model_id),
        },
    )
    session = _Session(model, setting)
    settings = Settings(_env_file=None)

    assert await resolve_multi_agent_compact_model(session, settings, tenant_id=tenant_id) is model  # type: ignore[arg-type]
    assert await resolve_multi_agent_planning_model(session, settings, tenant_id=tenant_id) is model  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_group_runtime_model_resolvers_reject_cross_tenant_models() -> None:
    tenant_id = uuid.uuid4()
    model_id = uuid.uuid4()
    model = _model(id=model_id, tenant_id=uuid.uuid4(), enabled=True)
    setting = SystemSetting(
        key=f"multi_agent_runtime_models:{tenant_id}",
        value={"planning_model_id": str(model_id)},
    )
    session = _Session(model, setting)

    with pytest.raises(PlatformModelConfigurationError, match="another tenant"):
        await resolve_multi_agent_planning_model(
            session,  # type: ignore[arg-type]
            Settings(_env_file=None),
            tenant_id=tenant_id,
        )
