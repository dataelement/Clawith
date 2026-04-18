import sys
import types
import unittest
import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


class _FakeColumn:
    def __init__(self, name: str):
        self.name = name

    def in_(self, values: Iterable):
        return ("in", self.name, tuple(values))

    def __eq__(self, other):
        return ("eq", self.name, other)

    def desc(self):
        return ("desc", self.name)


class _FakeQuery:
    def __init__(self, *items):
        self.items = items
        self.steps = []

    def join(self, *args):
        self.steps.append(("join", args))
        return self

    def where(self, *conditions):
        self.steps.append(("where", conditions))
        return self

    def order_by(self, *clauses):
        self.steps.append(("order_by", clauses))
        return self

    def limit(self, value):
        self.steps.append(("limit", value))
        return self


if "sqlalchemy" not in sys.modules:
    fake_sqlalchemy = types.ModuleType("sqlalchemy")

    def _fake_select(*args, **kwargs):
        return _FakeQuery(*args)

    fake_sqlalchemy.select = _fake_select
    sys.modules["sqlalchemy"] = fake_sqlalchemy

if "sqlalchemy.ext" not in sys.modules:
    sys.modules["sqlalchemy.ext"] = types.ModuleType("sqlalchemy.ext")

if "sqlalchemy.ext.asyncio" not in sys.modules:
    fake_asyncio = types.ModuleType("sqlalchemy.ext.asyncio")

    class _FakeAsyncSession:
        pass

    fake_asyncio.AsyncSession = _FakeAsyncSession
    sys.modules["sqlalchemy.ext.asyncio"] = fake_asyncio

if "app.models.chat_session" not in sys.modules:
    fake_chat_session = types.ModuleType("app.models.chat_session")

    class _FakeChatSession:
        id = _FakeColumn("id")
        external_conv_id = _FakeColumn("external_conv_id")
        source_channel = _FakeColumn("source_channel")
        agent_id = _FakeColumn("agent_id")
        is_group = _FakeColumn("is_group")

    fake_chat_session.ChatSession = _FakeChatSession
    sys.modules["app.models.chat_session"] = fake_chat_session

if "app.models.user" not in sys.modules:
    fake_user = types.ModuleType("app.models.user")

    class _FakeUser:
        id = _FakeColumn("id")
        display_name = _FakeColumn("display_name")
        username = _FakeColumn("username")

    fake_user.User = _FakeUser
    sys.modules["app.models.user"] = fake_user

if "app.models.agent" not in sys.modules:
    fake_agent = types.ModuleType("app.models.agent")

    class _FakeAgent:
        id = _FakeColumn("id")
        name = _FakeColumn("name")
        tenant_id = _FakeColumn("tenant_id")

    fake_agent.Agent = _FakeAgent
    sys.modules["app.models.agent"] = fake_agent

if "app.models.audit" not in sys.modules:
    fake_audit = types.ModuleType("app.models.audit")

    class _FakeChatMessage:
        conversation_id = _FakeColumn("conversation_id")
        created_at = _FakeColumn("created_at")
        user_id = _FakeColumn("user_id")
        agent_id = _FakeColumn("agent_id")

    fake_audit.ChatMessage = _FakeChatMessage
    sys.modules["app.models.audit"] = fake_audit


from app.services.channel_session import _normalize_shared_channel_history, load_shared_channel_history


class _FakeScalarResult:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


class _FakeResult:
    def __init__(self, *, fetchall_values=None, scalar_values=None):
        self._fetchall_values = list(fetchall_values or [])
        self._scalar_values = list(scalar_values or [])

    def fetchall(self):
        return list(self._fetchall_values)

    def scalars(self):
        return _FakeScalarResult(self._scalar_values)


class _FakeAsyncSession:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _query):
        if not self._results:
            raise AssertionError("Unexpected extra DB execute() call")
        return self._results.pop(0)


