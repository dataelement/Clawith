# AGENTS.md — Clawith Backend

These backend-specific rules apply to `backend/**` and supplement the repository-wide [conventions](../AGENTS.md#2-conventions).

The target Backend is a Python 3.11+ FastAPI application built on SQLAlchemy's asynchronous APIs, PostgreSQL, and Redis. It contains the Agent Runtime, product APIs, persistence, background execution, and external integrations.

Project metadata and dependency declarations are defined in `pyproject.toml`; `uv.lock` records the resolved dependency graph.

## Commands

Run Backend commands from `backend/`:

| Action | Command |
| --- | --- |
| Install project and development dependencies | `uv sync --extra dev` |
| Run the development server | `uv run uvicorn app.main:app --reload --port 8000` |
| Run a focused test file | `uv run --extra dev pytest tests/<test_file>.py` |
| Run the complete Backend test suite | `uv run --extra dev pytest` |
| Run lint checks | `uv run --extra dev ruff check .` |
| Run static type checks | `uv run --extra dev pyright app` |
| Inspect frozen migration topology | `uv run alembic heads` |

Use focused Pytest targets during development. Use the repository testing policy as the authority for when the complete Backend suite is required.

Application startup requires explicit `EXECUTION` deployment configuration for Credential/continuation keyrings and storage. Importing the ASGI application does not create keys or resources. Missing execution configuration fails before database resources are created; there is no health-only fallback startup mode.

Read [`alembic/AGENTS.md`](alembic/AGENTS.md) before creating or editing a database migration.

G002 has no target schema baseline. Alembic execution commands, including current, upgrade, downgrade, stamp, and offline SQL generation, are unavailable until G008. Only structural `heads` and `history` inspection is supported; startup and CI never apply revisions.

## Application layout

```text
pyproject.toml  Project metadata, dependencies, and tool configuration.
uv.lock         Locked Python dependency graph.
alembic/        Database schema migrations.
scripts/        Repository-operated Backend maintenance and data-migration scripts.
tests/          Backend unit, contract, integration, and regression tests.
app/main.py    ASGI export of the application produced by `app.application`.
app/application.py
               The single FastAPI factory and application resource lifespan.
app/infrastructure/config.py
               Target application configuration and environment validation.
app/infrastructure/database.py
               The single SQLAlchemy registry and application-owned control and
               execution database resources.
app/infrastructure/object_storage/
               Low-level object-storage contract plus local and S3 mechanics.
app/modules/   Target modular-monolith owners.
app/execution_dependencies/
               Application-side Tool adapters between typed owner services.
               No business fact ownership, ORM access, or application factory.
app/runtime/   Run-owned Runner and Loop execution mechanics only.
app/api/       HTTP and WebSocket transport adapters.
app/schemas/   Request, response, and transport validation models.
app/models/    SQLAlchemy persistence models.
app/dao/       Static zero-byte namespace; no modules, repositories, or exports.
app/services/  Product services, Runtime capabilities, background execution,
               and external integrations.
app/core/      Cross-cutting security, permissions, errors, events, logging, and
               middleware.
app/scripts/   Application maintenance, bootstrap, backfill, and migration tools.
```

Read the nearest nested `AGENTS.md` before modifying a specialized subtree. Detailed module structure belongs to that subtree's instruction or owning architecture document, not this file.

## Clean-break rewrite governance

The clean-break rewrite is implemented directly on `develop`. The target tree has one final-form application factory and one SQLAlchemy `Base`/metadata registry throughout the rewrite. The immutable `8ed4ae2f` legacy checkout is black-box evidence only: it uses separate disposable persistence and must not share imports, traffic, state, or authority with the target tree.

The target is one modular monolith under `app/modules/<owner>/`, with narrow execution mechanics under `app/runtime/` and shared database, transaction, and configuration infrastructure under `app/infrastructure/`. Every owner keeps its ORM models and repositories private. Another owner may use only its typed public service contract; it must not import the private model or repository, issue writes to the owner's tables, or recreate the owner's policy.

Cross-owner atomic operations use the infrastructure `TransactionContext` and typed application orchestration ports. The orchestrator selects one transaction and invokes owner services; it never writes owner tables directly. Define consumer-facing ports such as `OutcomeConsumer` before their callers depend on them. Login-scoped authorization supersedes the former authorization-dependency writer; no live generation projection or cancellation sweep is part of the target.

Audit is an independent non-blocking public interface. Emit committed observations without passing the business TransactionContext; its application-owned asynchronous consumer uses independent transactions. Audit loss or failure must not alter business outcomes or supply their authoritative state. Composition closes Audit before database resources; the [Audit Note](../.agents/notes/implemented/architecture/2026-09-06-asynchronous-audit-observation.md) defines its bounded best-effort behavior.

Object storage is infrastructure mechanics, not an alternate Workspace owner. Infrastructure and application composition may construct concrete local or S3 backends. The Workspace owner may depend only on `app.infrastructure.object_storage.base`; every other product owner and `app.runtime` must use the approved Workspace public service rather than importing object-storage contracts or implementations directly. The empty `object_storage` package initializer does not re-export implementations.

The public service/import DAG is:

```text
Infrastructure database/transactions/config
              |
      Identity/Tenant + Audit
          /             \
   Credential       Agent + Permission
       |              /       |       \
       +------ Model      Workspace   Capability/Tool
                    \        |        /
                     Run Snapshot inputs       Model public values
                              |                        |
                              |                   Context view
                              |                        |
                     Run + Runner + Loop <-------------+
                              |
                Session / Task / A2A / Goal
                              |
              Group / Trigger / Heartbeat / Channel
                              |
                    Remaining product modules
```

`identity_tenant` is one owner. `run` owns Run persistence and Runner/Loop mechanics; `app/runtime/` is only its implementation package. `session` owns Goal configuration and continuation; neither `runtime` nor `goal` is an owner. `tool` and `capability_market` are separate owners.

Context accepts explicit sourced messages and Model limits, not Run services or live authorization. Run translates its own History into Context inputs and consumes Context's public view/projection port. This import direction is distinct from the source-data flow and Context's schema foreign key to Run.

The owner roster and schema-registration waves are exact:

| Wave | Owners |
| --- | --- |
| S0 | `identity_tenant` |
| S1 | `agent`, `credential`, `model`, `audit`, `run`, `permission`, `context`, `auth` |
| S2 | `workspace`, `tool`, `capability_market`, `session`, `a2a`, `group`, `trigger`, `heartbeat`, `channel` |
| S3 | `sso`, `organization`, `invitation`, `onboarding`, `okr`, `focus`, `notification`, `published_page`, `plaza`, `enterprise_settings`, `platform_administration`, `agentbay`, `directory`, `agent_template`, `observability`, `tenant_knowledge` |

The acyclic public DAG controls service implementation order. S0-S3 control schema integration and may register strongly connected foreign keys together; they do not authorize a service to bypass the public DAG. One serialized schema-integration owner registers each wave into the complete shared metadata registry. Each wave gate uses real PostgreSQL to create and drop every registered table, constraint, and index, reject unresolved foreign keys and duplicate table ownership, and exercise positive and negative constraints.

Database registry, application composition, dependency lock, initial baseline, capability coverage, and final source disposition each have one serialized owner. Do not add another declarative base, metadata registry, application factory, or independently changing copy of these shared files.

### Phase 0 ledgers and gates

The three rewrite ledgers have separate authority:

- `rewrite/coverage.json` records old endpoint and lifecycle disposition, replacement, deletion, consumer, test, and removal evidence.
- `rewrite/owner-contracts.json` is the sole readiness authority for every target owner, including owners without a legacy endpoint.
- `rewrite/product-contracts.json` records S3 product decisions and supplies evidence for approving the corresponding owner-contract row; it does not replace that row.

A rewrite coverage row reaches `contract_approved` only when it references exactly one approved owner-contract row and the contract hashes match. Target-tree replacement and G002 legacy-authority deletion must not begin until every coverage row is `disposition_approved`. The current 401/401 `disposition_approved` rows collectively authorize G002 to delete the target-tree legacy authorities classified by those rows. Per-category deletion commits are reviewable execution slices of that collective approval; they do not introduce another approval state, boundary, or ledger. Schema or service work for an owner must not begin until that owner is `contract_approved`; S3 work additionally requires its complete approved product contract. Minimal Auth is S1/G003; its later registration/recovery/product workflow expansion still requires the separate Auth product-contract gate, without creating or approving another Auth owner. The initial baseline and legacy-reference removal require every coverage row to be terminal.

Phase 0 passes only when `unreviewed=0`, `disposition_missing=0`, the exact owner roster is complete and unique, ledger transitions and references validate, the governance and DAG/wave checks pass, the benchmark/pool/queue/fairness configuration validates, and the legacy reference remains clean, fixed at `8ed4ae2f`, boot-isolated, and black-box verified. Stop on any missing, extra, duplicate, unapproved, unhashed, mismatched, or invalid row. No target schema or source replacement begins before all Phase 0 gates pass.

Startup must never call `create_all`, mutate the schema, repair data, translate old state, or activate compatibility paths. Do not add legacy imports, dual reads or writes, old-schema adapters, startup repair, or fallbacks for old APIs, Redis keys, Workspace layouts, Runtime protocols, or storage paths. The target uses explicitly separate persistence namespaces and fails at the owning boundary when target configuration or schema is invalid.

## Async lifecycle

Represent one asynchronous operation with one lifecycle controller or transaction. Readiness, cancellation, disposal, reservation, and sentinel state remain in that owner unless they describe an independently owned object or settlement point. Do not split one operation into parallel lifecycle state machines.

## Lifecycle verification

Tests for registration, cancellation, shutdown, and cleanup must observe the owned resource reaching its terminal or removed state. Asserting only that `cancel()`, `close()`, `dispose()`, or a cleanup callback was invoked is not sufficient evidence that work stopped or resources were released.

## API and service boundaries

API handlers are transport adapters. They parse and validate request data, establish the authenticated and authorized caller, pass explicit inputs to the owning service or command-intake boundary, and map the result to the transport response. Do not put business orchestration, ORM queries, Runtime node calls, checkpoint mutation, or private lifecycle control into an API handler.

[`app.dao`](app/dao/AGENTS.md) is an empty static namespace. `app/dao/__init__.py` remains zero-byte, and `app/dao/` contains no Python modules, repositories, exports, or dynamic package hooks. Each owner keeps persistence inside its private `app/modules/<owner>/` boundary and exposes typed public services to other owners.

Design shared service contracts for all current consumers. Keep transport-, UI-, channel-, and provider-specific behavior in the owning adapter or consumer. Do not widen a public service for one internal caller; keep single-consumer capabilities private until a real shared contract exists.

## Public choices

Do not invent public defaults, modes, operation sets, API fields, event fields, or persisted formats merely to make an interface appear flexible. Every public choice must be supported by a current consumer, an owning product or architecture contract, or established behavior already used by the system.

When that evidence does not exist, require the caller to provide an explicit value or defer the choice instead of introducing a speculative default or extension point.

## Model-facing contracts

Write prompts, Tool schemas, Tool results, and model-visible diagnostics from the model's task perspective. Include the information needed to choose and complete the next action; do not expose UI state, transport details, database structure, internal service names, or implementation vocabulary unless the model must act on that concept.

A failure on a model-visible path must return a bounded, actionable result that identifies the failed subject, the relevant condition, and any safe next action. Do not silently drop the failure or dump stack traces, raw provider responses, internal records, or unbounded diagnostic output into model context.

Treat stable model-visible wording and schemas as behavior. Changes require an update to the owning contract and verification through the assembled model request or Tool execution path.

## Enforcement

The operation that reads protected data, mutates authoritative state, or causes an external side effect must stay within the authenticated, pre-resolved Tenant and capability scope. Human permissions are fixed for a valid login session; Agent-owned execution configuration is resolved for each new Run and fixed in Run Snapshot. Runner and Agent Loop do not reauthenticate users or poll live role/grant changes. Login validity and expiry remain Backend entry concerns. The owning boundary still enforces input limits and cannot trust caller-supplied scope. Upstream layers may perform an equivalent preflight for faster feedback, but Frontend visibility, prompt instructions, Tool-schema omission, API wrappers, and ordinary call ordering are user-experience guidance, not security enforcement.

Tests for a denial rule must exercise the real executor or mutation boundary, including relevant alternate callers that could bypass an upstream check.

## Independent outcomes

Report independent execution outcomes as separate facts. Acceptance, execution, persistence, synchronization, delivery, timeout, cancellation, and cleanup may coexist; do not collapse them into one success flag or infer one outcome from another.

## Public result contracts

A public Backend contract has one documented success, failure, cancellation, and uncertain-outcome model. Adapters normalize provider-, transport-, worker-, and implementation-specific result forms at the owning boundary before returning them to consumers.

Consumers depend only on the normalized contract and must not guess whether the same outcome arrives through an exception, status field, terminal event, empty value, or transport closure. Preserve internal defects as internal failures instead of misclassifying them as ordinary provider or business outcomes.

Test every supported source form through the real consumer-facing boundary.

## Failure containment and blocking decisions

A Tool, Provider, integration, observer, or optional-capability failure does not block the parent Run or unrelated work by default. Contain the failure at the owning capability boundary, record its exact outcome, and return a bounded, actionable error through the public result contract so the model or owning workflow can decide the next action.

Blocking a Turn, Run, downstream handler, or unrelated capability is an explicit product and Runtime contract. Before introducing new blocking semantics, identify why safe continuation is impossible, document the affected contract and recovery behavior, and confirm the decision with the user.

Security or authorization denial, durable-state corruption, protocol invalidity, and uncertain irreversible side effects may fail closed. Do not use these exceptions to turn ordinary Tool or Provider failures into global failures.

## State publication

Publish events, notifications, cache updates, projections, and user-visible state only after the authoritative operation reaches its documented commit point. A prepared, accepted, queued, or attempted operation is not a committed outcome.

Derived state must be rebuilt or updated from the authoritative committed fact, not from an optimistic side path. When an external side effect has an uncertain outcome, record and reconcile that uncertainty instead of publishing success or blindly repeating the operation.

## Complete-operation bounds

Apply item, byte, token, time, and concurrency limits at the owner of the complete returned, persisted, queued, or model-visible result. Include wrappers, metadata, retries, pagination assembly, and encoded representations when evaluating the bound; a limit on one intermediate step is not a complete operation bound.

Test limits below, at, and above the boundary, including one oversized item and multi-byte text where byte limits apply. Reject or truncate only according to the owning contract, and report truncation explicitly.
