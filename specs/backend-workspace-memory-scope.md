# Workspace contract amendment: Agent-owned shared Memory

Status: approved — the user confirmed the scope restriction and its addition to the contract approval chain.

## Contract baseline

This is the current Workspace owner contract together with the unchanged Workspace requirements in [Backend execution dependencies](backend-execution-dependencies.md), SHA-256 `e23c3e112800e955016da113fffb0680c76c2c406d918a6c5aa44c5d903def3a`. Only the shared Memory distillation exception is narrowed below. Other owners remain bound to their existing contracts; this amendment introduces no schema, API or execution behavior beyond the implemented restriction.

## Shared Memory restriction

Membership and Group execution contexts cannot distill into shared Agent Memory. `distill_memory` requires a non-preview Main whose trusted Workspace output scope is its own Agent Workspace. Workspace enforces this before mutation, and Tool composition omits the binding from ineligible Runs. The service cannot replace a private scope with an Agent scope to authorize publication.

Subagent distillation remains forbidden. Ordinary scoped Memory reads and writes retain their existing authorization, including inherited Agent-owned Subagent file access. Successful distillation emits source Run, source Workspace and content hash to independent asynchronous Audit; Audit does not authorize the operation or govern success.

This is an execution-source restriction, not semantic PII or Secret filtering, data-owner approval or retroactive sanitization. Product input owners must retain private provenance: choosing an Agent output destination does not convert personal or Group input into Agent-owned context. Later A2A, Trigger and attachment integration must preserve that boundary.

The [owning Memory Note](../.agents/notes/implemented/architecture/2026-09-08-agent-owned-memory-distillation.md) records the rationale and rejected alternatives. Original contracts and receipts remain immutable; a new Workspace amendment receipt binds this contract and its review evidence.
