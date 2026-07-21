"""Pure Tool Exchange normalization and context-window selection.

The new Runtime must never repair provider history by deleting one side of a
tool exchange.  This module groups generic message dictionaries into atomic
blocks, resolves incomplete groups against a caller-provided execution ledger,
and selects recent context without emitting orphan calls or results.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal


BlockKind = Literal[
    "normal",
    "tool_exchange",
    "pending_tool_exchange",
    "malformed_tool_exchange",
]
BlockAction = Literal[
    "emit",
    "retry_model",
    "summarize",
    "summarize_then_retry_model",
    "block_reconcile",
    "require_confirmation",
]
Message = dict[str, Any]
Ledger = Mapping[str, Mapping[str, Any]]
TokenCounter = Callable[[Sequence[Mapping[str, Any]]], int]


class ToolExchangeIntegrityError(RuntimeError):
    """Message history is unsafe to send to a model without reconciliation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ToolCallExecutionSummary:
    """Structured execution fact retained when an exchange leaves active context."""

    tool_call_id: str
    tool_name: str
    execution_status: str
    side_effect_classification: str
    result_summary: str | None
    result_ref: str | None
    request_ref: str | None


@dataclass(frozen=True, slots=True)
class ToolExchangeCompactionSummary:
    """Run-summary payload for one atomic Tool Exchange group."""

    assistant_message_id: str | None
    reason: str
    calls: tuple[ToolCallExecutionSummary, ...]
    tool_reexecution_allowed: bool = False


@dataclass(frozen=True, slots=True)
class MessageBlock:
    """One indivisible unit of Runtime message context."""

    kind: BlockKind
    messages: tuple[Message, ...]
    message_ids: tuple[str, ...]
    assistant_message_id: str | None = None
    call_ids: tuple[str, ...] = ()
    missing_call_ids: tuple[str, ...] = ()
    action: BlockAction = "emit"
    retry_model: bool = False
    blocked: bool = False
    requires_confirmation: bool = False
    compaction_summary: ToolExchangeCompactionSummary | None = None
    tool_reexecution_allowed: bool = False

    @property
    def message_count(self) -> int:
        return len(self.messages)


@dataclass(frozen=True, slots=True)
class RecentBlockSelection:
    """Atomic recent window plus deterministic Runtime follow-up directives."""

    messages: tuple[Message, ...]
    blocks: tuple[MessageBlock, ...]
    omitted_blocks: tuple[MessageBlock, ...]
    compaction_summaries: tuple[ToolExchangeCompactionSummary, ...]
    retry_model: bool
    blocked: bool
    requires_confirmation: bool
    tool_reexecution_call_ids: tuple[str, ...] = ()


def _stable_message_id(message: Mapping[str, Any]) -> str:
    message_id = message.get("id")
    alternate_id = message.get("message_id")
    if message_id is not None and alternate_id is not None and message_id != alternate_id:
        raise ToolExchangeIntegrityError(
            "conflicting_message_id",
            "message id and message_id disagree",
        )
    value = message_id if message_id is not None else alternate_id
    if not isinstance(value, str) or not value.strip():
        raise ToolExchangeIntegrityError(
            "missing_message_id",
            "every Runtime message must have a stable non-empty id",
        )
    return value


def _stable_call_id(call: Mapping[str, Any]) -> str:
    value = call.get("id")
    if not isinstance(value, str) or not value.strip():
        raise ToolExchangeIntegrityError(
            "missing_tool_call_id",
            "every assistant tool call must have a stable non-empty id",
        )
    return value


def _stable_result_call_id(message: Mapping[str, Any]) -> str:
    tool_call_id = message.get("tool_call_id")
    alternate_id = message.get("call_id")
    if (
        tool_call_id is not None
        and alternate_id is not None
        and tool_call_id != alternate_id
    ):
        raise ToolExchangeIntegrityError(
            "conflicting_tool_result_id",
            "tool result tool_call_id and call_id disagree",
        )
    value = tool_call_id if tool_call_id is not None else alternate_id
    if not isinstance(value, str) or not value.strip():
        raise ToolExchangeIntegrityError(
            "missing_tool_call_id",
            "every tool result must reference a stable non-empty tool call id",
        )
    return value


