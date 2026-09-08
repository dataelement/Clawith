# Agent Note: Restrict shared Memory distillation to Agent-owned execution

Status: implemented — Workspace rejects personal and Group execution scopes before shared Memory writes.

## Problem

The former Direct/Group distillation exception allowed model-supplied text to enter shared Agent Memory. A prompt asking for generalized knowledge did not enforce privacy or Secret filtering. Every authorized user of the Agent could later read the result.

## Decision

The first release forbids distillation from Membership and Group execution contexts. Only a non-preview Main with an Agent-owned Workspace output scope may invoke `distill_memory`. Workspace enforces the restriction at its public mutation boundary; application composition also omits the binding from ineligible Runs. The operation no longer replaces a private output scope with an Agent scope to authorize a write.

This supersedes the Direct/Group distillation exception in the [Workspace proposal](../../proposed/architecture/2026-08-27-user-agent-group-workspaces.md) and narrows the Main distillation operation in the frozen [execution-dependencies contract](../../../../specs/backend-execution-dependencies.md). Ordinary private Memory editing and authorized reads of shared Agent Memory remain unchanged. Subagent distillation remains forbidden.

After a successful conditional write, asynchronous Audit receives the source Run, source Workspace and SHA-256 content hash, not the content. Audit does not authorize the write or decide whether it succeeded.

## Alternatives considered

Allowing private-context distillation with only model instructions retains the observed disclosure path. Deterministic privacy classification and approval were not selected for this release; the user chose to prohibit the private-context operation instead.

## Consequences

This is an execution-scope restriction, not a semantic PII or Secret detector. Product owners must preserve private input provenance when constructing trusted execution scopes; moving private input to an Agent-owned output destination does not make that input Agent-owned. Future A2A, Trigger and attachment entry paths must enforce that distinction before permitting shared Memory mutation. The restriction does not retroactively sanitize previously stored Memory.

## Verification

Service tests deny Membership and Group distillation before storage or Audit, allow Agent-owned Main writes, verify the content hash and retain Subagent denial. Tool executor tests cover private-scope rejection and successful Agent-owned publication. Real Provider behavior and later product input routing are not established by these tests.
