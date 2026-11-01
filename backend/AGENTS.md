# AGENTS.md — Clawith Backend

These backend-specific rules apply to `backend/**` and supplement the
repository-wide [conventions](../AGENTS.md#2-conventions).

The Backend is a Python 3.11+ FastAPI application built on SQLAlchemy's
asynchronous APIs, PostgreSQL, Redis, and LangGraph with PostgreSQL
checkpoints. It contains the Agent Runtime, product APIs, persistence,
background execution, and external integrations.

Project metadata and dependency declarations are defined in `pyproject.toml`;
`uv.lock` records the resolved dependency graph.

## Commands

Run Backend commands from `backend/`:

| Action | Command |
|---|---|
| Install project and development dependencies | `uv sync --extra dev` |
| Run the development server | `uv run uvicorn app.main:app --reload --port 8000` |
| Run a focused test file | `uv run --extra dev pytest tests/<test_file>.py` |
| Run the complete Backend test suite | `uv run --extra dev pytest` |
| Run lint checks | `uv run --extra dev ruff check .` |
| Run static type checks | `uv run --extra dev pyright app` |
| Apply database migrations | `uv run alembic upgrade head` |

Use focused Pytest targets during development. Run the complete Backend suite
only when the affected contracts cross multiple Backend areas or when required
by the repository testing policy.

Read [`alembic/AGENTS.md`](alembic/AGENTS.md) before creating or editing a
database migration.

## Application layout

```text
pyproject.toml  Project metadata, dependencies, and tool configuration.
uv.lock         Locked Python dependency graph.
alembic/        Database schema migrations.
scripts/        Repository-operated Backend maintenance and data-migration scripts.
tests/          Backend unit, contract, integration, and regression tests.
app/main.py    FastAPI application composition, lifespan, middleware, and router
               registration.
app/config.py  Application configuration entry point.
app/database.py
               Database engine and Session infrastructure.
app/api/       HTTP and WebSocket transport adapters.
app/schemas/   Request, response, and transport validation models.
app/models/    SQLAlchemy persistence models.
app/dao/       Database access and query ownership.
app/services/  Product services, Runtime capabilities, background execution,
               and external integrations.
app/core/      Cross-cutting security, permissions, errors, events, logging, and
               middleware.
app/scripts/   Application maintenance, bootstrap, backfill, and migration tools.
```

Read the nearest nested `AGENTS.md` before modifying a specialized subtree.
Detailed module structure belongs to that subtree's instruction or owning
architecture document, not this file.

## Async lifecycle

Represent one asynchronous operation with one lifecycle controller or
transaction. Readiness, cancellation, disposal, reservation, and sentinel state
remain in that owner unless they describe an independently owned object or
settlement point. Do not split one operation into parallel lifecycle state
machines.

## Lifecycle verification

Tests for registration, cancellation, shutdown, and cleanup must observe the
owned resource reaching its terminal or removed state. Asserting only that
`cancel()`, `close()`, `dispose()`, or a cleanup callback was invoked is not
sufficient evidence that work stopped or resources were released.

## API and service boundaries

API handlers are transport adapters. They parse and validate request data,
establish the authenticated and authorized caller, pass explicit inputs to the
owning service or command-intake boundary, and map the result to the transport
response. Do not put business orchestration, ORM queries, Runtime node calls,
checkpoint mutation, or private lifecycle control into an API handler.

Design shared service contracts for all current consumers. Keep transport-,
UI-, channel-, and provider-specific behavior in the owning adapter or consumer.
Do not widen a public service for one internal caller; keep single-consumer
capabilities private until a real shared contract exists.

## Public choices

Do not invent public defaults, modes, operation sets, API fields, event fields,
or persisted formats merely to make an interface appear flexible. Every public
choice must be supported by a current consumer, an owning product or
architecture contract, or established behavior already used by the system.

When that evidence does not exist, require the caller to provide an explicit
value or defer the choice instead of introducing a speculative default or
extension point.

## Model-facing contracts

Write prompts, Tool schemas, Tool results, and model-visible diagnostics from
the model's task perspective. Include the information needed to choose and
complete the next action; do not expose UI state, transport details, database
structure, internal service names, or implementation vocabulary unless the
model must act on that concept.

A failure on a model-visible path must return a bounded, actionable result that
identifies the failed subject, the relevant condition, and any safe next action.
Do not silently drop the failure or dump stack traces, raw provider responses,
internal records, or unbounded diagnostic output into model context.

Treat stable model-visible wording and schemas as behavior. Changes require an
update to the owning contract and verification through the assembled model
request or Tool execution path.

## Enforcement

The operation that reads protected data, mutates authoritative state, or causes
an external side effect must obtain and enforce authorization, tenant scope,
limits, and policy decisions from the owning Backend permission model at that
execution boundary. Upstream layers may perform an equivalent preflight for
faster feedback, but Frontend visibility, prompt instructions, Tool-schema
omission, API wrappers, and ordinary call ordering are user-experience guidance,
not security enforcement.

Tests for a denial rule must exercise the real executor or mutation boundary,
including relevant alternate callers that could bypass an upstream check.

## Independent outcomes

Report independent execution outcomes as separate facts. Acceptance, execution,
persistence, synchronization, delivery, timeout, cancellation, and cleanup may
coexist; do not collapse them into one success flag or infer one outcome from
another.

## Public result contracts

A public Backend contract has one documented success, failure, cancellation,
and uncertain-outcome model. Adapters normalize provider-, transport-, worker-,
and implementation-specific result forms at the owning boundary before
returning them to consumers.

Consumers depend only on the normalized contract and must not guess whether the
same outcome arrives through an exception, status field, terminal event, empty
value, or transport closure. Preserve internal defects as internal failures
instead of misclassifying them as ordinary provider or business outcomes.

Test every supported source form through the real consumer-facing boundary.

## State publication

Publish events, notifications, cache updates, projections, and user-visible
state only after the authoritative operation reaches its documented commit
point. A prepared, accepted, queued, or attempted operation is not a committed
outcome.

Derived state must be rebuilt or updated from the authoritative committed fact,
not from an optimistic side path. When an external side effect has an uncertain
outcome, record and reconcile that uncertainty instead of publishing success or
blindly repeating the operation.

## Complete-operation bounds

Apply item, byte, token, time, and concurrency limits at the owner of the
complete returned, persisted, queued, or model-visible result. Include wrappers,
metadata, retries, pagination assembly, and encoded representations when
evaluating the bound; a limit on one intermediate step is not a complete
operation bound.

Test limits below, at, and above the boundary, including one oversized item and
multi-byte text where byte limits apply. Reject or truncate only according to
the owning contract, and report truncation explicitly.
