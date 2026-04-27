"""Unit tests for the shared tool-loop helper.

Three classes of tests:
  1. **Safety**: side-effecting tools (write, delete, A2A, plaza, exec)
     must NEVER appear in TOOLS_PARALLELIZABLE. Static disjoint check.
  2. **Order**: tool result messages must come back in the same order as
     the input tool_calls array, regardless of completion order. Tested
     with mocked latencies forcing reverse completion.
  3. **Behavior**: parallel reads run concurrently, side-effects serialize,
     exceptions in one don't kill siblings, empty-arg guard fires, etc.
"""

import asyncio
import time
from unittest.mock import patch

import pytest

from app.services.tool_loop import (
    TOOLS_PARALLELIZABLE,
    TOOLS_REQUIRING_ARGS,
    ToolExecutionContext,
    execute_tool_calls,
)


# ── Helpers ─────────────────────────────────────────────────────────────


def _tc(call_id: str, name: str, args: dict | None = None) -> dict:
    import json as _j
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": _j.dumps(args or {}, ensure_ascii=False),
        },
    }


def _make_ctx(tmp_path) -> ToolExecutionContext:
    """ctx with a real workspace path so vision/truncation paths are reachable."""
    return ToolExecutionContext(
        agent_id="test-agent-id",
        user_id="test-user-id",
        session_id="test-session",
        supports_vision=False,
    )


# ── Safety: side-effecting tools must not be parallelizable ─────────────


def test_no_side_effecting_tool_in_parallelizable():
    """Critical safety invariant. If this fails, A2A wake dedup, write
    ordering, and plaza rate limits all break under load."""
    forbidden = {
        # A2A — relationship check + dedup + max_fires=1 require serial
        "send_message_to_agent",
        "send_file_to_agent",
        # Filesystem writes
        "write_file",
        "delete_file",
        "edit_file",
        # External messages
        "send_feishu_message",
        "send_email",
        # Document mutation
        "feishu_doc_create",
        "feishu_doc_append",
        # Trigger lifecycle (state-changing)
        "set_trigger",
        "update_trigger",
        "cancel_trigger",
        # Plaza writes (heartbeat rate-limited too)
        "plaza_create_post",
        "plaza_add_comment",
        # Code execution
        "execute_code",
    }
    leaked = TOOLS_PARALLELIZABLE & forbidden
    assert not leaked, (
        f"Forbidden tools leaked into TOOLS_PARALLELIZABLE: {leaked}. "
        "Side-effecting tools must run serially or A2A/dedup invariants break."
    )


def test_tools_requiring_args_includes_essentials():
    """Sanity: write_file and read_file always need args."""
    assert "write_file" in TOOLS_REQUIRING_ARGS
    assert "read_file" in TOOLS_REQUIRING_ARGS
    assert "send_message_to_agent" in TOOLS_REQUIRING_ARGS


# ── Order preservation ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parallel_reads_preserve_order_under_reverse_latency(tmp_path, monkeypatch):
    """5 parallel reads with latency 5s/4s/3s/2s/1s — completion order is
    reversed but output order must match input order."""
    completion_log: list[str] = []

    async def fake_execute_tool(name, args, agent_id=None, user_id=None, session_id=""):
        delay = args["delay"]
        await asyncio.sleep(delay)
        completion_log.append(args["mark"])
        return f"result-{args['mark']}"

    # Use read_file (parallelizable) and stub execute_tool
    with patch("app.services.agent_tools.execute_tool", side_effect=fake_execute_tool):
        ctx = ToolExecutionContext(agent_id=None, user_id=None)  # no agent_id → no truncation
        tcs = [
            _tc("c1", "read_file", {"delay": 0.05, "mark": "A"}),
            _tc("c2", "read_file", {"delay": 0.04, "mark": "B"}),
            _tc("c3", "read_file", {"delay": 0.03, "mark": "C"}),
            _tc("c4", "read_file", {"delay": 0.02, "mark": "D"}),
            _tc("c5", "read_file", {"delay": 0.01, "mark": "E"}),
        ]
        results = await execute_tool_calls(tcs, ctx)

    # Completion order is reversed (E,D,C,B,A finish in that order)
    assert completion_log == ["E", "D", "C", "B", "A"]
    # But output order matches input
    assert [m.tool_call_id for m in results] == ["c1", "c2", "c3", "c4", "c5"]
    # Content also lines up
    assert [m.content for m in results] == [
        "result-A", "result-B", "result-C", "result-D", "result-E",
    ]


