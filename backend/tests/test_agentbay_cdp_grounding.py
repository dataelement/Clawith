from __future__ import annotations

import sys
import types


fake_agentbay = types.ModuleType("agentbay")
fake_agentbay.AgentBay = object
fake_agentbay.CreateSessionParams = object
sys.modules.setdefault("agentbay", fake_agentbay)

from app.services.agentbay_client import (  # noqa: E402
    _build_browser_cdp_action_script,
    _build_gemini_native_grounding_payload,
    _build_openai_compatible_grounding_payload,
    _extract_gemini_native_content,
    _extract_openai_compatible_content,
    _fallback_openai_base_url_from_native_models_base_url,
    _gemini_grounding_response_schema,
    _gemini_native_generate_content_url,
    _grounding_not_found_message,
    _grounding_result_has_usable_target,
    _grounding_target_not_found,
    _is_gemini_native_models_base_url,
    _normalize_grounding_result,
    _normalized_box_center_to_pixel,
    _parse_cdp_action_result,
    _parse_grounding_json,
    _summarize_gemini_native_response,
    _summarize_openai_compatible_response,
)


def test_parse_grounding_json_accepts_fenced_json():
    data = _parse_grounding_json(
        """```json
        {"target": "Search box", "box_2d": [100, 200, 300, 600], "confidence": 0.9}
        ```"""
    )

    assert data["target"] == "Search box"
    assert data["box_2d"] == [100, 200, 300, 600]


def test_normalized_box_center_to_pixel_uses_gemini_yxyx_order():
    ymin, xmin, ymax, xmax, x, y = _normalized_box_center_to_pixel(
        [100, 200, 300, 600],
        width=1920,
        height=1080,
    )

    assert (ymin, xmin, ymax, xmax) == (100, 200, 300, 600)
    assert x == 768
    assert y == 216


def test_normalized_box_center_to_pixel_clamps_to_image_bounds():
    *_, x, y = _normalized_box_center_to_pixel(
        [1500, 1500, 1700, 1700],
        width=100,
        height=50,
    )

    assert x == 99
    assert y == 49


def test_parse_cdp_action_result_accepts_success_stdout_after_timeout():
    result = types.SimpleNamespace(
        success=False,
        stdout='{"success":true,"action":"click","x":10,"y":20}\n',
        stderr="",
        error_message="Command timed out",
    )

    data = _parse_cdp_action_result(result)

    assert data["success"] is True
    assert data["action"] == "click"


def test_build_browser_cdp_action_script_disconnects_without_closing_remote_browser():
    script = _build_browser_cdp_action_script()

    assert "browser.disconnect" in script
    assert "process.exit(exitCode)" in script
    assert "Promise.race" in script


def test_grounding_target_not_found_message_includes_visible_page_summary():
    grounding = {
        "found": False,
        "box_2d": None,
        "page_content": "A Magento Admin login page with Username, Password, and Sign In controls.",
        "reason": "The requested Create Order button is not visible.",
        "clarification": "Navigate after login or specify a visible login control.",
    }

    assert _grounding_target_not_found(grounding) is True
    message = _grounding_not_found_message("Create Order button", grounding)

    assert "no CDP action was performed" in message
    assert "Magento Admin login page" in message
    assert "Create Order button" in message
    assert "Navigate after login" in message


def test_openai_compatible_grounding_payload_keeps_openai_json_mode():
    payload = _build_openai_compatible_grounding_payload(
        model_name="google/gemini-3.5-flash",
        prompt="Find the search field",
        image_mime_type="image/png",
        image_base64="abcd",
    )

    assert payload["model"] == "google/gemini-3.5-flash"
    assert payload["response_format"] == {"type": "json_object"}
    assert "generationConfig" not in payload
    image_part = payload["messages"][0]["content"][1]["image_url"]["url"]
    assert image_part == "data:image/png;base64,abcd"


def test_gemini_native_grounding_payload_uses_generation_config_schema():
    payload = _build_gemini_native_grounding_payload(
        prompt="Find the search field",
        image_mime_type="image/png",
        image_base64="abcd",
    )

    config = payload["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"] == _gemini_grounding_response_schema()
    assert config["maxOutputTokens"] == 512
    assert payload["contents"][0]["parts"][1]["inlineData"] == {
        "mimeType": "image/png",
        "data": "abcd",
    }
    schema = config["responseSchema"]
    assert schema["properties"]["box_2d"]["nullable"] is True
    assert schema["required"] == [
        "found",
        "target",
        "box_2d",
        "confidence",
        "reason",
        "page_content",
        "clarification",
    ]


def test_gemini_native_helpers_and_legacy_output_normalization():
    assert _is_gemini_native_models_base_url("https://open.palebluedot.ai/v1beta/models")
    assert _is_gemini_native_models_base_url("https://open.palebluedot.ai/v1/models/")
    assert not _is_gemini_native_models_base_url("https://open.palebluedot.ai/v1")
    assert _fallback_openai_base_url_from_native_models_base_url(
        "https://open.palebluedot.ai/v1beta/models"
    ) == "https://open.palebluedot.ai/v1"
    assert _fallback_openai_base_url_from_native_models_base_url(
        "https://open.palebluedot.ai/v1/models/"
    ) == "https://open.palebluedot.ai/v1"
    assert _gemini_native_generate_content_url(
        "https://open.palebluedot.ai/v1beta/models",
        "google/gemini-3.5-flash",
    ) == "https://open.palebluedot.ai/v1beta/models/google/gemini-3.5-flash:generateContent"
    assert _extract_gemini_native_content({
        "candidates": [{"content": {"parts": [{"text": "{\"found\":true}"}]}}],
    }) == "{\"found\":true}"

    normalized = _normalize_grounding_result({
        "point": [530, 517],
        "label": "Sign In button",
        "box_2d": [389, 344, 671, 690],
    })
    assert normalized["found"] is True
    assert normalized["target"] == "Sign In button"
    assert normalized["box_2d"] == [389, 344, 671, 690]
    assert normalized["reason"] == ""
    assert _grounding_result_has_usable_target(normalized) is True
    assert _grounding_result_has_usable_target({"found": True}) is False
    assert _grounding_result_has_usable_target({"found": False, "box_2d": None}) is True



def test_grounding_parse_error_includes_response_summary():
    try:
        _parse_grounding_json("", response_summary={"candidate_count": 1, "finishReason": "STOP"})
        raise AssertionError("expected non-json parse failure")
    except RuntimeError as exc:
        message = str(exc)

    assert "Gemini grounding returned non-JSON content" in message
    assert "response_summary=" in message
    assert "candidate_count" in message
    assert "STOP" in message


def test_grounding_response_summaries_are_safe_and_actionable():
    native = {
        "candidates": [{
            "finishReason": "STOP",
            "content": {"parts": [{"text": "not json"}]},
        }],
        "usageMetadata": {"totalTokenCount": 10},
    }
    openai = {
        "choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": ""},
        }],
        "usage": {"total_tokens": 10},
    }

    native_summary = _summarize_gemini_native_response(native)
    openai_summary = _summarize_openai_compatible_response(openai)

    assert native_summary["candidate_count"] == 1
    assert native_summary["finishReason"] == "STOP"
    assert native_summary["parts"][0]["text_preview"] == "not json"
    assert openai_summary["choice_count"] == 1
    assert openai_summary["finish_reason"] == "stop"
    assert openai_summary["content_type"] == "str"
    assert _extract_openai_compatible_content(openai) == ""
