# Agent Note: Preserve product context and private provenance in Run snapshots

Status: implemented — sourced product context and shared-Memory restrictions are captured with execution inputs.

## Problem

Session history and Group announcements must remain inspectable without being concatenated into the current input's bounded text. A2A receives explicitly supplied content under the target Agent's own authorization, but private source content must not become permission to publish into that Agent's shared Memory. Attachment preview results also need an explicit media contract rather than interpretation based on a Tool name.

## Decision

Snapshot source sections accept `product_context` alongside Memory and Skill indexes. Each section retains its subject, reference and content; Context sees it as sourced reference data, not platform instructions. Product owners select their authorized history cutoff and complete-operation bounds before capture. Run stores these sections without rereading live product records during execution.

`WorkspaceScope.allow_shared_memory_writes` carries a restrictive provenance flag, not another grant. Private A2A input or explicitly selected Membership credentials set it false during capture. Child snapshots inherit it, and further delegation cannot turn false back to true. Workspace's common mutation boundary rejects Agent Memory writes, edits, deletion and copy destinations when false; distillation applies the same check. The independent `allow_shared_file_writes` restriction from the [continuation amendment](../../../../specs/backend-product-input-continuations.md) protects shared Agent files for A2A receivers and private-origin Agent output. It does not grant a different Workspace or prevent ordinary writes to an already authorized User/Group output. Tool omission alone is not enforcement.

Tool Definitions can explicitly declare `result_format="content_blocks"`. The declaration is persisted and captured with the Tool, and the existing bounded MCP content presenter also handles these declared results. Unmarked non-MCP JSON remains plain text. No Tool-name switch, alternate media storage or result authority is added.

Missing optional fields retain their previous meaning. Snapshot v1 encoding omits the default unrestricted flag and absent result format so previously valid canonical hashes do not change. Restricted flags and explicit formats are serialized; unknown values are rejected at persistence decoding.

## Alternatives considered

Prepending history to the user's input would mix sources and consume the input limit twice. Enforcing privacy only through a distillation prompt or omitting one Tool would leave ordinary file mutations able to publish private material. Guessing media semantics from a Tool name would let unrelated JSON alter Model input representation. Explicit sourced sections, a Workspace-enforced restriction and captured result declarations preserve the existing owners.

## Consequences

Product composition must supply the restrictive flag whenever it introduces private provenance. This is not content classification, secret detection or permission to distill arbitrary private material. Agent Memory retains its existing source restrictions even when the flag is true. Snapshot and Tool codecs remain responsible for bounded persisted representation; Context does not decide authorization.

## Verification

Snapshot tests cover source round trips, Model-visible source roles, inherited restrictions and unchanged default v1 serialization. Tool tests cover persisted result declarations, explicit versus undeclared media interpretation and definition conflicts. Workspace tests exercise the common mutation denial. Product A2A tests verify private-source shared-Memory denial through the actual Tool executor. Live Provider interpretation and formal platform load remain unverified by these checks.
