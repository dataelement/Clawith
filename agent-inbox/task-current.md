# Current Codex Task

## Task ID
Stage-41A-GITHUB-BRIDGE

## Title
Deploy ChatGPT Web <-> Codex Desktop GitHub handoff workflow

## Objective
Create a lightweight GitHub-based handoff system so ChatGPT Web can generate tasks and review results, while Codex Desktop reads tasks, modifies code, writes reports, and creates PRs.

## Allowed Scope
Codex may create or update only:

- `.github/ISSUE_TEMPLATE/codex_task.yml`
- `.github/pull_request_template.md`
- `.github/workflows/codex-structure-check.yml`
- `agent-inbox/`
- `agent-outbox/`
- `ops/`
- `scripts/`
- `docs/`
- `CODEX_PASTE_PROMPT.md`
- `AGENTS.md`

## Forbidden Scope
Codex must not modify:

- `.env`
- `.env.*`
- secret files
- credential files
- database backups
- production data
- deployment credentials
- existing business source code

## Required Actions
1. Create the GitHub handoff structure.
2. Create inbox, outbox, ops, docs, and script files.
3. Add Codex operating rules to `AGENTS.md`.
4. Verify all required files exist.
5. Commit changes on the current branch.
6. Do not push unless explicitly instructed.

## Acceptance Criteria
- Required directories exist.
- Required files exist.
- No secret or production file is staged.
- Branch is `codex/stage-41a-github-bridge`.
- Commit is created successfully.
- Rollback plan exists.

## Rollback Plan
Revert the generated commit or delete the generated handoff files.
