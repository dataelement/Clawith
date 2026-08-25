# Implementation Plan: Vercel Async Deployment Wait Recovery

**Branch**: `001-fix-vercel-async-wait` | **Date**: 2026-08-05 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-fix-vercel-async-wait/spec.md`

## Summary

Change the existing Vercel Tool Adapter so accepted deployments in INITIALIZING, QUEUED, or BUILDING
return the Runtime's existing declared asynchronous-operation contract instead of a successful Tool
receipt. Add an internal poll mode that performs one exact deployment status read and reuses the same
operation identity until READY, ERROR, or CANCELED. Reuse all current Runtime scheduling, resume,
waiting, and terminal-settlement code without modification.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: FastAPI service stack, httpx, SQLAlchemy async ORM, existing Agent Runtime
**Storage**: Existing PostgreSQL-backed `AgentToolExecution.result_metadata`; no migration
**Testing**: pytest, pytest-asyncio, existing scripted Vercel provider fixtures
**Target Platform**: Clawith backend service and Runtime worker
**Project Type**: Existing web-service backend
**Performance Goals**: One status GET per scheduled poll; no blocking sleep inside Tool execution
**Constraints**: Exactly one deployment POST; fixed 2-second interval; no new dependency; no generic
Runtime changes; no public Tool behavior expansion
**Scale/Scope**: One Vercel Tool Adapter, its typed-outcome tests, and one existing Runtime-contract
integration path

## Constitution Check

*GATE: Passed before research and re-checked after design.*

- **Evidence Before Claims**: Current Vercel and Runtime code paths were inspected; the defect is the
  Vercel outcome mapping and missing internal poll mode.
- **Minimal Scoped Changes**: Source changes are limited to `backend/app/services/agent_tools.py` and
  scoped tests. Generic Runtime files are prohibited unless a failing contract test proves otherwise.
- **Contract and State Ownership**: Vercel maps `readyState`; Runtime consumes typed pending and
  terminal outcomes. Model prose is not used for settlement.
- **Tests Prove Behavior**: Tests cover non-terminal mapping, repeated exact polling, terminal mapping,
  original receipt settlement, and absence of duplicate deployment POSTs.
- **Preserve Existing Work**: Existing dirty files and ignored documentation remain untouched outside
  the approved Spec Kit artifacts and Vercel bug-fix scope.

Post-design re-check: PASS. The design adds no database entity, dependency, generic state machine, or
second scheduler.

## Project Structure

### Documentation (this feature)

```text
specs/001-fix-vercel-async-wait/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── vercel-async-operation.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code

```text
backend/
├── app/services/agent_tools.py
└── tests/
    ├── test_agent_tools_typed_vercel_deploy.py
    ├── test_agent_runtime_tool_step_service.py
    └── test_agent_runtime_async_tool_poll.py
```

**Structure Decision**: Keep implementation inside the existing monolithic built-in Tool Adapter.
Reuse existing Runtime tests where possible; add only the smallest Vercel-specific integration
coverage needed to prove the generic contract consumes the new outcome.

## Complexity Tracking

No constitution violations or added architectural complexity.