@pytest.mark.asyncio
async def test_parallel_reads_actually_run_concurrently(tmp_path):
    """Three reads at 80ms each should finish in ~80ms total, not 240ms."""
    async def fake_execute(name, args, agent_id=None, user_id=None, session_id=""):
        await asyncio.sleep(0.08)
        return "ok"

    with patch("app.services.agent_tools.execute_tool", side_effect=fake_execute):
        ctx = ToolExecutionContext(agent_id=None, user_id=None)
        tcs = [_tc(f"c{i}", "read_file", {"path": f"/p{i}"}) for i in range(3)]
        t0 = time.monotonic()
        await execute_tool_calls(tcs, ctx)
        elapsed = time.monotonic() - t0

    # Generous bound: 3x80ms=240ms serial, parallel should be < 200ms
    assert elapsed < 0.20, f"Parallel exec took {elapsed:.3f}s, expected < 0.20s"


# ── Mixed parallel + serial ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mixed_parallel_and_serial_preserves_issued_order(tmp_path):
    """Mixed reads + writes interleave in **issued order**. Only contiguous
    runs of read-only tools batch into ``asyncio.gather`` — a side-effecting
    tool flushes the pending batch first.

    With ``[read, write, read, write]`` no two tools are contiguous-parallel,
    so each runs alone in order: ``read → write → read → write``.
    """
    completion_log: list[str] = []

    async def fake_execute(name, args, agent_id=None, user_id=None, session_id=""):
        delay = args.get("delay", 0)
        await asyncio.sleep(delay)
        completion_log.append(args["mark"])
        return f"done-{args['mark']}"

    with patch("app.services.agent_tools.execute_tool", side_effect=fake_execute):
        ctx = ToolExecutionContext(agent_id=None, user_id=None)
        tcs = [
            _tc("c1", "read_file", {"delay": 0.05, "mark": "READ-A"}),
            _tc("c2", "write_file", {"delay": 0.01, "mark": "WRITE-B"}),
            _tc("c3", "read_file", {"delay": 0.03, "mark": "READ-C"}),
            _tc("c4", "write_file", {"delay": 0.01, "mark": "WRITE-D"}),
        ]
        results = await execute_tool_calls(tcs, ctx)

    # Output order matches input
    assert [m.tool_call_id for m in results] == ["c1", "c2", "c3", "c4"]
    # Execution order matches input — write_file never runs before its
    # preceding read, and a later read sees the previous write's effect.
    assert completion_log == ["READ-A", "WRITE-B", "READ-C", "WRITE-D"]


@pytest.mark.asyncio
async def test_write_then_read_executes_write_first():
    """Critical correctness regression test. The previous algorithm split
    parallel/serial up front and ran ALL parallel reads before ANY serial
    writes — meaning a model-issued ``[write_file, read_file]`` pair ran in
    REVERSE order. This pins the fix: writes complete before subsequent
    reads start, so the read sees the write's effect.
    """
    completion_log: list[str] = []

    async def fake_execute(name, args, agent_id=None, user_id=None, session_id=""):
        completion_log.append(name)
        await asyncio.sleep(0.01)
        return f"ok-{name}"

    with patch("app.services.agent_tools.execute_tool", side_effect=fake_execute):
        ctx = ToolExecutionContext(agent_id=None, user_id=None)
        tcs = [
            _tc("w1", "write_file", {"path": "x", "content": "v1"}),
            _tc("r1", "read_file", {"path": "x"}),
        ]
        await execute_tool_calls(tcs, ctx)

    # Write must appear in the log BEFORE read.
    assert completion_log == ["write_file", "read_file"], (
        f"Execution reordered side-effecting + read-only tools: {completion_log}. "
        "A2A wake / file edit-then-verify flows depend on issued-order semantics."
    )


