<!--
Sync Impact Report
- Version change: 1.0.0 -> 1.1.0
- Updated execution ownership to the clean-break Runner/History contract.
- Removed LangGraph and generic exactly-once external execution requirements.
- Added login-scoped human authorization and per-Run execution configuration.
- Templates requiring updates:
  - ✅ .specify/templates/plan-template.md (existing Constitution Check supports these gates)
  - ✅ .specify/templates/spec-template.md (scope and measurable acceptance sections already present)
  - ✅ .specify/templates/tasks-template.md (test-first and path-specific tasks already supported)
- Follow-up TODOs: none
-->
# Clawith Constitution

## Core Principles

### I. Evidence Before Claims
Current behavior MUST be established from source code, migrations, tests, or runtime evidence before
changes are designed. Provider facts, Runtime facts, Model output, and hypotheses MUST remain
separate. Point-in-time facts such as branches, versions, ports, commits, and deployment state MUST
be rechecked before they are reported.

### II. Minimal Scoped Changes
Every implementation MUST stay inside the user-approved scope and use the smallest reversible diff
that fixes the demonstrated behavior. Existing utilities and contracts MUST be reused before new
abstractions are introduced. Adjacent refactors, new dependencies, and speculative hardening are
forbidden unless explicitly approved.

### III. Contract and State Ownership
Each fact MUST have one authoritative owner. Provider-specific adapters own mapping external business
states into typed outcomes; Agent Runner owns Run status, append-only History, scheduling, waiting, settlement, and resume;
the Model owns intent and user-facing content. Consumers MUST use the structured contract rather than
re-deriving state from summaries or prose.

### IV. Tests Prove Behavior
Bug fixes MUST include regression coverage for the failing path and its terminal outcomes. Tests MUST
prove both the desired result and prohibited side effects, such as duplicate external writes. Scoped
tests and relevant static checks MUST pass before completion is claimed; live verification MUST be
reported separately from local automated evidence.

### V. Preserve Existing Work
Unrelated dirty-worktree changes belong to the user and MUST NOT be reverted, overwritten, or folded
into the feature. Files ignored by Git MUST be verified through direct filesystem inspection. Agents
MUST avoid destructive commands and MUST report unavoidable ownership conflicts before proceeding.

## Project Constraints

- Backend work uses Python, FastAPI, SQLAlchemy, PostgreSQL and pytest in the clean-break modular monolith. LangGraph, checkpoint, Command and generic Tool Ledger authorities are excluded.
- Human permissions are resolved at login; each new Run resolves Agent-owned execution configuration within that scope. Runner does not implement live reauthorization or revocation sweeps.
- No dependency may be added without explicit user approval.
- Documentation may describe historical intent, but implementation claims MUST be checked against
  current source.
- Public Tool behavior and internal Runtime behavior MUST not be broadened merely to simplify one fix.
- External writes use provider-supported idempotency where available. An uncertain outcome remains explicit and never authorizes blind replay; the platform does not promise generic exactly-once external execution.

## Development Workflow

1. Define the observed failure, authoritative fact, consumer, and approved boundary.
2. Write a testable specification and identify prohibited changes.
3. Add or update scoped regression tests before the implementation when practical.
4. Implement the smallest contract-preserving change.
5. Run scoped pytest and Ruff checks, then inspect the final diff for unrelated changes.
6. Report changed files, verification evidence, and remaining risks without overstating live status.

## Governance

This constitution governs Spec Kit artifacts for Clawith and is subordinate only to explicit user
instructions and the repository `AGENTS.md`. Amendments require a documented rationale, semantic
version update, date update, and consistency review of dependent Spec Kit templates. Every feature
plan MUST evaluate these principles before design and again before implementation. Any exception MUST
be explicit in the plan's Complexity Tracking section and approved before code changes begin.

**Version**: 1.1.0 | **Ratified**: 2026-08-05 | **Last Amended**: 2026-09-06
