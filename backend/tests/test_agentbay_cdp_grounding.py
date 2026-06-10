from __future__ import annotations

import sys
import types


fake_agentbay = types.ModuleType("agentbay")
fake_agentbay.AgentBay = object
fake_agentbay.CreateSessionParams = object
sys.modules.setdefault("agentbay", fake_agentbay)

from app.services.agentbay_client import (  # noqa: E402
    _build_browser_cdp_action_script,
    _grounding_not_found_message,
    _grounding_target_not_found,
    _normalized_box_center_to_pixel,
    _parse_cdp_action_result,
    _parse_grounding_json,
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
