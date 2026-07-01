# Codex Desktop Operating Rules

## Before Work
1. Read `AGENTS.md`.
2. Read `agent-inbox/task-current.md`.
3. Confirm the current branch and repository status.
4. Confirm the task's allowed and forbidden scope.

## During Work
- Stay inside the allowed scope.
- Do not modify `.env`, credentials, backups, production data, or deployment secrets.
- Keep changes small and easy to review.
- Record important actions in `agent-outbox/execution-log.md`.
- Record blockers or failures in `agent-outbox/error-report.md`.

## After Work
1. Run task-specific verification.
2. Run `scripts/verify-codex-bridge.sh` when the handoff structure is relevant.
3. Update `agent-outbox/codex-report.md`.
4. Check `git status --short` for unexpected files.
5. Commit changes when requested by the task.
6. Do not push unless explicitly instructed.
