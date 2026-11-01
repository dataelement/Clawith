# AGENTS.md — Clawith Agent Governance & Architecture Guidelines

## 1. Project Identity

Clawith is an enterprise agent harness for durable single-agent and multi-agent execution. It enables agents to use tools, maintain execution state, and operate across long-running workflows. This repository contains the agent runtime, execution contracts, product APIs, integrations, and web interface used to build and operate those agents.

### Repository layout

```text
backend/    Backend application and agent runtime.
            Internal structure and rules: backend/AGENTS.md
frontend/   Web application for configuring, operating, and observing agents.
            Internal structure and rules: frontend/AGENTS.md
docs/       Durable project documentation and current sources of truth.
specs/      Feature specifications and implementation artifacts.
scripts/    Repository-wide development, validation, and maintenance tooling.
deploy/     Production deployment documentation and deployment-specific assets.
helm/       Kubernetes deployment charts.
.github/    GitHub workflows and CI support scripts.
.specify/   Specification workflow templates and generators.
docker-compose*.yml  Local, CI, and production-oriented container topology.
```

Internal refactors update the nearest path-specific `AGENTS.md`. The root `AGENTS.md` changes only when a top-level repository boundary changes.

## 2. Conventions

Each behavior-driving fact has one authoritative owner. Other layers may submit commands, record outcomes, cache data, or build projections, but they must not independently redefine that fact or become a second authority for it.

- **Lifecycle ownership is explicit.** Every registration, task, subscription, connection, or resource that outlives the current operation has one owner, defined termination conditions, and cleanup paths for success, failure, and cancellation.
- **Runtime responsibilities are documented.** Every capability or subsystem with an independent runtime responsibility must document the authoritative facts and relationships it owns, how those facts change, and how their correctness is verified. Do not infer runtime health from the presence of code, configuration, services, or UI state.
- **State and protocol variants are explicit.** Treat internal lifecycle states and shared contracts as closed unless they are deliberately designed for extension. Update every producer and consumer when a closed set changes, and define explicit unknown-value behavior for extensible inputs.
- **Model-visible inputs are traceable.** Every input that can affect a model decision must have an identifiable source and be attributable to the corresponding Run. Do not inject transient context that cannot later be inspected or reconstructed. See [`docs/model-visible-inputs.md`](docs/model-visible-inputs.md).
- **Keep the Runtime core generic.** The Agent Runtime core may change while its execution model is being completed, but core changes must define general execution semantics rather than product-, integration-, UI-, or capability-specific behavior. Add specialized behavior through its owning Tool, Skill, Provider, Channel, Hook, or service boundary. Document and test every change to the execution model.
- **New state machines require an independent owner and need.** Do not introduce a state machine merely to represent workflow steps, UI progress, or a lifecycle already owned elsewhere. A new state machine must correspond to an independently identified object with authoritative transitions and a current behavioral consumer.
- **Capability boundaries require real participants.** Introduce a shared capability contract only when it has a current provider and consumer. Keep roles together when they change for the same reason; separate them only when their responsibilities and evolution are genuinely independent.
- **Resolve policy before execution.** Defaults, configuration precedence, and policy choices must be resolved explicitly by their owning layer before an operation executes. Execution code consumes resolved inputs and must not hide additional policy decisions in fallbacks.
- **Misconfiguration fails at the earliest authoritative point.** Reject an invalid or missing configuration as soon as its owning layer has enough information to determine the error. Do not silently skip the configured behavior, invent a fallback, or defer a known failure into execution.
- **Validate at trust boundaries.** Use static types for same-process internal contracts and avoid duplicating runtime validation between already typed layers. Validate data when it enters from configuration, HTTP or WebSocket requests, model or Tool JSON, persistence, files, workers, processes, and external integrations.
- **Data access is bounded and evidence-driven.** Query and loading paths must
  define their expected cardinality and enforce filtering, pagination, batching,
  and result limits at the layer that owns the complete data operation. Avoid
  per-item queries, repeated full materialization, and loading unbounded data
  for downstream filtering.
- **Caches require ownership and measured need.** Introduce caching only after
  identifying repeated expensive work on a real access path. Every cache must
  define its authoritative source, owner, key scope, invalidation rule, capacity
  bound, and freshness behavior.
