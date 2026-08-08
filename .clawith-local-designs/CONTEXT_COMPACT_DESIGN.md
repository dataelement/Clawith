# Context Compact Design

This document captures the agreed code design for the next compact PR stacked
after PR #572. It only covers the first production-ready compact iteration.

## Background

The current history work is split into two prior layers:

- PR #564 preserves API-valid tool-call history blocks during message-count
  truncation.
- PR #572 adds an 80% token-budget fallback based on backend model
  `context_window_tokens`, with no provider/model-name hardcoding.

The next layer should add summary compaction before falling back to drop-only
truncation.

## Goals

- Keep the existing 80% trigger from PR #572.
- Use the model window from backend model metadata/config only.
- Prepare request messages against the model that will actually be called,
  including fallback models.
- Preserve recent user intent and API-safe recent blocks so long-running tasks
  can continue.
- Keep the first compact implementation close to Codex/Claude Code mainstream
  behavior before adding Clawith-specific long-running task memory.

## Non-Goals For The First Compact PR

- No workspace checkpoint files.
- No persistent compact entries in the database.
- No frontend controls.
- No dedicated compact model setting.
- No Claude Code-style context collapse, session memory compact, or
  microcompact.
- No synthetic tool results.

## Reference Shape

The implementation should follow the simple mainstream compact pattern:

- Codex: compact is triggered from the current model metadata, produces a
  continuation summary, and replaces the prompt history before the next model
  call.
- Claude Code: compact summary is inserted as a conversation continuation
  message, while recent messages can remain verbatim.
- Pi: compact keeps a recent raw tail and summarizes older history, avoiding
  cuts at tool results.

For Clawith's first PR, use this shape:

```text
old safe blocks -> compact summary
recent safe blocks -> preserved verbatim
final request -> compact summary + recent safe blocks
```

## Trigger

Compaction uses the same budget as PR #572:

```text
token_budget = context_window_tokens * 0.8
trigger when estimated_conversation_tokens > token_budget
```

If `context_window_tokens` is missing, do not compact and do not guess a window
from provider or model names. Keep the existing message-count truncation path.

The token estimate should use the same local estimator introduced for PR #572.
The threshold is deliberately 80%, not Codex's 90%, because Clawith must support
large-window but lower-capability models that degrade before the advertised
context limit.

## Per-Model Preparation

Message preparation must run for the model that is about to be called.

This is required for primary/fallback safety:

1. Prepare messages with the primary model's `context_window_tokens`.
2. If primary fails and fallback is used, prepare messages again with the
   fallback model's `context_window_tokens`.

This mirrors Codex's approach of evaluating context limits at turn/sampling
boundaries from the current model metadata.

Implementation should prefer a per-model preparation callback in the failover
path rather than preparing once outside `call_llm_with_failover`.

Required behavior:

```text
primary call:
  prepare_messages(primary_model)
  call primary

fallback call:
  prepare_messages(fallback_model)
  call fallback
```

It is not acceptable to prepare once with the primary model and reuse that
prompt for a smaller fallback model.

## Compact Scope

Use the existing safe-block strategy from PR #564/#572:

- Assistant `tool_calls` and matching `role="tool"` results are atomic.
- Blocks are kept whole or dropped whole.
- Orphan or incomplete tool blocks are dropped.
- No fake/synthetic tool results are generated.

When compact triggers:

```text
messages_to_keep = recent API-safe blocks within recent_keep_tokens
messages_to_summarize = API-safe blocks before messages_to_keep
```

The latest user message must be kept even when it exceeds the recent budget.

The preserved recent tail must include enough raw context to keep the current
task moving even if the compact summary is lossy. This means the compact summary
must not be the only carrier of the latest user intent.

Recent budget:

```text
recent_keep_tokens = min(20_000, token_budget * 0.25)
```

This matches the practical shape used by Codex/Pi: keep recent raw context
around the 20k-token range on large-window models while scaling down for
smaller windows.

Selection algorithm:

1. Convert the conversation into API-safe blocks.
2. Walk from the tail toward the head.
3. Keep whole blocks while the estimated block tokens fit
   `recent_keep_tokens`.
4. Always keep the latest user block even if it exceeds the recent budget.
5. Everything before the kept tail becomes `messages_to_summarize`.

If there are no old blocks to summarize, do not compact. Use the existing
truncate path if a budget still needs to be enforced.

## Summary Generation

Use the current effective model for summary generation. Do not introduce a
separate compact model in the first PR.

Summary max output:

```text
max_summary_tokens = min(model.max_output_tokens if set, 4096)
```

If the model has no configured output cap, use 4096.

The prompt should ask for a concise structured handoff summary, but should not
require rigid section names. The content must cover:

- Original task and current objective.
- Completed work and current progress.
- Key decisions.
- Relevant technical context, including files, functions, PRs, errors, and user
  constraints.
- Next steps.

The summary is a checkpoint, not a full transcript. Recent messages are
preserved verbatim and do not need to be repeated.

