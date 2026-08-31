"""Atlas Cloud provider registry coverage."""

from __future__ import annotations

from app.services.llm.client import (
    LLMMessage,
    OpenAICompatibleClient,
    create_llm_client,
    get_max_tokens,
    get_provider_base_url,
    get_provider_manifest,
    get_provider_spec,
    normalize_provider,
)


def test_atlas_cloud_aliases_resolve_to_canonical_provider() -> None:
    assert normalize_provider("atlas") == "atlascloud"
    assert normalize_provider("atlas-cloud") == "atlascloud"
    assert normalize_provider("ATLASCloud") == "atlascloud"


def test_atlas_cloud_provider_spec_and_manifest_defaults() -> None:
    spec = get_provider_spec("atlas-cloud")

    assert spec is not None
    assert spec.provider == "atlascloud"
    assert spec.display_name == "Atlas Cloud"
    assert spec.protocol == "openai_compatible"
    assert spec.default_base_url == "https://api.atlascloud.ai/v1"
    assert spec.supports_tool_choice is True
    assert spec.default_max_tokens == 8192
    assert get_provider_base_url("atlas", None) == "https://api.atlascloud.ai/v1"
    assert get_provider_base_url("atlascloud", "https://proxy.example/v1") == "https://proxy.example/v1"

    manifest_entry = next(item for item in get_provider_manifest() if item["provider"] == "atlascloud")
    assert manifest_entry["display_name"] == "Atlas Cloud"
    assert manifest_entry["default_base_url"] == "https://api.atlascloud.ai/v1"
    assert manifest_entry["supports_tool_choice"] is True
    assert set(manifest_entry["aliases"]) == {"atlas", "atlas-cloud"}


def test_atlas_cloud_factory_uses_openai_compatible_client() -> None:
    client = create_llm_client(
        provider="atlas",
        api_key="test-key",
        model="qwen/qwen3.5-flash",
    )

    assert isinstance(client, OpenAICompatibleClient)
    assert client.base_url == "https://api.atlascloud.ai/v1"
    assert client.model == "qwen/qwen3.5-flash"
    assert client.supports_tool_choice is True
    assert client._normalize_base_url() == "https://api.atlascloud.ai/v1"


def test_atlas_cloud_uses_provider_token_defaults_and_overrides() -> None:
    assert get_max_tokens("atlascloud", "qwen/qwen3.5-flash") == 8192
    assert get_max_tokens("atlas", "deepseek-ai/deepseek-v4-pro", max_output_tokens=2048) == 2048


def test_atlas_cloud_openai_compatible_payload_keeps_one_leading_system_message() -> None:
    client = create_llm_client(
        provider="atlas-cloud",
        api_key="test-key",
        model="deepseek-ai/deepseek-v4-pro",
    )

    payload = client._build_payload(
        [
            LLMMessage(role="user", content="Earlier user turn"),
            LLMMessage(role="system", content="Static prompt", dynamic_content="Dynamic context"),
            LLMMessage(role="system", content="Later system note"),
            LLMMessage(role="user", content="Current user turn"),
        ],
        tools=[{"type": "function", "function": {"name": "finish", "parameters": {"type": "object"}}}],
        temperature=0.2,
        max_tokens=1024,
    )

    assert isinstance(client, OpenAICompatibleClient)
    assert payload["model"] == "deepseek-ai/deepseek-v4-pro"
    assert payload["tool_choice"] == "auto"
    assert [message["role"] for message in payload["messages"]].count("system") == 1
    assert payload["messages"][0]["role"] == "system"
    assert "Static prompt" in payload["messages"][0]["content"]
    assert "Dynamic context" in payload["messages"][0]["content"]
    assert "Later system note" in payload["messages"][0]["content"]
