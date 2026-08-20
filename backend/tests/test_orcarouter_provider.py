"""Provider registry tests for the OrcaRouter integration."""

from app.services.llm.client import (
    PROVIDER_REGISTRY,
    create_llm_client,
    get_provider_base_url,
    get_provider_manifest,
    normalize_provider,
)


def test_orcarouter_registered_in_provider_registry() -> None:
    spec = PROVIDER_REGISTRY.get("orcarouter")
    assert spec is not None
    assert spec.provider == "orcarouter"
    assert spec.display_name == "OrcaRouter"
    assert spec.protocol == "openai_compatible"
    assert spec.default_base_url == "https://api.orcarouter.ai/v1"
    assert spec.supports_tool_choice is True
    assert spec.default_max_tokens > 0


def test_orcarouter_manifest_round_trip() -> None:
    manifest = {entry["provider"]: entry for entry in get_provider_manifest()}
    entry = manifest.get("orcarouter")
    assert entry is not None
    assert entry["display_name"] == "OrcaRouter"
    assert entry["default_base_url"] == "https://api.orcarouter.ai/v1"


def test_orcarouter_base_url_resolution() -> None:
    assert get_provider_base_url("orcarouter") == "https://api.orcarouter.ai/v1"
    # explicit custom base_url takes precedence
    assert get_provider_base_url("orcarouter", "https://example.com/v1") == "https://example.com/v1"
    # aliases normalize to the canonical provider id
    assert normalize_provider("OrcaRouter") == "orcarouter"


def test_create_orcarouter_client_uses_openai_compatible_protocol() -> None:
    client = create_llm_client(
        provider="orcarouter",
        api_key="sk-orca-test",
        model="orcarouter/auto",
    )
    try:
        assert client.base_url == "https://api.orcarouter.ai/v1"
        assert client.model == "orcarouter/auto"
        assert client.supports_tool_choice is True
        # Bearer auth headers for the OpenAI-compatible gateway
        headers = client._get_headers()
        assert headers["Authorization"] == "Bearer sk-orca-test"
    finally:
        import asyncio

        asyncio.run(client.close())
