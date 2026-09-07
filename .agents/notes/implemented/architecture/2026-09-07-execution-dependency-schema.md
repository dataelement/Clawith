# Agent Note: Execution dependency and product-source schema

Status: implemented — S2 registers 23 owner-private tables in the existing metadata registry; product execution remains separately gated.

## Problem

Workspace, installed capabilities and product inputs reference the same Tenant, Agent and Run graph. Registering each module independently would leave unresolved relationships or allow an identifier to cross its actual owner boundary.

## Decision

The [G004 contract](../../../../specs/backend-execution-dependencies.md) defines the nine S2 owners. Their private models register together through the [existing integration point](../../../../backend/app/infrastructure/schema.py), yielding 40 tables across S0–S2. Same-Tenant composite foreign keys constrain Agent, Credential, source and Run relationships. Deletes use RESTRICT; services must explicitly settle dependent facts instead of silently cascading them away.

Workspace identity is exactly one Membership, Agent or Group. Skill package ownership distinguishes Tenant-shared from Agent-private even if their UUID values coincide. Only the owning Agent can bind a private package. A shared Catalog Skill has one shared package per Tenant and source. Skill names permit hyphens; Tool canonical names retain their separate naming contract.

Catalog platform templates cannot serve as executable Tenant bindings. Materialized platform origins reference platform records only. Skill and MCP references constrain the Catalog kind. MCP grants agree with the connection's Agent and source; Credential references agree with their owner kind and identity. A nullable human grantor permits Agent self-install without fabricating a Membership.

Session input and reply positions are distinct facts. Waiting replies link to a Main Run of the same Session. Goal iterations can reuse an original input with distinct history cutoffs; no global equality constraint makes those iterations invalid. A2A source and target Runs match their respective Agents. A pending result delivery requires an object result, not SQL NULL or JSON null. Group, Trigger, Heartbeat and Channel records retain their source and correlation identities without duplicating Run lifecycle states.

## Alternatives considered

Separate module registries were rejected because the foreign-key graph needs one integration authority. Task, Goal, Skill-history and Tool-execution tables were excluded by the approved architecture: they would add persistence owners absent from the execution contract.

## Consequences

Schema registration does not enable product APIs or workers. Session, A2A, Group, Trigger, Heartbeat and Channel remain schema-only until their execution stage. Startup performs no DDL; migrations remain deferred to G008. Schema test fixtures may construct records directly to verify constraints, without authorizing cross-owner ORM use in services.

## Verification

`uv run --extra dev pytest tests/database/test_schema_wave_S1.py tests/database/test_schema_wave_S2.py tests/architecture/test_owner_package_skeleton.py -q` passed 74 tests. S2 coverage exercises PostgreSQL creation/drop, valid relationships and cross-owner constraint violations. Independent schema code and architecture reviews found no remaining blocker. These checks do not establish Runtime, product E2E, migrations, deployment or 50-execution performance.