- **Ignored failures are narrow and explained.** Catch only the single operation whose specific failure may be ignored, and state what is being ignored and why the primary outcome remains safe. Never use an empty or broad catch to hide unrelated failures.
- **Tests enforce behavior, not product truth.** A passing test proves that the implementation matches its asserted behavior; it does not prove that the asserted behavior matches the current product or architecture contract. Update obsolete tests together with an explicitly approved contract change, and never change an expectation merely to make a failure disappear.
- **Non-trivial changes keep code, Agent Notes, and commit history aligned.** Any change to behavior, architecture, a shared contract, Runtime semantics, persistence, security, permissions, compatibility, or engineering process must add or update its owning Agent Note in the same change. The code implements the decision, the Agent Note owns its durable rationale and current contract, and the commit message records the intent, scope, and verification of this change. These three records must not contradict one another. Update an existing owning note instead of creating a duplicate; only mechanical or strictly local changes are exempt.

## 3. Change Discipline

- Keep each change scoped to one intent. Do not mix structural refactoring,
  behavior changes, compatibility work, and unrelated cleanup.
- Preserve verified behavior unless the task explicitly changes the owning
  product or architecture contract.
- Before introducing an abstraction, identify the current owner and consumer.
  Delete obsolete code, reuse the existing owner when it already fits, and move
  misplaced behavior back to that owner while removing bypass paths. Add a new
  layer only when it has an independently changing responsibility and a current
  consumer.
- **Delete verified dead code.** Once code, configuration, tests, compatibility
  paths, or documentation are confirmed to have no current contract or
  production consumer, remove them in the same change. Do not keep
  commented-out implementations, speculative fallbacks, or tests that only
  preserve deleted behavior.
- Preserve unrelated working-tree changes and user-owned files.
- Use repository-relative paths in code, documentation, and instructions.
- When ownership or a boundary changes, update the nearest path-specific
  `AGENTS.md` and the corresponding durable documentation.
- Do not add fallback or compatibility paths without a documented reason,
  regression coverage, and a removal condition.
- Keep source facts, test evidence, CI evidence, deployment evidence, and
  live-system evidence clearly separated.

## 4. Type Checking

Everything compiles under `strict: true` with `noImplicitAny`; every remaining `any` explains why narrowing is infeasible.

Public interfaces must be usable without reading their implementation. Types
define structure; owning documentation defines non-obvious behavior, failure,
side effects, ownership, timing, cancellation, and durability.

Every new or changed automated rule must include positive and negative coverage:
valid cases pass, and representative invalid cases fail for the intended
reason.

## 5. Quick Command Reference

Dev and test commands live in sub-project instruction files:

- Backend: `backend/AGENTS.md` (Server start, Alembic migrations, Pytest, Ruff)
- Frontend: `frontend/AGENTS.md` (Vite dev server, type-check, lint, build)

## 6. Failure Diagnosis and Handling

When a command fails:

```text
Command fails
    ↓
Identify the failing layer
    ↓
Collect evidence from that layer
    ↓
Fix the layer that owns the failure
    ↓
Run the original command again
```

Do not:

- Modify product code to accommodate the current machine before evidence shows that the environment is the failing layer and that a product-level portability change is required.
- Dismiss a test failure as an environment problem before collecting environment evidence and ruling out a product-code regression.

## 7. Verification

After code changes, verification scope is determined by the affected contracts and consumers, not by the number of modified files. Cross-layer changes must follow the real execution path and update and verify every affected layer; local changes require only local evidence.

Match evidence to the surface.

Use [`docs/testing.md`](docs/testing.md) to select verification by changed contract. Start with focused checks and expand only when the change crosses a documented boundary.

Run checks before pushes via [`clawith-pre-push-checks`](.agents/skills/clawith-pre-push-checks/SKILL.md) and report the exact commands and results. After rebasing, merging, resolving conflicts, or otherwise synchronizing a branch, immediately rerun the checks affected by the resulting diff. Do not merge while required checks are failing.

## Communication

- Lead with the conclusion, result, or blocker.
- Use direct, concrete language and name the actual actor, fact, file, command,
  API, state, or behavior.
- Separate verified repository facts, inference, and unverified live behavior.
- Do not narrate internal reasoning, tool choreography, or review history.
- Report only commands and checks actually run, together with relevant
  verification gaps.
- Keep responses concise unless risk, ambiguity, or the user requests more
  detail.

## Editing these instructions

Keep repository-wide instructions concise, self-contained, and linked to their
owning documentation. Put path-specific rules in the nearest nested
`AGENTS.md`, and do not duplicate rules across instruction files. Add or expand
a root rule only when it must remain available across the repository.