def _tool_calls(message: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw_calls = message.get("tool_calls")
    if raw_calls is None or raw_calls == []:
        return ()
    if message.get("role") != "assistant" or not isinstance(raw_calls, list):
        raise ToolExchangeIntegrityError(
            "malformed_tool_calls",
            "tool_calls must be a non-empty list on an assistant message",
        )
    if not raw_calls or not all(isinstance(call, Mapping) for call in raw_calls):
        raise ToolExchangeIntegrityError(
            "malformed_tool_calls",
            "assistant tool_calls must contain mapping objects",
        )
    return tuple(raw_calls)


def _is_tool_result(message: Mapping[str, Any]) -> bool:
    return message.get("role") in {"tool", "tool_result"}


def _tool_name(call: Mapping[str, Any], ledger_entry: Mapping[str, Any]) -> str:
    function = call.get("function")
    function_name = function.get("name") if isinstance(function, Mapping) else None
    value = (
        ledger_entry.get("tool_name")
        or function_name
        or call.get("name")
        or "unknown_tool"
    )
    return str(value)


def _short_result(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= 500 else f"{text[:497]}..."


def _summary_for_exchange(
    *,
    assistant_message_id: str | None,
    calls: Sequence[Mapping[str, Any]],
    results: Mapping[str, Mapping[str, Any]],
    ledger: Ledger,
    reason: str,
) -> ToolExchangeCompactionSummary:
    summaries: list[ToolCallExecutionSummary] = []
    for call in calls:
        call_id = _stable_call_id(call)
        entry = ledger.get(call_id, {})
        result = results.get(call_id, {})
        status = entry.get("status")
        if status is None:
            status = "succeeded" if result else "unknown"
        summaries.append(
            ToolCallExecutionSummary(
                tool_call_id=call_id,
                tool_name=_tool_name(call, entry),
                execution_status=str(status),
                side_effect_classification=str(
                    entry.get("side_effect_classification")
                    or entry.get("side_effect")
                    or "unknown"
                ),
                result_summary=_short_result(
                    entry.get("result_summary")
                    if entry.get("result_summary") is not None
                    else result.get("content")
                ),
                result_ref=(
                    str(entry.get("result_ref") or result.get("result_ref"))
                    if entry.get("result_ref") or result.get("result_ref")
                    else None
                ),
                request_ref=(
                    str(entry.get("request_ref"))
                    if entry.get("request_ref") is not None
                    else None
                ),
            )
        )
    return ToolExchangeCompactionSummary(
        assistant_message_id=assistant_message_id,
        reason=reason,
        calls=tuple(summaries),
    )


def _summary_for_orphan_result(
    message: Mapping[str, Any],
    ledger: Ledger,
) -> ToolExchangeCompactionSummary:
    call_id = _stable_result_call_id(message)
    entry = ledger.get(call_id, {})
    synthetic_call: Mapping[str, Any] = {
        "id": call_id,
        "name": entry.get("tool_name") or message.get("name") or "unknown_tool",
    }
    return _summary_for_exchange(
        assistant_message_id=(
            str(entry.get("assistant_message_id"))
            if entry.get("assistant_message_id") is not None
            else None
        ),
        calls=(synthetic_call,),
        results={call_id: message},
        ledger=ledger,
        reason="orphan_result",
    )


def _guard_observed_results(
    *,
    messages: tuple[Message, ...],
    message_ids: tuple[str, ...],
    assistant_message_id: str,
    calls: tuple[Mapping[str, Any], ...],
    results: Mapping[str, Mapping[str, Any]],
    missing_call_ids: tuple[str, ...],
    ledger: Ledger,
) -> MessageBlock | None:
    """Fail closed when persisted results contradict execution-ledger state."""
    call_ids = tuple(_stable_call_id(call) for call in calls)
    observed_entries = {
        call_id: ledger[call_id]
        for call_id in call_ids
        if call_id in results and call_id in ledger
    }
    if any(
        entry.get("status") == "unknown"
        and bool(entry.get("may_have_side_effect", True))
        for entry in observed_entries.values()
    ):
        return MessageBlock(
            kind="pending_tool_exchange",
            messages=messages,
            message_ids=message_ids,
            assistant_message_id=assistant_message_id,
            call_ids=call_ids,
            missing_call_ids=missing_call_ids,
            action="require_confirmation",
            blocked=True,
            requires_confirmation=True,
        )

    if any(
        entry.get("status") in {"started", "unknown"}
        for entry in observed_entries.values()
    ):
        return MessageBlock(
            kind="pending_tool_exchange",
            messages=messages,
            message_ids=message_ids,
            assistant_message_id=assistant_message_id,
            call_ids=call_ids,
            missing_call_ids=missing_call_ids,
            action="block_reconcile",
            blocked=True,
        )

    valid_terminal_statuses = {"succeeded", "failed"}
    if any(
        entry.get("status") not in valid_terminal_statuses
        for entry in observed_entries.values()
    ):
        return MessageBlock(
            kind="malformed_tool_exchange",
            messages=messages,
            message_ids=message_ids,
            assistant_message_id=assistant_message_id,
            call_ids=call_ids,
            missing_call_ids=missing_call_ids,
            action="block_reconcile",
            blocked=True,
        )
    return None


def _resolve_incomplete_exchange(
    *,
    messages: tuple[Message, ...],
    message_ids: tuple[str, ...],
    assistant_message_id: str,
    calls: tuple[Mapping[str, Any], ...],
    results: Mapping[str, Mapping[str, Any]],
    missing_call_ids: tuple[str, ...],
    ledger: Ledger,
) -> MessageBlock:
    call_ids = tuple(_stable_call_id(call) for call in calls)
    observed_guard = _guard_observed_results(
        messages=messages,
        message_ids=message_ids,
        assistant_message_id=assistant_message_id,
        calls=calls,
        results=results,
        missing_call_ids=missing_call_ids,
        ledger=ledger,
    )
    if observed_guard is not None:
        return observed_guard

    missing_statuses: dict[str, str] = {}
    missing_entries: list[str] = []
    for call_id in missing_call_ids:
        entry = ledger.get(call_id)
        if entry is None or not isinstance(entry.get("status"), str):
            missing_entries.append(call_id)
        else:
            missing_statuses[call_id] = str(entry["status"])

    if missing_entries:
        return MessageBlock(
            kind="malformed_tool_exchange",
            messages=messages,
            message_ids=message_ids,
            assistant_message_id=assistant_message_id,
            call_ids=call_ids,
            missing_call_ids=missing_call_ids,
            action="block_reconcile",
            blocked=True,
        )

    unknown_with_side_effect = any(
        status == "unknown"
        and bool(ledger.get(call_id, {}).get("may_have_side_effect", True))
        for call_id, status in missing_statuses.items()
    )
    if unknown_with_side_effect:
        return MessageBlock(
            kind="pending_tool_exchange",
            messages=messages,
            message_ids=message_ids,
            assistant_message_id=assistant_message_id,
            call_ids=call_ids,
            missing_call_ids=missing_call_ids,
            action="require_confirmation",
            blocked=True,
            requires_confirmation=True,
        )
    if any(status in {"started", "unknown", "failed"} for status in missing_statuses.values()):
        return MessageBlock(
            kind="pending_tool_exchange",
            messages=messages,
            message_ids=message_ids,
            assistant_message_id=assistant_message_id,
            call_ids=call_ids,
            missing_call_ids=missing_call_ids,
            action="block_reconcile",
            blocked=True,
        )

    invalid_statuses = {
        status
        for status in missing_statuses.values()
        if status not in {"not_started", "succeeded"}
    }
    if invalid_statuses:
        return MessageBlock(
            kind="malformed_tool_exchange",
            messages=messages,
            message_ids=message_ids,
            assistant_message_id=assistant_message_id,
            call_ids=call_ids,
            missing_call_ids=missing_call_ids,
            action="block_reconcile",
            blocked=True,
        )

    cancelled_before_execution = bool(missing_call_ids) and all(
        missing_statuses.get(call_id) == "not_started"
        and ledger.get(call_id, {}).get("cancelled_before_execution") is True
        for call_id in missing_call_ids
    )
    if cancelled_before_execution:
        summary = _summary_for_exchange(
            assistant_message_id=assistant_message_id,
            calls=calls,
            results=results,
            ledger=ledger,
            reason="cancelled_before_execution",
        )
        return MessageBlock(
            kind="pending_tool_exchange",
            messages=messages,
            message_ids=message_ids,
            assistant_message_id=assistant_message_id,
            call_ids=call_ids,
            missing_call_ids=missing_call_ids,
            action="summarize",
            compaction_summary=summary,
        )

    has_execution_fact = bool(results) or any(
        status == "succeeded" for status in missing_statuses.values()
    )
    if not has_execution_fact:
        return MessageBlock(
            kind="pending_tool_exchange",
            messages=messages,
            message_ids=message_ids,
            assistant_message_id=assistant_message_id,
            call_ids=call_ids,
            missing_call_ids=missing_call_ids,
            action="retry_model",
            retry_model=True,
        )

    summary = _summary_for_exchange(
        assistant_message_id=assistant_message_id,
        calls=calls,
        results=results,
        ledger=ledger,
        reason="partial_parallel_exchange" if results else "succeeded_result_missing",
    )
    return MessageBlock(
        kind="pending_tool_exchange",
        messages=messages,
        message_ids=message_ids,
        assistant_message_id=assistant_message_id,
        call_ids=call_ids,
        missing_call_ids=missing_call_ids,
        action=(
            "summarize_then_retry_model"
            if any(status == "not_started" for status in missing_statuses.values())
            else "summarize"
        ),
        retry_model=True,
        compaction_summary=summary,
    )


def _validate_ids(messages: Sequence[Mapping[str, Any]]) -> None:
    message_ids: set[str] = set()
    proposed_call_ids: set[str] = set()
    result_call_ids: set[str] = set()
    for message in messages:
        message_id = _stable_message_id(message)
        if message_id in message_ids:
            raise ToolExchangeIntegrityError(
                "duplicate_message_id",
                f"duplicate Runtime message id: {message_id}",
            )
        message_ids.add(message_id)

        for call in _tool_calls(message):
            call_id = _stable_call_id(call)
            if call_id in proposed_call_ids:
                raise ToolExchangeIntegrityError(
                    "duplicate_tool_call_id",
                    f"duplicate assistant tool call id: {call_id}",
                )
            proposed_call_ids.add(call_id)

        if _is_tool_result(message):
            call_id = _stable_result_call_id(message)
            if call_id in result_call_ids:
                raise ToolExchangeIntegrityError(
                    "duplicate_tool_result_id",
                    f"duplicate tool result for call id: {call_id}",
                )
            result_call_ids.add(call_id)


def build_message_blocks(
    messages: Sequence[Mapping[str, Any]],
    tool_execution_ledger: Ledger | None = None,
) -> tuple[MessageBlock, ...]:
    """Normalize messages into atomic blocks without mutating the input.

    Incomplete assistant proposals are never returned as emit-ready context.
    Their action is derived from the ledger; an absent ledger record is an
    unknown execution state and therefore blocks for reconciliation.
    """

    ledger = tool_execution_ledger or {}
    _validate_ids(messages)
    copied_messages = tuple(dict(message) for message in messages)
    blocks: list[MessageBlock] = []
    index = 0
    while index < len(copied_messages):
        message = copied_messages[index]
        message_id = _stable_message_id(message)
        calls = _tool_calls(message)
        if calls:
            call_ids = tuple(_stable_call_id(call) for call in calls)
            expected = set(call_ids)
            results: dict[str, Mapping[str, Any]] = {}
            group_messages = [message]
            group_message_ids = [message_id]
            cursor = index + 1
            while cursor < len(copied_messages) and _is_tool_result(
                copied_messages[cursor]
            ):
                result = copied_messages[cursor]
                result_call_id = _stable_result_call_id(result)
                if result_call_id not in expected:
                    break
                results[result_call_id] = result
                group_messages.append(result)
                group_message_ids.append(_stable_message_id(result))
                cursor += 1

            missing_call_ids = tuple(
                call_id for call_id in call_ids if call_id not in results
            )
            if missing_call_ids:
                blocks.append(
                    _resolve_incomplete_exchange(
                        messages=tuple(group_messages),
                        message_ids=tuple(group_message_ids),
                        assistant_message_id=message_id,
                        calls=calls,
                        results=results,
                        missing_call_ids=missing_call_ids,
                        ledger=ledger,
                    )
                )
            else:
                observed_guard = _guard_observed_results(
                    messages=tuple(group_messages),
                    message_ids=tuple(group_message_ids),
                    assistant_message_id=message_id,
                    calls=calls,
                    results=results,
                    missing_call_ids=(),
                    ledger=ledger,
                )
                blocks.append(
                    observed_guard
                    or MessageBlock(
                        kind="tool_exchange",
                        messages=tuple(group_messages),
                        message_ids=tuple(group_message_ids),
                        assistant_message_id=message_id,
                        call_ids=call_ids,
                    )
                )
            index = cursor
            continue

        if _is_tool_result(message):
            call_id = _stable_result_call_id(message)
            entry = ledger.get(call_id)
            if entry is None:
                blocks.append(
                    MessageBlock(
                        kind="malformed_tool_exchange",
                        messages=(message,),
                        message_ids=(message_id,),
                        call_ids=(call_id,),
                        action="block_reconcile",
                        blocked=True,
                    )
                )
                index += 1
                continue

            status = entry.get("status")
            unknown_side_effect = status == "unknown" and bool(
                entry.get("may_have_side_effect", True)
            )
            needs_reconciliation = status not in {"succeeded", "failed"}
            action: BlockAction = "summarize"
            blocks.append(
                MessageBlock(
                    kind="malformed_tool_exchange",
                    messages=(message,),
                    message_ids=(message_id,),
                    assistant_message_id=(
                        str(entry.get("assistant_message_id"))
                        if entry.get("assistant_message_id") is not None
                        else None
                    ),
                    call_ids=(call_id,),
                    action=(
                        "require_confirmation"
                        if unknown_side_effect
                        else "block_reconcile"
                        if needs_reconciliation
                        else action
                    ),
                    retry_model=not unknown_side_effect and not needs_reconciliation,
                    blocked=unknown_side_effect or needs_reconciliation,
                    requires_confirmation=unknown_side_effect,
                    compaction_summary=(
                        None
                        if needs_reconciliation
                        else _summary_for_orphan_result(message, ledger)
                    ),
                )
            )
            index += 1
            continue

        blocks.append(
            MessageBlock(
                kind="normal",
                messages=(message,),
                message_ids=(message_id,),
            )
        )
        index += 1

    return tuple(blocks)


def _count_tokens(
    messages: Sequence[Mapping[str, Any]],
    *,
    token_counter: TokenCounter,
) -> int:
    count = token_counter(messages)
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError("token_counter must return a non-negative integer")
    return count


def validate_tool_exchange_integrity(
    messages: Sequence[Mapping[str, Any]],
) -> None:
    """Fail closed unless every emitted call has exactly one adjacent result."""

    blocks = build_message_blocks(messages, {})
    unsafe = [block for block in blocks if block.kind not in {"normal", "tool_exchange"}]
    if unsafe:
        call_ids = sorted(
            call_id for block in unsafe for call_id in block.call_ids
        )
        raise ToolExchangeIntegrityError(
            "incomplete_tool_exchange",
            f"messages contain incomplete or orphan Tool Exchange IDs: {call_ids}",
        )


def select_recent_blocks(
    blocks: Sequence[MessageBlock],
    *,
    target_messages: int | None = None,
    token_budget: int | None = None,
    token_counter: TokenCounter | None = None,
    tool_execution_ledger: Ledger | None = None,
) -> RecentBlockSelection:
    """Select recent blocks backward while preserving Tool Exchange atomicity."""

    if target_messages is not None and target_messages <= 0:
        raise ValueError("target_messages must be greater than zero")
    if (token_budget is None) != (token_counter is None):
        raise ValueError("token_budget and token_counter must be provided together")
    if token_budget is not None and token_budget < 0:
        raise ValueError("token_budget must not be negative")

    ledger = tool_execution_ledger or {}
    selected_reversed: list[MessageBlock] = []
    omitted_reversed: list[MessageBlock] = []
    summaries_reversed: list[ToolExchangeCompactionSummary] = []
    selected_message_count = 0
    selected_messages_reversed: list[Message] = []
    retry_model = False
    blocked = False
    requires_confirmation = False
    window_closed = False

    def omit(block: MessageBlock) -> None:
        nonlocal retry_model, blocked, requires_confirmation
        omitted_reversed.append(block)
        if block.compaction_summary is not None:
            summaries_reversed.append(block.compaction_summary)
        retry_model = retry_model or block.retry_model
        blocked = blocked or block.blocked
        requires_confirmation = requires_confirmation or block.requires_confirmation

    for block in reversed(blocks):
        if (
            window_closed
            or target_messages is not None
            and selected_message_count >= target_messages
        ):
            window_closed = True
            omit(block)
            continue

        if block.action != "emit":
            omit(block)
            continue

        candidate_blocks = [block, *reversed(selected_reversed)]
        candidate_messages = tuple(
            message for candidate in candidate_blocks for message in candidate.messages
        )
        if token_budget is not None and token_counter is not None:
            candidate_tokens = _count_tokens(
                candidate_messages,
                token_counter=token_counter,
            )
            if candidate_tokens > token_budget:
                omit(block)
                window_closed = True
                if block.kind == "tool_exchange":
                    calls = tuple(
                        call
                        for call in _tool_calls(block.messages[0])
                    )
                    results = {
                        _stable_result_call_id(message): message
                        for message in block.messages[1:]
                    }
                    summaries_reversed.append(
                        _summary_for_exchange(
                            assistant_message_id=block.assistant_message_id,
                            calls=calls,
                            results=results,
                            ledger=ledger,
                            reason="complete_exchange_over_token_budget",
                        )
                    )
                    retry_model = True
                    continue
                continue

        selected_reversed.append(block)
        selected_messages_reversed.extend(reversed(block.messages))
        selected_message_count += block.message_count

    selected_blocks = tuple(reversed(selected_reversed))
    selected_messages = tuple(reversed(selected_messages_reversed))
    validate_tool_exchange_integrity(selected_messages)
    return RecentBlockSelection(
        messages=selected_messages,
        blocks=selected_blocks,
        omitted_blocks=tuple(reversed(omitted_reversed)),
        compaction_summaries=tuple(reversed(summaries_reversed)),
        retry_model=retry_model,
        blocked=blocked,
        requires_confirmation=requires_confirmation,
    )


def build_recent_tool_safe_window(
    messages: Sequence[Mapping[str, Any]],
    tool_execution_ledger: Ledger | None = None,
    *,
    target_messages: int | None = None,
    token_budget: int | None = None,
    token_counter: TokenCounter | None = None,
) -> RecentBlockSelection:
    """Convenience entrypoint for normalization plus atomic recent selection."""

    ledger = tool_execution_ledger or {}
    blocks = build_message_blocks(messages, ledger)
    return select_recent_blocks(
        blocks,
        target_messages=target_messages,
        token_budget=token_budget,
        token_counter=token_counter,
        tool_execution_ledger=ledger,
    )


__all__ = [
    "MessageBlock",
    "RecentBlockSelection",
    "ToolCallExecutionSummary",
    "ToolExchangeCompactionSummary",
    "ToolExchangeIntegrityError",
    "build_message_blocks",
    "build_recent_tool_safe_window",
    "select_recent_blocks",
    "validate_tool_exchange_integrity",
]
