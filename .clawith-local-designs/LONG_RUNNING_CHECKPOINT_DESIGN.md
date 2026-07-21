# Long-Running Checkpoint Design

This document captures a later Clawith-specific optimization that is not part
of the first compact PR.

## Idea

Base compact should keep short summaries in the model context. Long-running
tasks may also need a detailed external checkpoint that can preserve exact task
details without bloating every prompt.

The proposed second layer is:

```text
compact summary -> short, included in context
workspace checkpoint -> detailed, written to a workspace file, read on demand
```

## Motivation

Clawith agents can run long tasks where the original user task, detailed
progress, decisions, files, tests, and errors should not disappear after several
compactions.

A short compact summary should not carry every detail. A workspace checkpoint
can preserve richer information while keeping normal prompts small.

## Candidate File Layout

```text
.clawith/checkpoints/<agent_id>/<conversation_id>/latest.md
```

The path must isolate agents and conversations to avoid concurrent overwrite.

## Candidate Checkpoint Content

```markdown
# Clawith Task Checkpoint

## Original Task
## Current Objective
## Detailed Progress
## Decisions
## Files Read
## Files Modified
## Commands And Tests
## Errors And Fixes
## Open Questions
## Next Steps
```

## Runtime Behavior

When compact runs:

1. Generate the short compact summary for context.
2. Generate or update a detailed checkpoint file if a writable workspace exists.
3. Add only a short path hint to the model context.
4. Do not read the checkpoint by default.
5. The agent reads the checkpoint only when exact older details are needed.

Checkpoint write failure should not block compact.

## Risks To Resolve Before Implementation

- Workspace write policy and user visibility.
- `.gitignore` and repository pollution.
- Sensitive value filtering for API keys, tokens, and credentials.
- Multi-agent and multi-session concurrency.
- Cleanup/retention strategy.
- Behavior for Feishu/Slack/Teams channels without a clear workspace.
- Consistency between DB compact summary and workspace checkpoint.

## Relationship To The First Compact PR

The first compact PR should not implement this layer. It should only provide:

- 80% trigger.
- model-metadata-based budget.
- short summary compaction.
- recent safe-block preservation.
- drop-only fallback.

This checkpoint layer can be added after the base compact path is stable.
