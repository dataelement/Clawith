"""Regression coverage for Claude Relay Service's native Anthropic API."""

import pytest

from app.services.llm.client import (
    PROVIDER_REGISTRY,
    AnthropicClient,
    create_llm_client,
    get_provider_manifest,
    normalize_provider,
)


def test_claude_relay_provider_is_exposed_as_anthropic() -> None:
    spec = PROVIDER_REGISTRY["claude-relay-service"]
    manifest = next(
        item for item in get_provider_manifest() if item["provider"] == "claude-relay-service"
    )

    assert spec.protocol == "anthropic"
    assert spec.default_base_url is None
    assert spec.supports_tool_choice is False
    assert manifest["protocol"] == "anthropic"
    assert manifest["default_base_url"] is None
    assert set(manifest["aliases"]) == {"claude-relay", "crs"}


@pytest.mark.parametrize(
    "provider",
    ["claude-relay-service", "claude-relay", "crs"],
)
@pytest.mark.parametrize(
    "base_url",
    [
        "https://relay.example/claude",
        "https://relay.example/claude/",
        "https://relay.example/claude/v1",
        "https://relay.example/claude/v1/messages",
    ],
)
def test_claude_relay_uses_native_anthropic_client(
    provider: str,
    base_url: str,
) -> None:
    client = create_llm_client(
        provider=provider,
        api_key="cr_test_key",
        model="claude-sonnet-4-5",
        base_url=base_url,
    )

    assert isinstance(client, AnthropicClient)
    assert client._normalize_base_url() == "https://relay.example/claude"
    assert client._get_headers()["x-api-key"] == "cr_test_key"
    assert normalize_provider(provider) == "claude-relay-service"
