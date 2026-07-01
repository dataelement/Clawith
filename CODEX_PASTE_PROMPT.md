# Codex Paste Prompt

Use this prompt when handing a GitHub task from ChatGPT Web to Codex Desktop.

```text
You are working in the Clawith repository.

Read AGENTS.md first, then read agent-inbox/task-current.md.

Implement only the allowed scope listed in the current task. Do not modify forbidden files, secrets, production data, deployment credentials, database backups, or unrelated business source code.

After implementation:
1. Update agent-outbox/codex-report.md with the summary and verification.
2. Update agent-outbox/execution-log.md with important actions.
3. Update agent-outbox/error-report.md if anything failed.
4. Update agent-outbox/next-actions.md with remaining work.
5. Run the requested verification commands.
6. Show git status.
7. Commit only the task-related changes if the task requires a commit.
8. Do not push unless explicitly instructed.
```
