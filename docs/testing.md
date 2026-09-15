# Testing Policy

This document defines what each Clawith verification surface proves and how to select evidence for a change. The [pre-push Skill](../.agents/skills/clawith-pre-push-checks/SKILL.md) applies this policy to the complete outgoing diff.

## Evidence principles

Match evidence to the changed contract and the claim being made. Start with the narrowest check that would fail for the intended regression, then expand only across boundaries the change actually reaches.

Unit tests, static checks, builds, browser validation, CI, deployment, and live acceptance are different facts. None substitutes for another.

Tests enforce the behavior they assert; they do not decide whether that behavior matches current product or architecture intent. An approved contract change updates code, owning documentation, Agent Note, and tests together. Never change an expectation merely to make a failure disappear.

## Backend evidence

- **Focused Pytest:** proves behavior owned by the selected test target and its real collaborators.
- **Ruff:** proves the checked Python scope satisfies configured lint rules; it is not a type or behavior test.
- **Pyright:** proves the checked Python scope satisfies static type contracts; it does not validate external payloads at runtime.
- **Architecture Guard:** proves only the repository rules implemented by `scripts/arch-guard.sh`; a new or changed rule requires positive and negative coverage.
- **Full Backend Pytest:** is appropriate for repository-wide Backend changes, CI diagnosis, or an explicit request; it is not the default response to a local change.

Prefer real implementations below the expensive or nondeterministic boundary. Mock external providers, network, clocks, or nondeterministic inputs when necessary; keep the owning Service, Runtime, Tool, persistence, and executor path real when those behaviors are the subject.

## Frontend evidence

- **Focused Node test:** proves the imported service, reducer, state transition, utility, or narrow source contract named by the test.
- **TypeScript check:** proves static type compatibility across the Frontend project.
- **ESLint and Prettier:** prove lint and formatting compliance for the checked scope; they do not prove user-visible behavior.
- **Production build:** proves TypeScript compilation and Vite production bundling; it does not prove rendering or interaction.
- **Browser validation:** proves rendered content, interaction, focus, scrolling, responsive layout, and navigation in the exercised browser path.

Prefer behavior tests that execute an owning function or state transition. Source-text regex tests are narrow static guards and must not be reported as component rendering or user-flow evidence.

## Shared contracts and assembled paths

A Backend/Frontend API, event, Runtime state, Tool result, or error-contract change requires evidence from every affected owner and consumer. Update and verify both sides rather than treating one side's passing tests as compatibility proof.

Runtime, Tool, Worker, and lifecycle changes require the focused owning tests plus the real executor or consumer-facing path. Verify durable or external state instead of trusting a model response, callback invocation, or local projection.

Model-visible prompts, Tool schemas, Tool results, and stable diagnostics are behavior. Verify them at the assembled model-request or Tool-execution path when the change can alter what the model sees.

## Test the real entry path

A product-visible behavior requires evidence through the entry path that users, workers, agents, or deployed services actually execute. A directly imported helper, manually constructed service, or mocked transport does not prove API routing, dependency composition, worker startup, Runtime wiring, container startup, or browser integration.

Use the narrowest real assembled path that crosses the boundary changed by the contract. Keep lower-level tests as supporting evidence.

## Test resource ownership and cleanup

Tests that create tasks, workers, subscriptions, connections, temporary files, sandboxes, or external resources own and clean them on success, failure, cancellation, retry, and timeout. Assert that cleanup reaches the terminal or removed state; calling a cleanup method is not sufficient evidence.

## Database migrations

Read `backend/alembic/AGENTS.md` before changing a migration. Migration evidence may include single-head validation, migration-specific tests, downgrade/upgrade, fresh-database migration, previous-release upgrade, and deployment-shaped checks; select the surfaces required by the migration contract.

A source migration file, hash, or successful local import does not prove that a real database can migrate or roll back safely.

## External and live evidence

Provider, Channel, OAuth, Tool, browser, and deployment claims that depend on a real external system require an authorized real-system check. Local mocks and CI remain supporting evidence.

Keep local tests, remote CI, deployed-version proof, service health, and real business acceptance separate. A health response does not prove a workflow, and a successful external request does not prove product reconciliation or delivery.

## Full-suite policy and historical baselines

Run complete local suites only when explicitly requested, while diagnosing CI, when the change is irreducibly repository-wide, or when this policy names the full suite as the owning gate. Run the complete Backend suite when an affected contract crosses multiple Backend areas. Run the complete Frontend suite and production build when an affected contract crosses multiple Frontend areas or changes assembled user-visible behavior.

A known repository-wide baseline failure does not excuse a new violation. Check the affected scope, preserve unrelated work, and report the baseline separately. After a gate reaches a green baseline, later failures are blocking until evidence proves they are environmental or unrelated to the outgoing commits.

## Reporting

Report exact commands, results, affected scope, and relevant verification not performed. Do not claim a broader success than the evidence supports.
