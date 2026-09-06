# Implementation contracts

[Backend Foundation](backend-foundation.md) records the agreed G003 scope. It is a design contract, not evidence that the services or schema exist. The owner ledger binds reviewed contracts and review evidence; the Goal gates define implementation acceptance.

Foundation approval artifacts stay at stable tracked paths. Later product contracts use `specs/backend-products/<module>.md`. Local `.omx/` plans are non-authoritative mirrors. Updating an Agent Note's lifecycle does not move an approved implementation contract.

Before approval, reviewers check ownership, current consumers, authorization lifetime, persistence, public service boundaries, failure and transaction behavior, upgrade requirements and named acceptance scenarios. Hashes, required fields and receipts verify artifact integrity and approval execution; they cannot establish semantic completeness by themselves.

An amendment requires review and explicit updates to the affected owner binding and linked coverage hashes. Later Auth registration/recovery workflows keep their separate product contract even though minimal Auth is implemented under the same owner in G003. An earlier foundation approval never approves those later workflows.

The agreed [asynchronous Audit decision](../.agents/notes/proposed/architecture/2026-09-06-asynchronous-audit-observation.md) changes the target from the foundation contract's coupled Audit transactions. The original contract, receipts and G003 evidence remain unchanged historical bindings. The replacement is not implemented; its contract amendment and affected service/caller/test changes must be reviewed together before execution continues on that boundary.
