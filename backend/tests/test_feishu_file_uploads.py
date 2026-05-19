import json
import uuid
from pathlib import Path
from types import SimpleNamespace
import sys
import types
from unittest.mock import AsyncMock
import importlib.util

import pytest

from app.models.audit import ChatMessage

FEISHU_API_PATH = Path(__file__).resolve().parents[1] / "app" / "api" / "feishu.py"
_feishu_spec = importlib.util.spec_from_file_location("clawith_local_feishu_api", FEISHU_API_PATH)
feishu_api = importlib.util.module_from_spec(_feishu_spec)
assert _feishu_spec and _feishu_spec.loader
_feishu_spec.loader.exec_module(feishu_api)


class DummyResult:
    def __init__(self, values=None, scalar_value=None):
        self._values = list(values or [])
        self._scalar_value = scalar_value

    def scalar_one_or_none(self):
        if self._values:
            return self._values[0]
        return self._scalar_value

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class RecordingSession:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.added = []
        self.commits = 0

    async def execute(self, _statement):
        if self.responses:
            return self.responses.pop(0)
        return DummyResult()

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


class SessionFactory:
    def __init__(self, sessions):
        self._sessions = list(sessions)

    def __call__(self):
        session = self._sessions.pop(0)

        class _Ctx:
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Ctx()


class FakeHttpxResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeHttpxClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, _url, **_kwargs):
        return FakeHttpxResponse({"app_access_token": ""})


@pytest.mark.asyncio
async def test_handle_feishu_pdf_upload_runs_llm_and_sends_reply(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    conv_id = uuid.uuid4()
    agent = SimpleNamespace(
        id=agent_id,
        name="DocBot",
        context_window_size=20,
        creator_id=uuid.uuid4(),
    )
    session = SimpleNamespace(id=conv_id, last_message_at=None)

    session1 = RecordingSession(
        responses=[
            DummyResult(scalar_value=agent),
            DummyResult(values=[]),
        ]
    )
    session2 = RecordingSession()
    session3 = RecordingSession()
    session4 = RecordingSession()

    monkeypatch.setattr(
        "app.database.async_session",
        SessionFactory([session1, session2, session3, session4]),
    )
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(AGENT_DATA_DIR=str(tmp_path)),
    )
    monkeypatch.setattr("httpx.AsyncClient", lambda *args, **kwargs: FakeHttpxClient())

    fake_channel_user_service_module = types.ModuleType("app.services.channel_user_service")
    fake_channel_user_service_module.channel_user_service = SimpleNamespace(
        resolve_channel_user=AsyncMock(return_value=SimpleNamespace(id=user_id))
    )
    monkeypatch.setitem(
        sys.modules,
        "app.services.channel_user_service",
        fake_channel_user_service_module,
    )

    fake_channel_session_module = types.ModuleType("app.services.channel_session")
    fake_channel_session_module.find_or_create_channel_session = AsyncMock(return_value=session)
    monkeypatch.setitem(
        sys.modules,
        "app.services.channel_session",
        fake_channel_session_module,
    )

    fake_text_extractor_module = types.ModuleType("app.services.text_extractor")
    fake_text_extractor_module.needs_extraction = lambda filename: filename.endswith(".pdf")

    def fake_save_extracted_text(save_path: Path, _file_bytes: bytes, _filename: str):
        md_path = save_path.with_suffix(".md")
        md_path.write_text("Extracted text", encoding="utf-8")
        return md_path

    fake_text_extractor_module.save_extracted_text = fake_save_extracted_text
    monkeypatch.setitem(
        sys.modules,
        "app.services.text_extractor",
        fake_text_extractor_module,
    )
    monkeypatch.setattr(
        feishu_api.feishu_service,
        "download_message_resource",
        AsyncMock(return_value=b"%PDF-1.4 fake"),
    )

    sent_messages = []

    async def fake_send_message(_app_id, _app_secret, receive_id, msg_type, content, receive_id_type="open_id", **_kwargs):
        sent_messages.append(
            {
                "receive_id": receive_id,
                "receive_id_type": receive_id_type,
                "msg_type": msg_type,
                "content": json.loads(content)["text"],
            }
        )
        return {"code": 0, "data": {"message_id": "msg_1"}}

    monkeypatch.setattr(feishu_api.feishu_service, "send_message", fake_send_message)

    llm_inputs = []

    async def fake_call_agent_llm(_db, _agent_id, user_text, **_kwargs):
        llm_inputs.append(user_text)
        return "我已经读取了这份 PDF，请告诉我你希望我怎么处理。"

    monkeypatch.setattr(feishu_api, "_call_agent_llm", fake_call_agent_llm)

    fake_activity_logger_module = types.ModuleType("app.services.activity_logger")
    fake_activity_logger_module.log_activity = AsyncMock()
    monkeypatch.setitem(
        sys.modules,
        "app.services.activity_logger",
        fake_activity_logger_module,
    )

    await feishu_api._handle_feishu_file(
        db=None,
        agent_id=agent_id,
        config=SimpleNamespace(app_id="app_id", app_secret="app_secret"),
        message={
            "message_type": "file",
            "message_id": "om_123",
            "content": json.dumps({"file_key": "file_key_123", "file_name": "report.pdf"}),
        },
        sender_open_id="ou_sender",
        sender_user_id_from_event="ou_user",
        chat_type="p2p",
        chat_id="",
    )

    pdf_path = tmp_path / str(agent_id) / "workspace" / "uploads" / "report.pdf"
    md_path = tmp_path / str(agent_id) / "workspace" / "uploads" / "report.md"

    assert pdf_path.exists()
    assert md_path.exists()
    assert len(sent_messages) == 2
    assert sent_messages[0]["content"] == "已收到文件，正在读取内容，请稍等。"
    assert "我已经读取了这份 PDF" in sent_messages[1]["content"]
    assert 'workspace/uploads/report.pdf' in llm_inputs[0]
    assert 'workspace/uploads/report.md' in llm_inputs[0]

    user_messages = [obj for obj in session1.added if isinstance(obj, ChatMessage) and obj.role == "user"]
    ack_messages = [obj for obj in session2.added if isinstance(obj, ChatMessage) and obj.role == "assistant"]
    final_messages = [obj for obj in session4.added if isinstance(obj, ChatMessage) and obj.role == "assistant"]
    assert len(user_messages) == 1
    assert user_messages[0].content == "[file:report.pdf]"
    assert ack_messages[0].content == "已收到文件，正在读取内容，请稍等。"
    assert "我已经读取了这份 PDF" in final_messages[0].content


def test_build_uploaded_file_user_text_prefers_extracted_markdown():
    prompt = feishu_api._build_uploaded_file_user_text(
        filename="report.pdf",
        workspace_rel_path="workspace/uploads/report.pdf",
        extracted_rel_path="workspace/uploads/report.md",
    )

    assert "report.pdf" in prompt
    assert "report.md" in prompt
    assert "read_file" in prompt
    assert "read_document" in prompt
