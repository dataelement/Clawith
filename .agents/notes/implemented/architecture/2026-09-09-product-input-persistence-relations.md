# Agent Note: Bind product messages and transport state to their exact sources

Status: implemented — G006 product relations are registered in the shared S2 metadata before the G008 migration baseline.

## Problem

Product messages, Group conversations, attachments and Channel delivery need durable associations without duplicating Run lifecycle. A tenant-correct foreign key alone is insufficient when a message can name another Agent's Run, another input or another conversation within that Tenant.

## Decision

Session and Group retain their existing input, message and Run-link owners. A message's execution source references the exact originating Run-link input; Group additionally binds the Agent and conversation. Group Run links reference the conversation of their input event. Message source keys and delivery correlations deduplicate their own facts without creating a Task or Goal table.

Group membership, Agent participation, conversations and read watermarks have scoped relations and unique participant/read keys. One enabled default conversation is allowed per Group. Group owns conversation positions and closure; Run still owns execution termination.

Session and Group attachment rows identify the uploader, immutable metadata, optional first input binding, publication revision and unbound expiry. Publication revision and time are either both present or both absent. A cleanup claim is valid only while unbound. Partial unbound indexes support bounded cleanup scans; physical storage and owner-level authorization remain outside these constraints.

Channel actor and group mappings, stable direct conversations and input routes retain their owning Tenant and Agent. A route names exactly one real input. Reply contexts bind the originating Channel configuration and encrypted payload; clearing expired bytes does not remove delivery associations. Delivery distinguishes pending, delivered, failed and uncertain, with only one original Discord reply per context. Private synchronization cursors use scoped uniqueness, positive CAS versions and explicit transport coordinate kinds; a partial global pending index follows the worker's scan. They represent provider transport consumption, not recovery of Agent execution.

All records use the existing Base and metadata registry. Existing schema-only fixture rows may omit an execution source; once present, its full source association is enforced. New application producers always record their actual source. No startup schema mutation or legacy migration compatibility is added.

## Alternatives considered

Independent message/task state machines would duplicate execution facts. Loose same-Tenant references would allow conflicting source identities. Deleting expired Channel context rows would break durable delivery associations. Composite relations and bounded owner-specific transport metadata preserve existing authority without these additional mechanisms.

## Consequences

Database constraints protect relationships and basic shapes, not human authorization, Model interpretation or physical file publication. Owners validate those operations before mutation. All changes precede the first target migration baseline; G008 still owns migration creation and fresh-install verification.

## Verification

S2 PostgreSQL tests create the complete shared graph, verify owner registration and foreign-key resolution, and insert every registered table. Product owner and application tests exercise source linkage, conversation deletion, attachment claim/bind ordering and delivery deduplication. Dedicated negative schema tests reject cross-source lineage, invalid publication/claim combinations and transport relationship conflicts. These checks do not prove production migrations or external provider delivery.
