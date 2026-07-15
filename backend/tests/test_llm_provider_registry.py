from app.services.llm.client import (
    AnthropicClient,
    OpenAICompatibleClient,
    PROVIDER_REGISTRY,
    create_llm_client,
    get_provider_manifest,
)


def test_minimax_registry_contains_target_models_and_endpoints():
    spec = PROVIDER_REGISTRY["minimax"]

    assert spec.default_model_id == "MiniMax-M3"
    assert [model.model_id for model in spec.models] == ["MiniMax-M3", "MiniMax-M2.7"]
    assert {endpoint.region for endpoint in spec.endpoints} == {"global_en", "cn_zh"}
    assert {
        (endpoint.openai_base_url, endpoint.anthropic_base_url)
        for endpoint in spec.endpoints
    } == {
        ("https://api.minimax.io/v1", "https://api.minimax.io/anthropic"),
        ("https://api.minimaxi.com/v1", "https://api.minimaxi.com/anthropic"),
    }
    assert all(endpoint.anthropic_base_url.endswith("/anthropic") for endpoint in spec.endpoints)


def test_minimax_manifest_exposes_model_metadata_and_endpoint_choices():
    manifest = next(item for item in get_provider_manifest() if item["provider"] == "minimax")

    assert manifest["model_ids"] == ["MiniMax-M3", "MiniMax-M2.7"]
    assert manifest["models"][0]["context_window"] == 1000000
    assert manifest["models"][0]["input_modalities"] == ["text", "image", "video"]
    assert manifest["models"][1]["thinking"] == ["always_on"]
    assert {endpoint["region"] for endpoint in manifest["endpoints"]} == {"global_en", "cn_zh"}


def test_minimax_anthropic_base_uses_anthropic_client():
    client = create_llm_client(
        provider="minimax",
        api_key="test-key",
        model="MiniMax-M3",
        base_url="https://api.minimax.io/anthropic",
    )

    assert isinstance(client, AnthropicClient)
    assert client._normalize_base_url() == "https://api.minimax.io/anthropic"


def test_minimax_openai_base_uses_openai_compatible_client():
    client = create_llm_client(
        provider="minimax",
        api_key="test-key",
        model="MiniMax-M3",
        base_url="https://api.minimaxi.com/v1",
    )

    assert isinstance(client, OpenAICompatibleClient)