@pytest.mark.asyncio
async def test_contiguous_reads_still_batch_concurrently(tmp_path):
    """The order-preserving fix must not regress the parallelism win for
    contiguous read batches. 3 reads followed by 1 write: reads overlap,
    write runs only after all reads complete."""
    timeline: list[tuple[str, str, float]] = []

    async def fake_execute(name, args, agent_id=None, user_id=None, session_id=""):
        timeline.append(("start", args["mark"], time.monotonic()))
        await asyncio.sleep(0.05)
        timeline.append(("end", args["mark"], time.monotonic()))
        return "ok"

    with patch("app.services.agent_tools.execute_tool", side_effect=fake_execute):
        ctx = ToolExecutionContext(agent_id=None, user_id=None)
        tcs = [
            _tc("r1", "read_file", {"mark": "R1"}),
            _tc("r2", "read_file", {"mark": "R2"}),
            _tc("r3", "read_file", {"mark": "R3"}),
            _tc("w1", "write_file", {"mark": "W1"}),
        ]
        t0 = time.monotonic()
        await execute_tool_calls(tcs, ctx)
        elapsed = time.monotonic() - t0

    # 3 reads in parallel ~50ms + 1 write ~50ms ≈ 100ms total.
    # Serial would be 4×50=200ms.
    assert elapsed < 0.15, f"Expected < 0.15s, got {elapsed:.3f}s"

    # All 3 reads start before any of them ends (true overlap)
    starts = {m: t for k, m, t in timeline if k == "start"}
    ends = {m: t for k, m, t in timeline if k == "end"}
    assert starts["R2"] < ends["R1"]
    assert starts["R3"] < ends["R1"]
    # W1 must start AFTER all reads have completed
    assert starts["W1"] >= max(ends["R1"], ends["R2"], ends["R3"]) - 0.005


# ── Exception isolation ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exception_in_one_parallel_does_not_kill_siblings(tmp_path):
    async def fake_execute(name, args, agent_id=None, user_id=None, session_id=""):
        if args.get("fail"):
            raise RuntimeError("kaboom")
        return f"ok-{args.get('mark', '?')}"

    with patch("app.services.agent_tools.execute_tool", side_effect=fake_execute):
        ctx = ToolExecutionContext(agent_id=None, user_id=None)
        tcs = [
            _tc("c1", "read_file", {"mark": "A"}),
            _tc("c2", "read_file", {"fail": True}),
            _tc("c3", "read_file", {"mark": "C"}),
        ]
        results = await execute_tool_calls(tcs, ctx)

    assert len(results) == 3
    assert results[0].content == "ok-A"
    assert "Error:" in str(results[1].content)
    assert "kaboom" in str(results[1].content)
    assert results[2].content == "ok-C"


@pytest.mark.asyncio
async def test_exception_in_serial_continues_to_next(tmp_path):
    """Serial bucket: one tool raises, the next still runs."""
    async def fake_execute(name, args, agent_id=None, user_id=None, session_id=""):
        if args.get("fail"):
            raise ValueError("bad arg")
        return f"ok-{args.get('mark', '?')}"

    with patch("app.services.agent_tools.execute_tool", side_effect=fake_execute):
        ctx = ToolExecutionContext(agent_id=None, user_id=None)
        tcs = [
            _tc("c1", "write_file", {"fail": True}),
            _tc("c2", "write_file", {"mark": "B"}),
        ]
        results = await execute_tool_calls(tcs, ctx)

    assert len(results) == 2
    assert "Error: ValueError" in str(results[0].content)
    assert results[1].content == "ok-B"