class SharedChannelHistoryTests(unittest.TestCase):
    def test_empty_history_returns_empty_list(self):
        result = _normalize_shared_channel_history(
            [],
            current_agent_id=uuid.uuid4(),
            user_names={},
            agent_names={},
            limit=20,
        )

        self.assertEqual(result, [])

    def test_other_agents_are_injected_as_public_transcript(self):
        current_agent_id = uuid.uuid4()
        other_agent_id = uuid.uuid4()
        user_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        messages = [
            SimpleNamespace(
                role="user",
                user_id=user_id,
                agent_id=current_agent_id,
                content="大家帮我看一下这个 PR",
                created_at=now,
                external_message_id="om_user_1",
            ),
            SimpleNamespace(
                role="assistant",
                user_id=user_id,
                agent_id=other_agent_id,
                content="我先看 migration 风险。",
                created_at=now + timedelta(seconds=1),
                external_message_id=None,
            ),
            SimpleNamespace(
                role="assistant",
                user_id=user_id,
                agent_id=current_agent_id,
                content="我来检查 API 改动。",
                created_at=now + timedelta(seconds=2),
                external_message_id=None,
            ),
        ]

        result = _normalize_shared_channel_history(
            messages,
            current_agent_id=current_agent_id,
            user_names={user_id: "Yifei"},
            agent_names={current_agent_id: "Dev-Agent", other_agent_id: "Review-Agent"},
            limit=20,
        )

        self.assertEqual(
            result,
            [
                {"role": "user", "content": "[群成员 Yifei] 大家帮我看一下这个 PR"},
                {"role": "user", "content": "[其他智能体 Review-Agent] 我先看 migration 风险。"},
                {"role": "assistant", "content": "我来检查 API 改动。"},
            ],
        )

    def test_duplicate_human_messages_with_same_external_message_id_are_deduped(self):
        current_agent_id = uuid.uuid4()
        user_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        messages = [
            SimpleNamespace(
                role="user",
                user_id=user_id,
                agent_id=uuid.uuid4(),
                content="同一条群消息",
                created_at=now,
                external_message_id="om_shared_1",
            ),
            SimpleNamespace(
                role="user",
                user_id=user_id,
                agent_id=uuid.uuid4(),
                content="同一条群消息",
                created_at=now + timedelta(seconds=2),
                external_message_id="om_shared_1",
            ),
        ]

        result = _normalize_shared_channel_history(
            messages,
            current_agent_id=current_agent_id,
            user_names={user_id: "Alice"},
            agent_names={},
            limit=20,
        )

        self.assertEqual(result, [{"role": "user", "content": "[群成员 Alice] 同一条群消息"}])

    def test_text_bucket_fallback_still_dedupes_when_external_message_id_is_missing(self):
        current_agent_id = uuid.uuid4()
        user_id = uuid.uuid4()
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        messages = [
            SimpleNamespace(
                role="user",
                user_id=user_id,
                agent_id=uuid.uuid4(),
                content="没有 message id 的旧消息",
                created_at=now,
                external_message_id=None,
            ),
            SimpleNamespace(
                role="user",
                user_id=user_id,
                agent_id=uuid.uuid4(),
                content="没有 message id 的旧消息",
                created_at=now + timedelta(seconds=4),
                external_message_id=None,
            ),
        ]

        result = _normalize_shared_channel_history(
            messages,
            current_agent_id=current_agent_id,
            user_names={user_id: "Alice"},
            agent_names={},
            limit=20,
        )

        self.assertEqual(result, [{"role": "user", "content": "[群成员 Alice] 没有 message id 的旧消息"}])

    def test_non_chat_roles_are_skipped(self):
        current_agent_id = uuid.uuid4()
        user_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        messages = [
            SimpleNamespace(
                role="tool_call",
                user_id=user_id,
                agent_id=current_agent_id,
                content='{"name":"search"}',
                created_at=now,
                external_message_id=None,
            ),
            SimpleNamespace(
                role="system",
                user_id=user_id,
                agent_id=current_agent_id,
                content="internal note",
                created_at=now + timedelta(seconds=1),
                external_message_id=None,
            ),
        ]

        result = _normalize_shared_channel_history(
            messages,
            current_agent_id=current_agent_id,
            user_names={user_id: "Alice"},
            agent_names={},
            limit=20,
        )

        self.assertEqual(result, [])

    def test_assistant_message_wins_when_same_external_message_id_is_seen_twice(self):
        current_agent_id = uuid.uuid4()
        other_agent_id = uuid.uuid4()
        bot_user_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        messages = [
            SimpleNamespace(
                role="assistant",
                user_id=bot_user_id,
                agent_id=other_agent_id,
                content="我来检查 migration。",
                created_at=now,
                external_message_id="om_bot_1",
            ),
            SimpleNamespace(
                role="user",
                user_id=bot_user_id,
                agent_id=current_agent_id,
                content="我来检查 migration。",
                created_at=now + timedelta(seconds=1),
                external_message_id="om_bot_1",
            ),
        ]

        result = _normalize_shared_channel_history(
            messages,
            current_agent_id=current_agent_id,
            user_names={bot_user_id: "BotUser"},
            agent_names={other_agent_id: "Review-Agent"},
            limit=20,
        )

        self.assertEqual(result, [{"role": "user", "content": "[其他智能体 Review-Agent] 我来检查 migration。"}])

    def test_missing_names_fall_back_to_generic_labels(self):
        current_agent_id = uuid.uuid4()
        other_agent_id = uuid.uuid4()
        unknown_user_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        messages = [
            SimpleNamespace(
                role="user",
                user_id=unknown_user_id,
                agent_id=current_agent_id,
                content="有人在吗",
                created_at=now,
                external_message_id="om_unknown_user_1",
            ),
            SimpleNamespace(
                role="assistant",
                user_id=unknown_user_id,
                agent_id=other_agent_id,
                content="我在。",
                created_at=now + timedelta(seconds=1),
                external_message_id=None,
            ),
        ]

        result = _normalize_shared_channel_history(
            messages,
            current_agent_id=current_agent_id,
            user_names={},
            agent_names={},
            limit=20,
        )

        self.assertEqual(
            result,
            [
                {"role": "user", "content": "[群成员 群成员] 有人在吗"},
                {"role": "user", "content": "[其他智能体 未知智能体] 我在。"},
            ],
        )

    def test_limit_keeps_latest_entries_after_normalization(self):
        current_agent_id = uuid.uuid4()
        user_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        messages = [
            SimpleNamespace(
                role="user",
                user_id=user_id,
                agent_id=current_agent_id,
                content="第一条",
                created_at=now,
                external_message_id="m1",
            ),
            SimpleNamespace(
                role="user",
                user_id=user_id,
                agent_id=current_agent_id,
                content="第二条",
                created_at=now + timedelta(seconds=1),
                external_message_id="m2",
            ),
            SimpleNamespace(
                role="assistant",
                user_id=user_id,
                agent_id=current_agent_id,
                content="第三条",
                created_at=now + timedelta(seconds=2),
                external_message_id="m3",
            ),
        ]

        result = _normalize_shared_channel_history(
            messages,
            current_agent_id=current_agent_id,
            user_names={user_id: "Alice"},
            agent_names={current_agent_id: "Dev-Agent"},
            limit=2,
        )

        self.assertEqual(
            result,
            [
                {"role": "user", "content": "[群成员 Alice] 第二条"},
                {"role": "assistant", "content": "第三条"},
            ],
        )


class SharedChannelHistoryLoaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_loader_returns_empty_when_no_sessions_match(self):
        db = _FakeAsyncSession([
            _FakeResult(fetchall_values=[]),
        ])

        result = await load_shared_channel_history(
            db,
            current_agent_id=uuid.uuid4(),
            current_tenant_id=uuid.uuid4(),
            external_conv_id="feishu_group_missing",
            source_channel="feishu",
            limit=20,
        )

        self.assertEqual(result, [])

    async def test_loader_returns_empty_when_sessions_exist_but_no_messages(self):
        db = _FakeAsyncSession([
            _FakeResult(fetchall_values=[(uuid.uuid4(),), (uuid.uuid4(),)]),
            _FakeResult(scalar_values=[]),
        ])

        result = await load_shared_channel_history(
            db,
            current_agent_id=uuid.uuid4(),
            current_tenant_id=uuid.uuid4(),
            external_conv_id="feishu_group_empty",
            source_channel="feishu",
            limit=20,
        )

        self.assertEqual(result, [])

    async def test_loader_merges_cross_agent_history_and_prefers_assistant_variant(self):
        current_agent_id = uuid.uuid4()
        other_agent_id = uuid.uuid4()
        user_id = uuid.uuid4()
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Match the real DB query shape: ordered by created_at DESC before the
        # loader reverses back to chronological order.
        messages = [
            SimpleNamespace(
                role="user",
                user_id=user_id,
                agent_id=current_agent_id,
                content="我先看 migration 风险。",
                conversation_id="sess-a",
                external_message_id="ext-bot-1",
                created_at=now + timedelta(seconds=3),
            ),
            SimpleNamespace(
                role="assistant",
                user_id=user_id,
                agent_id=other_agent_id,
                content="我先看 migration 风险。",
                conversation_id="sess-b",
                external_message_id="ext-bot-1",
                created_at=now + timedelta(seconds=2),
            ),
            SimpleNamespace(
                role="user",
                user_id=user_id,
                agent_id=other_agent_id,
                content="大家看看这个 PR",
                conversation_id="sess-b",
                external_message_id="ext-user-1",
                created_at=now + timedelta(seconds=1),
            ),
            SimpleNamespace(
                role="user",
                user_id=user_id,
                agent_id=current_agent_id,
                content="大家看看这个 PR",
                conversation_id="sess-a",
                external_message_id="ext-user-1",
                created_at=now,
            ),
        ]

        db = _FakeAsyncSession([
            _FakeResult(fetchall_values=[("sess-a",), ("sess-b",)]),
            _FakeResult(scalar_values=messages),
            _FakeResult(fetchall_values=[(user_id, "Admin", "admin")]),
            _FakeResult(fetchall_values=[(current_agent_id, "Morty"), (other_agent_id, "Meeseeks")]),
        ])

        result = await load_shared_channel_history(
            db,
            current_agent_id=current_agent_id,
            current_tenant_id=uuid.uuid4(),
            external_conv_id="feishu_group_demo",
            source_channel="feishu",
            limit=20,
        )

        self.assertEqual(
            result,
            [
                {"role": "user", "content": "[群成员 Admin] 大家看看这个 PR"},
                {"role": "user", "content": "[其他智能体 Meeseeks] 我先看 migration 风险。"},
            ],
        )

    async def test_loader_returns_empty_when_tenant_scope_is_missing(self):
        db = _FakeAsyncSession([])

        result = await load_shared_channel_history(
            db,
            current_agent_id=uuid.uuid4(),
            current_tenant_id=None,
            external_conv_id="feishu_group_demo",
            source_channel="feishu",
            limit=20,
        )

        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
