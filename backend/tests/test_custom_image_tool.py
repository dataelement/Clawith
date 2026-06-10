import base64

import pytest

from app.services.agent_tools import (
    _custom_image_reference_to_bytes,
    _json_path_get,
    _render_json_template,
)


def test_render_json_template_replaces_placeholders_after_json_parse():
    payload = _render_json_template(
        '{"model":"{model}","messages":[{"role":"user","content":"Draw: {prompt}"}],"size":"{size}"}',
        {
            "model": "google/gemini-2.5-flash-image",
            "prompt": 'red "apple"\nwhite background',
            "size": "1024x1024",
        },
    )

    assert payload["model"] == "google/gemini-2.5-flash-image"
    assert payload["messages"][0]["content"] == 'Draw: red "apple"\nwhite background'
    assert payload["size"] == "1024x1024"


def test_render_json_template_accepts_escaped_quote_object_text():
    payload = _render_json_template(
        r'{ \"model\": \"{model}\", \"messages\": [{ \"role\": \"user\", \"content\": \"{prompt}\" }] }',
        {
            "model": "google/gemini-2.5-flash-image",
            "prompt": "red apple",
            "size": "1024x1024",
        },
    )

    assert payload["model"] == "google/gemini-2.5-flash-image"
    assert payload["messages"][0]["content"] == "red apple"


def test_render_json_template_accepts_smart_quotes():
    payload = _render_json_template(
        '{ “model”: “{model}”, “messages”: [{ “role”: “user”, “content”: “{prompt}” }] }',
        {
            "model": "google/gemini-2.5-flash-image",
            "prompt": "blue circle",
            "size": "1024x1024",
        },
    )

    assert payload["model"] == "google/gemini-2.5-flash-image"
    assert payload["messages"][0]["content"] == "blue circle"


def test_json_path_get_supports_nested_lists_and_dicts():
    data = {
        "choices": [
            {
                "message": {
                    "images": [
                        {"image_url": {"url": "data:image/png;base64,abc"}}
                    ]
                }
            }
        ]
    }

    assert (
        _json_path_get(data, "choices.0.message.images.0.image_url.url")
        == "data:image/png;base64,abc"
    )
    assert _json_path_get(data, "choices.1.message") is None
    assert _json_path_get(data, "choices.foo.message") is None


@pytest.mark.asyncio
async def test_custom_image_reference_to_bytes_decodes_data_url():
    raw = b"fake-png-bytes"
    data_url = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")

    assert await _custom_image_reference_to_bytes(data_url, client=None) == raw
