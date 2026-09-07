# Agent Note: Foundation schema and shared transactions

Status: implemented — S0/S1/S2 metadata uses one registry and caller-owned transactions; application startup does not create tables.

## Problem

Foundation owners need relational constraints across module boundaries without introducing another SQLAlchemy registry or permitting services to write another owner's tables. An operation that changes several owners must not publish only part of its result.

## Decision

[`register_schema`](../../../../backend/app/infrastructure/schema.py) explicitly loads the 18 approved S0/S1/S2 schema owners into the existing Base. The graph contains 40 tables. Composite foreign keys constrain Tenant ownership, login Account/Membership correspondence, Agent-owned parent Runs, and Run-related records. Closed alternatives use CHECK constraints. Registration performs no DDL and adds no execution behavior. Run, Context, Session, A2A, Group, Trigger, Heartbeat and Channel services remain outside this schema integration; the [S2 Note](2026-09-07-execution-dependency-schema.md) describes the additional relationships.

[`transaction`](../../../../backend/app/infrastructure/transactions.py) provides one AsyncSession through a typed TransactionContext. Public services share that context; private repositories flush but do not commit. The enclosing operation commits once on success and rolls back on failure or cancellation before releasing its connection. External Model/Tool work must run outside this transaction.

The PostgreSQL fixture creates a unique schema for each test. It uses either an explicitly supplied test connection or its own loopback-only disposable Compose project. Teardown exercises metadata drop, removes only the generated schema even if metadata cleanup fails, verifies its absence, and disposes the engine. Startup and Alembic remain unchanged; the target baseline is deferred to G008.

## Alternatives considered

**Separate registries per module.** Rejected because cross-owner foreign keys require one integrated schema authority.

**Repository-local commits.** Rejected because they can leave participating authoritative owner changes partially committed. Audit observations are independent and asynchronous under the [Audit contract](2026-09-06-asynchronous-audit-observation.md); their loss does not invalidate a business commit.

**SQLite-only constraint tests.** Rejected because PostgreSQL generated columns, composite constraints and transaction behavior are part of this contract.

## Consequences

Schema integration remains serialized even when public services are implemented in parallel. Tests may provision Run records directly only to exercise schema constraints while the Run service is unavailable. Ordinary cross-owner service tests use public contracts.

## Verification

Real PostgreSQL tests create and drop the complete registered graph and exercise valid and invalid Tenant and identity relationships. Transaction tests observe pre-commit invisibility, committed rows, failure/cancellation rollback and zero checked-out connections after completion. This is local database evidence, not application routing, migration, Provider, Runtime, browser or 50-execution load acceptance.
