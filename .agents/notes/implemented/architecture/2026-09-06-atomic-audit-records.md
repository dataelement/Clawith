# Agent Note: Audit records share the mutation commit

Status: implemented — Audit supports append and bounded Tenant reads through the caller's transaction.

## Problem

A required Audit record must not disappear independently of the change it describes. Actor attribution must distinguish a human Membership, platform Account, Agent/Run or named System component without accepting contradictory identities.

## Decision

Audit owns an append-only public service and private persistence. Its closed actor union maps to database CHECKs and same-Tenant foreign keys; an Agent's optional Run must belong to that Agent. Metadata uses one explicitly supported JSON schema version with depth, item and complete UTF-8 byte bounds, finite JSON values and recursive rejection of known Secret field names. Returned metadata is copied rather than exposing mutable ORM state. Secret-free metadata remains a caller contract; key-name validation cannot identify every possible secret value.

Required Audit writes use the authoritative mutation's TransactionContext and commit or roll back with it. Audit does not start another transaction, write another owner's tables, publish success early, or provide update/delete operations. Tenant-administrator queries filter by Tenant and paginate in SQL. Actor references used only in schema fixtures are not product APIs.

## Alternatives considered

**Independent asynchronous Audit persistence.** Rejected for required records because the authoritative mutation could commit without its evidence.

**Several nullable actor IDs without a closed union.** Rejected because contradictory attribution could be persisted.

## Consequences

The outer application operation decides which mutations require Audit and supplies the actor. Logging remains independent observability, not a replacement for durable Audit. Product workflow orchestration is not implemented by this service.

## Verification

Real PostgreSQL tests exercise all actor variants, invalid mixed actors, cross-Tenant references, Agent/Run mismatch, metadata limits, version rejection, scoped reads and rollback of a public Identity mutation when the required Audit write fails. External delivery, user-facing Audit pages and product mutation coverage remain deferred to their owners.