# ── Empty-args guard ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_args_guard_blocks_required_arg_tool():
    """write_file with empty args must NOT execute; returns guidance string."""
    called = []

    async def fake_execute(*args, **kwargs):
        called.append(args)
        return "should not happen"

    with patch("app.services.agent_tools.execute_tool", side_effect=fake_execute):
        ctx = ToolExecutionContext(agent_id=None, user_id=None)
        tcs = [_tc("c1", "write_file", {})]
        results = await execute_tool_calls(tcs, ctx)

    assert called == []  # execute_tool was never called
    assert "empty arguments" in str(results[0].content)
    assert "retry" in str(results[0].content).lower()


@pytest.mark.asyncio
async def test_empty_args_ok_for_non_required_tool():
    async def fake_execute(name, args, agent_id=None, user_id=None, session_id=""):
        return "fine"

    with patch("app.services.agent_tools.execute_tool", side_effect=fake_execute):
        ctx = ToolExecutionContext(agent_id=None, user_id=None)
        # list_triggers doesn't require args
        tcs = [_tc("c1", "list_triggers", {})]
        results = await execute_tool_calls(tcs, ctx)

    assert results[0].content == "fine"


# ── Pre-execute hook (heartbeat plaza rate limit) ───────────────────────


@pytest.mark.asyncio
async def test_pre_execute_hook_short_circuits():
    """Hook returning a string blocks execute_tool and uses the string as result."""
    called_execute = []

    async def fake_execute(*args, **kwargs):
        called_execute.append(args)
        return "should-not-run"

    def hook(name, args):
        if name == "plaza_create_post":
            return "[BLOCKED] rate-limited"
        return None

    with patch("app.services.agent_tools.execute_tool", side_effect=fake_execute):
        ctx = ToolExecutionContext(
            agent_id=None, user_id=None, pre_execute_hook=hook,
        )
        tcs = [_tc("c1", "plaza_create_post", {"content": "x"})]
        results = await execute_tool_calls(tcs, ctx)

    assert called_execute == []
    assert "[BLOCKED]" in str(results[0].content)


@pytest.mark.asyncio
async def test_pre_execute_hook_passes_through_when_none():
    async def fake_execute(name, args, agent_id=None, user_id=None, session_id=""):
        return "ran"

    def hook(name, args):
        return None  # don't short-circuit anything

    with patch("app.services.agent_tools.execute_tool", side_effect=fake_execute):
        ctx = ToolExecutionContext(
            agent_id=None, user_id=None, pre_execute_hook=hook,
        )
        tcs = [_tc("c1", "write_file", {"path": "x", "content": "y"})]
        results = await execute_tool_calls(tcs, ctx)

    assert results[0].content == "ran"


# ── on_tool_call callback fires both running + done ─────────────────────


@pytest.mark.asyncio
async def test_on_tool_call_fires_running_and_done():
    events: list[dict] = []

    async def fake_execute(name, args, agent_id=None, user_id=None, session_id=""):
        return "result"

    async def on_tc(payload):
        events.append(payload)

    with patch("app.services.agent_tools.execute_tool", side_effect=fake_execute):
        ctx = ToolExecutionContext(
            agent_id=None, user_id=None, on_tool_call=on_tc,
        )
        tcs = [_tc("c1", "read_file", {"path": "x"})]
        await execute_tool_calls(tcs, ctx)

    statuses = [e["status"] for e in events]
    assert statuses == ["running", "done"]
    assert events[1]["result"] == "result"


@pytest.mark.asyncio
async def test_on_tool_call_callback_exception_does_not_kill_tool():
    """If the callback raises, the tool result still flows through."""
    async def fake_execute(name, args, agent_id=None, user_id=None, session_id=""):
        return "result"

    async def bad_on_tc(payload):
        raise RuntimeError("callback broken")

    with patch("app.services.agent_tools.execute_tool", side_effect=fake_execute):
        ctx = ToolExecutionContext(
            agent_id=None, user_id=None, on_tool_call=bad_on_tc,
        )
        tcs = [_tc("c1", "read_file", {"path": "x"})]
        results = await execute_tool_calls(tcs, ctx)

    assert results[0].content == "result"


# ── Empty input ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_tool_calls_returns_empty():
    ctx = ToolExecutionContext(agent_id=None, user_id=None)
    results = await execute_tool_calls([], ctx)
    assert results == []