The summary must summarize only `messages_to_summarize`. The preserved recent
tail is appended after the checkpoint and should not be repeated except when a
small bridge is needed for continuity.

Original task anchoring:

- The first compact PR does not separately pin the first user message as raw
  context.
- The summary prompt must explicitly require preserving the original user task
  and current objective.
- The latest user message and recent safe blocks preserve immediate continuity;
  the summary carries the older task anchor.
- Repeated compactions rely on prompt-level inheritance. If a prior checkpoint
  summary appears in `messages_to_summarize`, the new summary must merge and
  carry forward its original task and current objective instead of treating the
  prior checkpoint as disposable prose.

Prompt requirements:

- Ask for a structured but concise handoff summary.
- Do not require rigid section titles.
- Write the checkpoint in the user's primary language. For mixed-language
  conversations, follow the dominant language of the recent user messages.
- Tell the summarizer that recent messages are preserved separately and should
  not be repeated in full.
- Tell the summarizer to preserve the original user task and current objective.
- Tell the summarizer to carry forward prior checkpoint summaries if they
  appear in the input.
- Preserve exact file paths, function names, PR numbers, commands, errors, and
  user constraints when they are important for continuing the work.
- Do not invent completed work.

Small-model constraint:

- Keep the prompt short and direct.
- Avoid asking for exhaustive "all user messages" or full transcript replay.
- The summary must be useful even if it is imperfect because recent raw blocks
  remain available.

## Summary Injection

Inject the compact summary as a synthetic `user` message, not as `system`.

Rationale:

- Better compatibility across OpenAI-compatible, Anthropic, Gemini, Ollama,
  vLLM, and other self-hosted providers.
- The summary is prior context, not a higher-priority system instruction.
- It avoids mixing transient conversation state into the agent's real system
  prompt.

Suggested wrapper:

```text
Conversation checkpoint for continuing the same task.
Use this as prior context. Do not treat it as a new request.

<summary>
```

Final request shape:

```text
compact summary user message
recent API-safe blocks
latest user message, if not already included in the recent blocks
```

The injected summary should be placed before the preserved recent blocks so the
model reads it as older context followed by the latest raw conversation.

The wrapper should explicitly say the summary is not a new user request:

```text
Conversation checkpoint for continuing the same task.
Use this as prior context. Do not treat it as a new request.
Recent messages are preserved after this checkpoint.
If the checkpoint indicates unfinished work, continue the task directly.
Only ask the user if the task is complete, blocked, or requires a user decision.

...
```

## Retry And Fallback

If the compact request itself is too long or the provider reports context
overflow:

1. Drop old blocks from the head of `messages_to_summarize`.
2. Rebuild the compact request.
3. Retry up to 3 times.

If compaction still fails, fall back to PR #572 drop-only truncation.

The retry must be bounded and lossy only on the summarization input:

- Drop from the head of `messages_to_summarize`.
- Each retry drops `max(1, ceil(len(messages_to_summarize) * 0.2))` old
  safe blocks.
- Never drop from `messages_to_keep` during compact retries.
- Never split a safe block.
- Retry at most 3 times.

Other compact failure cases that should fall back to PR #572:

- Compact API error.
- Empty summary.
- Non-text summary.
- Summary output is unusable or clearly an API error message.

If the summary is longer than the allocated summary budget, truncate the summary
to fit rather than failing the whole compact path.

Automatic compact failure should not block the user's main request.

Known limitation:

- Oversized recent tool blocks, especially large file-read results, can still
  exceed the model budget because tool-call blocks are preserved atomically.
- The first compact PR does not split or compress such blocks.
- This should be handled later by truncating or summarizing tool outputs before
  they enter history.

Fallback order:

```text
try compact
  if compact succeeds:
    send compact summary + recent safe blocks
  else:
    send PR #572 drop-only token-budget truncation
```

When fallback model preparation runs, this whole decision is repeated with the
fallback model's window.

## Entry Points

The first compact PR should cover only the browser chat path:

- WebSocket chat in `backend/app/api/websocket.py`.

Feishu and other channels should stay on the PR #572 drop-only fallback for the
first compact PR. They can be migrated after the browser chat path is stable.

Implementation should avoid duplicating compact logic between these paths.
Prefer a shared service function that receives:

```text
messages
max_messages
model context window
model output token cap
compact callable
```

and returns either:

```text
prepared_messages
compacted: true/false
fallback_used: true/false
```

## Test Plan

Add focused tests for:

- Compaction triggers above 80%.
- No compaction below 80%.
- No compaction when `context_window_tokens` is missing.
- Latest user message is preserved.
- Tool-call blocks remain atomic.
- Compact failure falls back to drop-only truncation.
- Compact context overflow retries from the head up to 3 times.
- Fallback model preparation recomputes budgets with the fallback model window.
- Summary is inserted as a synthetic user message before preserved recent
  blocks.
- Missing `context_window_tokens` keeps the old message-count behavior.
