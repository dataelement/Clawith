"""Shared tool-calling loop primitives.

Three places used to each run their own copy of the tool-execution loop:
``llm/caller.py:_process_tool_call`` (call_llm), the inner loop of
``call_agent_llm_with_tools``, and ``services/heartbeat.py``. The duplication
was a known drift hazard — the heartbeat copy's ``_TOOLS_REQUIRING_ARGS``
already disagreed with caller.py's. This module consolidates the shared
parts and adds parallel execution for read-only tools.

What this module does:
  - Single source of truth for ``TOOLS_REQUIRING_ARGS`` and
    ``TOOLS_PARALLELIZABLE``.
  - ``execute_tool_calls`` — run a list of tool_calls; whitelisted reads
    via ``asyncio.gather``, everything else serialized in original order.
    Tool result messages are returned in the same order as the input
    ``tool_calls`` array (provider-strict requirement).
  - ``_run_one`` — execute one tool call end-to-end (args-guard, optional
    pre-flight hook, optional ``on_tool_call`` notifications, optional
    vision injection, mandatory result truncation). Returns the tool
    content as ``str | list``; never mutates shared state.

What this module deliberately does *not* do:
  - Side-effecting tools (write/send/delete/A2A/plaza/exec) are *never* in
    the parallel set. A static safety test asserts the disjoint condition
    (see tests/test_tool_loop.py). The conservative default is "serial".
  - Callback re-entrancy: ``on_tool_call`` may fire concurrently from
    parallel coroutines. Callers must tolerate out-of-order events keyed
    by ``call_id``. The web-chat front-end already keys by id.
  - Heartbeat's plaza rate limiter lives outside the helper as a
    ``pre_execute_hook`` — stateful counters belong to the caller because
    they're per-tick.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from app.services.llm.utils import LLMMessage


# ── Tool classification (single source of truth) ────────────────────────


# Tools that must reject empty-arg invocations rather than executing them.
# The model is told to retry with proper arguments.
TOOLS_REQUIRING_ARGS: frozenset[str] = frozenset({
    "write_file", "read_file", "delete_file", "read_document",
    "send_message_to_agent", "send_feishu_message", "send_email",
    "web_search", "jina_search", "jina_read",
})


# Tools whose output is the model's intentional retrieval path for content
# that may itself have already been spilled to ``_tool_results/``. If we
# re-truncate the result of these, the spill file gets rewritten with its
# own marker — every subsequent ``read_file`` returns a fresh marker, and
# the model can never recover the original content (infinite loop, also
# silently overwrites the spill so even an out-of-band reader sees only
# the marker). Both listed tools support ``offset``/``limit`` pagination,
# so if their output is itself too large for one round the model must
# page through it explicitly.
_TOOLS_BYPASS_TRUNCATION: frozenset[str] = frozenset({
    "read_file",
    "read_document",
})


# Tools safe to run concurrently within one LLM round. Default-deny: a
# tool not in this set runs serially, in the order the model issued it.
# Adding a tool here is a *security* decision — see
# tests/test_tool_loop.py::test_no_side_effecting_tool_in_parallelizable.
TOOLS_PARALLELIZABLE: frozenset[str] = frozenset({
    # Local file reads
    "read_file",
    "list_files",
    "read_document",
    # Network reads
    "web_fetch",
    "web_search",
    "search_jina",
    "jina_search",
    "jina_read",
    # PDF / extraction (read-only)
    "extract_pdf",
    # Trigger introspection (read-only)
    "list_triggers",
})


# ── Execution context ───────────────────────────────────────────────────


PreExecuteHook = Callable[[str, dict[str, Any]], str | None]
OnToolCallCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class ToolExecutionContext:
    """Per-call-site configuration for tool execution.

    ``agent_id`` and ``user_id`` get forwarded to the tool implementation
    (sandbox boundary). ``session_id`` is informational. The optional hooks
    are how individual call sites (web chat, heartbeat) plug in their own
    behavior without forking the loop.
    """

    agent_id: Any
    user_id: Any
    session_id: str = ""
    supports_vision: bool = False
    on_tool_call: OnToolCallCallback | None = None
    full_reasoning_content: str = ""
    # Optional sync hook called before execute_tool. If it returns a string,
    # that string becomes the tool result and execute_tool is skipped — used
    # by heartbeat for plaza rate-limiting ("[BLOCKED] ...").
    pre_execute_hook: PreExecuteHook | None = None


# ── Public API ──────────────────────────────────────────────────────────


async def execute_tool_calls(
    tool_calls: list[dict[str, Any]],
    ctx: ToolExecutionContext,
) -> list[LLMMessage]:
    """Run all tool_calls; return tool result messages in input order.

    **Execution order matches the model-issued order** — only *contiguous*
    runs of whitelisted read tools are batched via ``asyncio.gather``. As
    soon as a non-parallelizable tool is encountered, the pending parallel
    batch is flushed (awaited) before the side-effecting tool runs. This
    preserves dependency ordering: a model-issued ``[write_file, read_file]``
    pair runs write-then-read, so the read sees the write's effect.

    Result messages are reassembled in the original ``tool_calls`` order —
    providers (Anthropic in particular) reject tool_results that drift from
    the assistant's ``tool_calls[]`` order.

    Exceptions from individual tools are caught and surfaced as
    ``Error: <ExceptionName>: <msg>`` content; sibling tools complete.
    """
    results_map: dict[str, Any] = {}
    pending_parallel: list[dict[str, Any]] = []

    async def _flush_pending() -> None:
        """Await any accumulated read-only tools concurrently."""
        if not pending_parallel:
            return
        outs = await asyncio.gather(
            *[_run_one(tc, ctx) for tc in pending_parallel],
            return_exceptions=True,
        )
        for tc, out in zip(pending_parallel, outs):
            results_map[tc["id"]] = out
        pending_parallel.clear()

    for tc in tool_calls:
        name = tc.get("function", {}).get("name", "")
        if name in TOOLS_PARALLELIZABLE:
            # Accumulate; will run as a batch when flushed.
            pending_parallel.append(tc)
        else:
            # Side-effecting tool: run any pending reads first, THEN run this.
            # Order is preserved relative to subsequent reads: a later read
            # in the same round will see this write's effect.
            await _flush_pending()
            try:
                results_map[tc["id"]] = await _run_one(tc, ctx)
            except Exception as exc:
                results_map[tc["id"]] = exc

    # Final flush for any trailing read batch
    await _flush_pending()

    # Reassemble in original order
    messages: list[LLMMessage] = []
    for tc in tool_calls:
        outcome = results_map.get(tc["id"])
        if isinstance(outcome, Exception):
            content: str | list = (
                f"Error: {type(outcome).__name__}: {str(outcome)[:200]}"
            )
            logger.warning(
                f"[tool-loop] Tool '{tc.get('function', {}).get('name', '?')}' "
                f"raised {type(outcome).__name__}: {outcome!s:.200}"
            )
        else:
            content = outcome
        messages.append(LLMMessage(
            role="tool",
            tool_call_id=tc["id"],
            content=content,
        ))
    return messages


# ── Internal: run a single tool call ────────────────────────────────────


async def _run_one(tc: dict[str, Any], ctx: ToolExecutionContext) -> str | list:
    """Execute one tool call; return content (string or vision list).

    Pure function over inputs — does not mutate any shared list. This is
    what makes parallelization safe: gather() over N _run_one calls produces
    N independent results, then the caller assembles them in order.
    """
    fn = tc.get("function", {})
    tool_name = fn.get("name", "")
    raw_args = fn.get("arguments", "{}")
    logger.info(
        f"[tool-loop] Calling {tool_name}({json.dumps(raw_args, ensure_ascii=False)[:100]})"
    )

    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError:
        args = {}

    # Pre-execute hook (e.g. heartbeat plaza rate limits). If it returns a
    # string, that becomes the result and we skip execute_tool entirely.
    short_circuit: str | None = None
    if ctx.pre_execute_hook is not None:
        short_circuit = ctx.pre_execute_hook(tool_name, args)

    # Empty-arg guard
    if short_circuit is None and not args and tool_name in TOOLS_REQUIRING_ARGS:
        return (
            f"Error: {tool_name} was called with empty arguments. "
            "You must provide the required parameters. Please retry with the correct arguments."
        )

    # Notify status=running
    if ctx.on_tool_call is not None:
        try:
            await ctx.on_tool_call({
                "name": tool_name,
                "call_id": tc.get("id", ""),
                "args": args,
                "status": "running",
                "reasoning_content": ctx.full_reasoning_content,
            })
        except Exception:
            pass

    # Execute (or use short-circuit result). Lazy import keeps this module
    # importable in test environments without the full DB/sandbox dep tree.
    if short_circuit is not None:
        result: Any = short_circuit
    else:
        from app.services.agent_tools import execute_tool
        result = await execute_tool(
            tool_name, args,
            agent_id=ctx.agent_id,
            user_id=ctx.user_id or ctx.agent_id,
            session_id=ctx.session_id,
        )

    # Resolve workspace path once for vision + truncation
    ws_path: Path | None = None
    if ctx.agent_id:
        from app.config import get_settings
        ws_path = Path(get_settings().AGENT_DATA_DIR) / str(ctx.agent_id)

    # Vision injection
    tool_content: str | list = str(result)
    if ctx.supports_vision and ws_path is not None:
        try:
            from app.services.vision_inject import try_inject_screenshot_vision
            vision_content = try_inject_screenshot_vision(tool_name, str(result), ws_path)
            if vision_content:
                tool_content = vision_content
                logger.info(f"[tool-loop] Injected screenshot vision for {tool_name}")
        except Exception as e:
            logger.warning(f"[tool-loop] Vision injection failed for {tool_name}: {e}")

    # Tool-result truncation (large payload → spill + marker).
    # Bypass for read_file / read_document: see _TOOLS_BYPASS_TRUNCATION
    # docstring — re-truncating retrieval tools forms a loop with the
    # spill marker.
    if ws_path is not None and tool_name not in _TOOLS_BYPASS_TRUNCATION:
        from app.services.tool_result_truncation import maybe_truncate_tool_result
        tool_content = maybe_truncate_tool_result(
            tool_content, call_id=tc["id"], agent_workspace=ws_path,
        )

    # Notify status=done
    if ctx.on_tool_call is not None:
        try:
            await ctx.on_tool_call({
                "name": tool_name,
                "call_id": tc.get("id", ""),
                "args": args,
                "status": "done",
                "result": result,
                "reasoning_content": ctx.full_reasoning_content,
            })
        except Exception:
            pass

    return tool_content
