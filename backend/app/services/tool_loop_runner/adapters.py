from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from loguru import logger

from app.models.audit import ChatMessage
from app.models.task import Task, TaskLog
from app.services.tool_loop_runner.models import RunResult, RunStatus, TokenUsage


class EventAbortSource:
    def __init__(self, event: asyncio.Event) -> None:
        self._event = event

    async def is_aborted(self) -> bool:
        return self._event.is_set()


_TASK_ABORT_EVENTS: dict[uuid.UUID, asyncio.Event] = {}


class InProcessTaskAbortSource:
    def __init__(self, task_id: uuid.UUID) -> None:
        self._event = _TASK_ABORT_EVENTS.setdefault(task_id, asyncio.Event())

    async def is_aborted(self) -> bool:
        return self._event.is_set()


def request_abort(task_id: uuid.UUID) -> bool:
    evt = _TASK_ABORT_EVENTS.get(task_id)
    if evt is None:
        return False
    evt.set()
    logger.info(f"[Adapters] request_abort sent for task {task_id}")
    return True


def release_abort(task_id: uuid.UUID) -> None:
    _TASK_ABORT_EVENTS.pop(task_id, None)


class ChatMessageSink:
    def __init__(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id

    async def write_round(
        self,
        round_idx: int,
        tool_calls: list[dict],
        tool_results: list[dict],
        usage: TokenUsage,
    ) -> None:
        result_map: dict[str, str] = {}
        for tr in tool_results:
            result_map[tr.get("tool_call_id", "")] = str(tr.get("content", ""))

        from app.database import async_session

        for tc in tool_calls:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                args = {}
            payload = json.dumps(
                {
                    "name": fn.get("name", ""),
                    "args": args,
                    "status": "done",
                    "result": result_map.get(tc.get("id", ""), "")[:500],
                    "reasoning_content": tc.get("reasoning_content"),
                },
                ensure_ascii=False,
            )
            async with async_session() as db:
                db.add(ChatMessage(role="tool_call", content=payload, conversation_id=self._conversation_id))
                await db.commit()


class ChatMessageSinkFull:
    def __init__(self, conversation_id: str, agent_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self._conversation_id = conversation_id
        self._agent_id = agent_id
        self._user_id = user_id

    async def write_round(
        self,
        round_idx: int,
        tool_calls: list[dict],
        tool_results: list[dict],
        usage: TokenUsage,
    ) -> None:
        result_map: dict[str, str] = {}
        for tr in tool_results:
            result_map[tr.get("tool_call_id", "")] = str(tr.get("content", ""))

        from app.database import async_session

        for tc in tool_calls:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                args = {}
            payload = json.dumps(
                {
                    "name": fn.get("name", ""),
                    "args": args,
                    "status": "done",
                    "result": result_map.get(tc.get("id", ""), "")[:500],
                    "reasoning_content": tc.get("reasoning_content"),
                },
                ensure_ascii=False,
            )
            async with async_session() as db:
                db.add(
                    ChatMessage(
                        agent_id=self._agent_id,
                        user_id=self._user_id,
                        role="tool_call",
                        content=payload,
                        conversation_id=self._conversation_id,
                    )
                )
                await db.commit()


class TaskLogSink:
    def __init__(self, task_id: uuid.UUID) -> None:
        self._task_id = task_id

    async def write_round(
        self,
        round_idx: int,
        tool_calls: list[dict],
        tool_results: list[dict],
        usage: TokenUsage,
    ) -> None:
        result_map: dict[str, str] = {}
        for tr in tool_results:
            result_map[tr.get("tool_call_id", "")] = str(tr.get("content", ""))
        lines = [f"Round {round_idx + 1} tool calls"]
        for tc in tool_calls:
            fn = tc.get("function", {})
            lines.append(f"  {fn.get('name', '?')} -> {result_map.get(tc.get('id', ''), '')[:200]}")
        from app.database import async_session

        async with async_session() as db:
            db.add(TaskLog(task_id=self._task_id, content="\n".join(lines)))
            await db.commit()


def translate_to_ws_frame(result: RunResult) -> dict:
    if result.status == RunStatus.QUOTA_EXCEEDED:
        msg = "⚠️ 今日 token 用量已达上限，请明天再试或联系管理员提高限额。"
        if result.scope != "daily":
            msg = "⚠️ 本月 token 用量已达上限，请联系管理员提高限额。"
        return {"type": "done", "role": "assistant", "content": msg}
    if result.status == RunStatus.ABORTED:
        partial = result.final_text.strip()
        content = (partial + "\n\n*[Generation stopped]*") if partial else "*[Generation stopped]*"
        return {"type": "done", "role": "assistant", "content": content}
    if result.status == RunStatus.MAX_ROUNDS:
        return {"type": "done", "role": "assistant", "content": "[Error] Too many tool call rounds"}
    if result.status == RunStatus.ERROR:
        return {"type": "done", "role": "assistant", "content": result.error or "[LLM Error]"}
    return {"type": "done", "role": "assistant", "content": result.final_text}


async def translate_to_task_failure(result: RunResult, task_id: uuid.UUID, task_type: str) -> None:
    now = datetime.now(timezone.utc)
    if result.status == RunStatus.QUOTA_EXCEEDED:
        message = "⚠️ 今日 token 用量已达上限，任务执行中止"
        if result.scope != "daily":
            message = "⚠️ 本月 token 用量已达上限，任务执行中止"
    elif result.status == RunStatus.ABORTED:
        message = "任务执行被中止"
    elif result.status == RunStatus.MAX_ROUNDS:
        message = "已达到最大工具调用轮数，任务执行中止"
    else:
        message = result.error or f"任务执行失败: {result.status}"

    from app.database import async_session
    from sqlalchemy import select

    async with async_session() as db:
        res = await db.execute(select(Task).where(Task.id == task_id))
        task = res.scalar_one_or_none()
        if not task:
            return
        if task_type == "supervision":
            task.status = "pending"
            task.completed_at = None
        else:
            task.status = "failed"
            task.completed_at = now
        db.add(TaskLog(task_id=task_id, content=f"❌ {message}"))
        await db.commit()
