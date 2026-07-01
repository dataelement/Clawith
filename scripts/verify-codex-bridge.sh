#!/usr/bin/env bash
set -euo pipefail

required_paths=(
  ".github/ISSUE_TEMPLATE/codex_task.yml"
  ".github/pull_request_template.md"
  ".github/workflows/codex-structure-check.yml"
  "agent-inbox/task-current.md"
  "agent-inbox/task-queue.md"
  "agent-outbox/codex-report.md"
  "agent-outbox/execution-log.md"
  "agent-outbox/error-report.md"
  "agent-outbox/next-actions.md"
  "ops/acceptance-checklist.md"
  "ops/rollback-plan.md"
  "scripts/verify-codex-bridge.sh"
  "docs/chatgpt-codex-workflow.md"
  "docs/codex-desktop-operating-rules.md"
  "CODEX_PASTE_PROMPT.md"
  "AGENTS.md"
)

missing=0
for path in "${required_paths[@]}"; do
  if [[ ! -e "$path" ]]; then
    echo "missing: $path"
    missing=1
  fi
done

if git diff --cached --name-only | grep -E '(^|/)\.env($|\.)|secret|credential|backup|production-data' >/dev/null; then
  echo "forbidden staged file detected"
  exit 1
fi

current_branch="$(git branch --show-current)"
if [[ "$current_branch" != "codex/stage-41a-github-bridge" ]]; then
  echo "unexpected branch: $current_branch"
  missing=1
fi

if [[ "$missing" -ne 0 ]]; then
  exit 1
fi

echo "codex bridge structure ok"
