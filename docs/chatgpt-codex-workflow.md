# ChatGPT Web <-> Codex Desktop Workflow

## Purpose
This repository uses GitHub as a lightweight handoff layer between ChatGPT Web and Codex Desktop.

## Flow
1. ChatGPT Web drafts a scoped task using `.github/ISSUE_TEMPLATE/codex_task.yml`.
2. The task is copied into `agent-inbox/task-current.md` or tracked in `agent-inbox/task-queue.md`.
3. Codex Desktop reads the inbox, implements only the allowed scope, and avoids forbidden files.
4. Codex Desktop writes results to `agent-outbox/`.
5. Codex Desktop commits on a task branch and opens a pull request when explicitly instructed.
6. ChatGPT Web reviews the PR, the outbox report, and verification evidence.

## Required Outbox Files
- `agent-outbox/codex-report.md`
- `agent-outbox/execution-log.md`
- `agent-outbox/error-report.md`
- `agent-outbox/next-actions.md`

## Guardrails
- Keep each handoff small and reviewable.
- Include acceptance criteria before implementation starts.
- Never include secrets in inbox, outbox, issues, commits, or PRs.
- Do not push branches unless explicitly instructed.
