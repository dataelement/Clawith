# Rollback Plan

## Preferred Rollback
Revert the setup commit:

```bash
git revert <commit_sha>
```

## Manual Rollback
Delete the generated handoff files and restore the two updated files:

```bash
rm -rf agent-inbox agent-outbox ops
rm -f scripts/verify-codex-bridge.sh
rm -f docs/chatgpt-codex-workflow.md docs/codex-desktop-operating-rules.md
rm -f CODEX_PASTE_PROMPT.md
rm -f .github/ISSUE_TEMPLATE/codex_task.yml
rm -f .github/workflows/codex-structure-check.yml
git checkout -- AGENTS.md .github/pull_request_template.md
```

## Safety Notes
- Do not delete existing project source code.
- Do not delete existing issue templates unrelated to Codex.
- Do not modify `.env` or credential files during rollback.
