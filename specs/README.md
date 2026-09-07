# Implementation contracts

[Backend Foundation](backend-foundation.md) records the agreed G003 scope. It is a design contract, not evidence that the services or schema exist. The owner ledger binds reviewed contracts and review evidence; the Goal gates define implementation acceptance.

Foundation approval artifacts stay at stable tracked paths. Later product contracts use `specs/backend-products/<module>.md`. Local `.omx/` plans are non-authoritative mirrors. Updating an Agent Note's lifecycle does not move an approved implementation contract.

Before approval, reviewers check ownership, current consumers, authorization lifetime, persistence, public service boundaries, failure and transaction behavior, upgrade requirements and named acceptance scenarios. Hashes, required fields and receipts verify artifact integrity and approval execution; they cannot establish semantic completeness by themselves.

An amendment requires review and explicit updates to the affected owner binding and linked coverage hashes. Later Auth registration/recovery workflows keep their separate product contract even though minimal Auth is implemented under the same owner in G003. An earlier foundation approval never approves those later workflows.

The [Audit observation contract](backend-audit-observation.md) replaces the foundation's coupled Audit transactions through an appended approval amendment. Its [implemented Note](../.agents/notes/implemented/architecture/2026-09-06-asynchronous-audit-observation.md) owns current behavior; original contracts, receipts and G003 evidence remain historical bindings.

[Execution Dependencies](backend-execution-dependencies.md) defines the reviewed G004 implementation scope, Agent-only shared/private Skills, MCP account selection and S2 schema-only product boundaries. It does not claim completed Workspace, Tool, Market, Provider or Runtime implementation.
