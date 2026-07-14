"""Pure tests for cached model capability and platform-model resolution."""

import uuid

import pytest

from app.config import Settings
from app.models.llm import LLMModel
from app.models.tenant import Tenant
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
    def __init__(self, value: object | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object | None:
        return self.value


class _Session:
    def __init__(self, *values: object | None) -> None:
        self.values = list(values)
        self.statements: list[object] = []

    async def execute(self, statement: object) -> _Result:
        self.statements.append(statement)
        if not self.values:
            raise AssertionError("unexpected query")
        value = self.values.pop(0) if len(self.values) > 1 else self.values[0]
        return _Result(value)


def test_llm_capability_columns_and_checks_are_declared() -> None:
    table = LLMModel.__table__
    for column_name in (
        "context_window_tokens",
        "context_window_tokens_override",
        "max_input_tokens",
        "max_input_tokens_override",
        "capability_source",
        "capability_checked_at",
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


def test_unknown_input_capabilities_use_non_blocking_budget() -> None:
    model = _model(max_output_tokens=4_000)

    budget = ModelCapabilityResolver.runtime_budget(
        model,
        requested_max_output_tokens=1_000,
    )

    assert budget.request_input_limit == 32_000
    assert budget.effective_runtime_budget == 32_000
    assert budget.compact_threshold == 27_200


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
    tenant = Tenant(id=tenant_id, name="Tenant", slug="tenant", planning_model_id=None)
    session = _Session(model, tenant, model)

    assert await resolve_multi_agent_compact_model(session, settings) is model  # type: ignore[arg-type]
    assert await resolve_multi_agent_planning_model(  # type: ignore[arg-type]
        session,
        tenant_id=tenant_id,
        settings=settings,
    ) is model
    assert len(session.statements) == 3
    assert settings.AGENT_RUNTIME_SUMMARY_THRESHOLD_RATIO == 0.85
    assert settings.AGENT_RUNTIME_MODEL_CAPABILITY_REFRESH_SECONDS == 86400
    assert settings.MULTI_AGENT_COMPACT_MODEL_ID == compact_id
    assert settings.MULTI_AGENT_PLANNING_MODEL_ID == planning_id


@pytest.mark.asyncio
async def test_tenant_planning_model_overrides_platform_fallback() -> None:
    tenant_id = uuid.uuid4()
    model = _model(id=uuid.uuid4(), tenant_id=tenant_id, enabled=True)
    tenant = Tenant(
        id=tenant_id,
        name="Tenant",
        slug="tenant",
        planning_model_id=model.id,
    )
    settings = Settings(
        _env_file=None,
        MULTI_AGENT_PLANNING_MODEL_ID=uuid.uuid4(),
    )
    session = _Session(tenant, model)

    resolved = await resolve_multi_agent_planning_model(  # type: ignore[arg-type]
        session,
        tenant_id=tenant_id,
        settings=settings,
    )

    assert resolved is model
    assert len(session.statements) == 2


@pytest.mark.asyncio
async def test_invalid_tenant_planning_model_does_not_fallback() -> None:
    tenant_id = uuid.uuid4()
    model = _model(id=uuid.uuid4(), tenant_id=uuid.uuid4(), enabled=True)
    tenant = Tenant(
        id=tenant_id,
        name="Tenant",
        slug="tenant",
        planning_model_id=model.id,
    )
    session = _Session(tenant, model)

    with pytest.raises(PlatformModelConfigurationError, match="another tenant"):
        await resolve_multi_agent_planning_model(  # type: ignore[arg-type]
            session,
            tenant_id=tenant_id,
            settings=Settings(
                _env_file=None,
                MULTI_AGENT_PLANNING_MODEL_ID=uuid.uuid4(),
            ),
        )

    assert len(session.statements) == 2
