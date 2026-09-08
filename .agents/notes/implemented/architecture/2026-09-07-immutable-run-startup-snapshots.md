# Agent Note: Immutable Run startup snapshots

Status: implemented — versioned Snapshot encoding and private transactional storage are available.

## Problem

A Run must not change its Model, authorization or fixed instructions when configuration changes. Reading an old Snapshot through evolving public dataclass shapes would also make a public-interface addition silently redefine the stored version.

## Decision

Run Snapshot stores an explicit Tenant/Agent/role, versioned Platform Instructions, Agent identity/Soul/timezone, fixed Model policy and profile, role-independent authorized Tool bindings, initial direct exposure, Workspace scope, Skill discovery and labelled Memory/Skill index sections. Initial work and later inputs belong to History and are not copied into Snapshot. Private execution settings and Credential references never enter the selected model-visible prefix.

Version 1 uses frozen private DTOs and explicit conversion to owner-produced public values. A canonical SHA-256 covers kind, version and payload. Reads reject unknown versions, shape changes, inconsistent scope, invalid Model policy/profile relations or a hash mismatch rather than refreshing current configuration. Future public optional fields do not change the stored version-1 representation. A schema evolution must preserve the existing version reader or provide an explicit data-preserving migration.

The private repository operates in the caller's transaction. An insert requires the corresponding Run with matching Tenant, Agent, role and identity. A same-hash retry returns the original Snapshot; different content cannot replace it. Metadata bounds uncompressed payload bytes before JSON loading, followed by exact typed and hash validation. A Snapshot is limited to 16 MiB; source sections and collections have separate physical bounds.

A fresh insert reuses its validated version-1 DTO to return a detached immutable value, checking DTO equality after conversion. It does not decode and hash the just-created payload again. Existing-record retries and persisted reads retain the full version, shape, scope and hash checks.

Child derivation retains the exact Model, authorization, Skill discovery, sources and initial exposure. Only its role and Workspace Run identity change. Tool-owned role filtering then excludes Main-only capabilities and retains authorized Todo. No Parent or sibling History is inherited implicitly.

## Alternatives considered

Re-querying live grants for a Child would expand Parent authorization. Deriving from a filtered Main Tool view would lose Todo. Serializing public dataclasses directly would couple old records to future Python interface changes. Rebuilding an unreadable Snapshot from live settings would overwrite the evidence of how execution actually started.

## Consequences

Snapshot validation establishes representation and scope consistency, not new login authorization. Authenticated product intake and capability owners supply trusted values. Lifecycle start must commit Run, Snapshot and initial input atomically; Snapshot storage alone is not a complete start operation.

## Verification

Forty focused tests passed, covering exact canonical round trips, hash and field corruption, frozen version-1 decoding after a public type extension, role/Workspace/Credential scope, Model protocol and capability consistency, initial exposure, immutable retries, rollback, Tenant isolation and prefetch bounds with real PostgreSQL. Fresh insertion performs one encoding without a redundant decode; existing reads remain checked. Ruff, Pyright and independent Snapshot code review passed. Application admission and full G005 acceptance are separate evidence.
