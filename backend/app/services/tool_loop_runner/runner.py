from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any

from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.models.llm import LLMModel
from app.services.tool_loop_runner.models import (
    CapabilityFlags,
    RoundOutcome,
    RunContext,
    RunResult,
    RunStatus,
    TOOLS_REQUIRING_ARGS,
    TokenUsage,
)

_WARN_THRESHOLD_RATIO = 0.8
_WARN_REMAINING = 2


class ToolLoopRunner:
    async def run(
        self,
        ctx: RunContext,
        messages: list[Any],
        flags: CapabilityFlags,
    ) -> RunResult:
        settings = get_settings()
        start_time = time.monotonic()
        agent = ctx.agent
        max_rounds = self._resolve_max_rounds(ctx, agent, flags, settings)

        if flags.persist_per_round:
            assert ctx.round_log_sink is not None

        from app.database import async_session
        from app.services.agent_tools import execute_tool, get_agent_tools_for_llm
        from app.services.llm_utils import (
            FallbackModelConfig,
            LLMError,
            LLMMessage,
            create_llm_client,
            get_max_tokens,
            get_model_api_key,
            try_create_fallback_client,
        )

        model = None
        fallback_config: FallbackModelConfig | None = None
        async with async_session() as db:
            primary_model_id = agent.primary_model_id
            fallback_model_id = agent.fallback_model_id
            model_id = primary_model_id or fallback_model_id
            if model_id:
                res = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
                model = res.scalar_one_or_none()
            if fallback_model_id and fallback_model_id != model_id:
                fb_res = await db.execute(select(LLMModel).where(LLMModel.id == fallback_model_id))
                fb_model = fb_res.scalar_one_or_none()
                if fb_model:
                    fallback_config = FallbackModelConfig.from_orm(fb_model)

        if model is None:
            return RunResult(status=RunStatus.ERROR, error=f"{agent.name} 未配置 LLM 模型，无法执行")

        supports_vision = getattr(model, "supports_vision", False)
        try:
            active_client = create_llm_client(
                provider=model.provider,
                api_key=get_model_api_key(model),
                model=model.model,
                base_url=model.base_url,
                timeout=float(getattr(model, "request_timeout", None) or 120.0),
            )
            active_model = model
        except Exception as e:
            return RunResult(status=RunStatus.ERROR, error=f"创建 LLM 客户端失败: {e}")

        clients_to_close = [active_client]
        used_fallback = False
        messages = self._preprocess_messages(messages, flags, supports_vision, agent.id)
        tools_for_llm = await get_agent_tools_for_llm(agent.id)
        max_tokens = get_max_tokens(model.provider, model.model, getattr(model, "max_output_tokens", None))

        rounds_completed = 0
        accumulated_tokens = 0
        final_text = ""
        all_rounds: list[RoundOutcome] = []
        arg_guard_rejections: list[dict] = []
        result_status = RunStatus.MAX_ROUNDS

        try:
            for round_i in range(max_rounds):
                round_start = time.monotonic()

                if flags.listen_abort and ctx.abort_source is not None and await ctx.abort_source.is_aborted():
                    result_status = RunStatus.ABORTED
                    break

                if flags.enforce_quota:
                    quota_result = self._check_quota(agent)
                    if quota_result is not None:
                        await self._log_activity(
                            agent.id,
                            ctx.caller,
                            rounds_completed,
                            RunStatus.QUOTA_EXCEEDED,
                            quota_rejected=True,
                            quota_scope=quota_result,
                            arg_guard_rejections=arg_guard_rejections,
                            duration_ms=int((time.monotonic() - start_time) * 1000),
                        )
                        return RunResult(
                            status=RunStatus.QUOTA_EXCEEDED,
                            scope=quota_result,
                            rounds=all_rounds,
                            total_usage=TokenUsage(total_tokens=accumulated_tokens),
                        )

                warn_80 = int(max_rounds * _WARN_THRESHOLD_RATIO)
                warn_last2 = max_rounds - _WARN_REMAINING
                if round_i == warn_80:
                    messages.append(
                        LLMMessage(
                            role="user",
                            content=(
                                f"⚠️ 你已使用 {round_i}/{max_rounds} 轮工具调用。"
                                "如果当前任务尚未完成，请尽快保存进度到 focus.md，"
                                "并使用 set_trigger 设置续接触发器，在剩余轮次中做好收尾。"
                            ),
                        )
                    )
                elif round_i == warn_last2:
                    messages.append(
                        LLMMessage(
                            role="user",
                            content=f"🚨 仅剩 {_WARN_REMAINING} 轮工具调用。请立即保存进度到 focus.md 并设置续接触发器。",
                        )
                    )

                response = None
                llm_error: str | None = None
                from app.services.task_executor import LLM_CALL_MAX_RETRIES, LLM_CALL_RETRY_BASE_DELAY

                for llm_attempt in range(LLM_CALL_MAX_RETRIES):
                    try:
                        response = await active_client.stream(
                            messages=messages,
                            tools=tools_for_llm if tools_for_llm else None,
                            temperature=active_model.temperature,
                            max_tokens=max_tokens,
                        )
                        break
                    except LLMError as e:
                        if llm_attempt < LLM_CALL_MAX_RETRIES - 1:
                            await asyncio.sleep(LLM_CALL_RETRY_BASE_DELAY * (llm_attempt + 1))
                            continue
                        if not used_fallback:
                            fb = try_create_fallback_client(fallback_config, default_timeout=120.0, log_prefix="[Runner] ")
                            if fb:
                                clients_to_close.append(fb)
                                active_client = fb
                                active_model = fallback_config  # type: ignore[assignment]
                                used_fallback = True
                                break
                        llm_error = f"[LLM Error] {e}"
                        break
                    except Exception as e:
                        if llm_attempt < LLM_CALL_MAX_RETRIES - 1:
                            await asyncio.sleep(LLM_CALL_RETRY_BASE_DELAY * (llm_attempt + 1))
                            continue
                        llm_error = f"[LLM call error] {type(e).__name__}: {str(e)[:200]}"
                        break

                if response is None and used_fallback and llm_error is None:
                    continue

                if llm_error:
                    if accumulated_tokens > 0:
                        await self._record_tokens(agent.id, accumulated_tokens, flags)
                    result_status = RunStatus.ERROR
                    final_text = llm_error
                    break

                round_tokens = 0
                if flags.track_token_budget:
                    from app.services.token_tracker import estimate_tokens_from_chars, extract_usage_tokens

                    real_tokens = extract_usage_tokens(response.usage)
                    if real_tokens:
                        round_tokens = real_tokens
                    else:
                        round_chars = sum(len(m.content or "") if isinstance(m.content, str) else 0 for m in messages) + len(response.content or "")
                        round_tokens = estimate_tokens_from_chars(round_chars)
                    accumulated_tokens += round_tokens

                reasoning_content = response.reasoning_content or ""
                if not response.tool_calls:
                    final_text = response.content or "[LLM returned empty content]"
                    result_status = RunStatus.COMPLETED
                    rounds_completed = round_i + 1
                    if accumulated_tokens > 0:
                        await self._record_tokens(agent.id, accumulated_tokens, flags)
                    break

                messages.append(
                    LLMMessage(
                        role="assistant",
                        content=response.content or None,
                        tool_calls=[{"id": tc["id"], "type": "function", "function": tc["function"]} for tc in response.tool_calls],
                        reasoning_content=reasoning_content,
                    )
                )

                round_tool_calls: list[dict] = []
                round_tool_results: list[dict] = []
                for tc in response.tool_calls:
                    fn = tc["function"]
                    tool_name = fn["name"]
                    raw_args = fn.get("arguments", "{}")
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except json.JSONDecodeError:
                        args = {}
                    tc_id = tc.get("id", "")
                    if flags.tool_arg_guard and not args and tool_name in TOOLS_REQUIRING_ARGS:
                        arg_guard_rejections.append({"tool": tool_name, "round": round_i})
                        synthetic_result = f"Error: {tool_name} was called with empty arguments. You must provide the required parameters. Please retry with the correct arguments."
                        messages.append(LLMMessage(role="tool", content=synthetic_result, tool_call_id=tc_id))
                        round_tool_calls.append({**tc, "reasoning_content": reasoning_content})
                        round_tool_results.append({"tool_call_id": tc_id, "content": synthetic_result})
                        continue

                    tool_result = await execute_tool(tool_name, args, agent_id=agent.id, user_id=agent.creator_id)
                    tool_content: str | list = str(tool_result)
                    if flags.inject_vision and supports_vision:
                        try:
                            from app.services.vision_inject import try_inject_screenshot_vision
                            from app.services.agent_tools import WORKSPACE_ROOT

                            ws_path = WORKSPACE_ROOT / str(agent.id)
                            vision_content = try_inject_screenshot_vision(tool_name, str(tool_result), ws_path)
                            if vision_content:
                                tool_content = vision_content
                        except Exception:
                            pass

                    messages.append(LLMMessage(role="tool", tool_call_id=tc_id, content=tool_content))
                    round_tool_calls.append({**tc, "reasoning_content": reasoning_content})
                    round_tool_results.append({"tool_call_id": tc_id, "content": str(tool_result)[:500]})

                round_outcome = RoundOutcome(
                    round_index=round_i,
                    tool_calls=round_tool_calls,
                    assistant_text=response.content or "",
                    token_usage=TokenUsage(total_tokens=round_tokens),
                    latency_ms=int((time.monotonic() - round_start) * 1000),
                )
                all_rounds.append(round_outcome)
                rounds_completed = round_i + 1

                if flags.persist_per_round and ctx.round_log_sink is not None:
                    await ctx.round_log_sink.write_round(
                        round_idx=round_i,
                        tool_calls=round_tool_calls,
                        tool_results=round_tool_results,
                        usage=TokenUsage(total_tokens=round_tokens),
                    )
                if ctx.on_round_complete is not None:
                    await ctx.on_round_complete(round_outcome)
            else:
                result_status = RunStatus.MAX_ROUNDS
                if accumulated_tokens > 0:
                    await self._record_tokens(agent.id, accumulated_tokens, flags)
        finally:
            for client in clients_to_close:
                try:
                    await client.close()
                except Exception:
                    pass

        await self._log_activity(
            agent.id,
            ctx.caller,
            rounds_completed,
            result_status,
            quota_rejected=False,
            quota_scope=None,
            arg_guard_rejections=arg_guard_rejections,
            duration_ms=int((time.monotonic() - start_time) * 1000),
        )
        return RunResult(
            status=result_status,
            rounds=all_rounds,
            final_text=final_text,
            total_usage=TokenUsage(total_tokens=accumulated_tokens),
        )

    def _resolve_max_rounds(self, ctx: RunContext, agent: Any, flags: CapabilityFlags, settings: Any) -> int:
        default = settings.TOOL_LOOP_DEFAULT_MAX_ROUNDS
        if ctx.max_rounds_override is not None:
            return ctx.max_rounds_override
        if flags.dynamic_max_rounds:
            agent_rounds = getattr(agent, "max_tool_rounds", None)
            if agent_rounds:
                return agent_rounds
        return default

    def _check_quota(self, agent: Any) -> str | None:
        if agent.max_tokens_per_day and (agent.tokens_used_today or 0) >= agent.max_tokens_per_day:
            return "daily"
        if agent.max_tokens_per_month and (agent.tokens_used_month or 0) >= agent.max_tokens_per_month:
            return "monthly"
        return None

    def _preprocess_messages(self, messages: list[Any], flags: CapabilityFlags, supports_vision: bool, agent_id: uuid.UUID) -> list[Any]:
        from app.services.llm_utils import LLMMessage

        processed = list(messages)
        if flags.inject_vision and supports_vision:
            img_pattern = r'\[image_data:(data:image/[^;]+;base64,[A-Za-z0-9+/=]+)\]'
            for i, msg in enumerate(processed):
                if msg.role != "user" or not msg.content or not isinstance(msg.content, str):
                    continue
                images = re.findall(img_pattern, msg.content)
                if not images:
                    continue
                text = re.sub(img_pattern, "", msg.content).strip()
                parts: list[dict] = []
                for img_url in images:
                    parts.append({"type": "image_url", "image_url": {"url": img_url}})
                if text:
                    parts.append({"type": "text", "text": text})
                processed[i] = LLMMessage(role=msg.role, content=parts)
        elif flags.strip_images:
            img_strip = r'\[image_data:data:image/[^;]+;base64,[A-Za-z0-9+/=]+\]'
            for i, msg in enumerate(processed):
                if msg.role != "user" or not isinstance(msg.content, str):
                    continue
                if "[image_data:" not in msg.content:
                    continue
                n_imgs = len(re.findall(img_strip, msg.content))
                cleaned = re.sub(img_strip, "", msg.content).strip()
                if n_imgs > 0:
                    cleaned += f"\n[用户发送了 {n_imgs} 张图片，但当前模型不支持视觉，无法查看图片内容]"
                processed[i] = LLMMessage(role=msg.role, content=cleaned)
        return processed

    async def _record_tokens(self, agent_id: uuid.UUID, tokens: int, flags: CapabilityFlags) -> None:
        if not flags.track_token_budget:
            return
        from app.services.token_tracker import record_token_usage

        await record_token_usage(agent_id, tokens)

    async def _log_activity(
        self,
        agent_id: uuid.UUID,
        caller: str,
        rounds_used: int,
        status: RunStatus,
        *,
        quota_rejected: bool,
        quota_scope: str | None,
        arg_guard_rejections: list[dict],
        duration_ms: int,
    ) -> None:
        from app.services.activity_logger import log_activity

        await log_activity(
            agent_id,
            "tool_loop_completed",
            f"tool_loop caller={caller} rounds={rounds_used} status={status.value}",
            detail={
                "caller": caller,
                "rounds_used": rounds_used,
                "status": status.value,
                "quota_rejected": quota_rejected,
                "quota_scope": quota_scope,
                "arg_guard_rejections": arg_guard_rejections,
                "duration_ms": duration_ms,
            },
        )
