# Agent Note: Consistent login authorization capture

Status: implemented — minimal Auth services issue and validate opaque sessions; product login transport and execution integration remain deferred.

## Problem

Human authorization must remain fixed during a login session, while concurrent role or password changes must not produce a partially captured login. Password hashing must not occupy database transactions or block the event loop.

## Decision

Auth owns salted versioned scrypt verifiers and opaque random session tokens whose digests alone are persisted. KDF work runs off the event loop and outside transactions. After verification, a repeatable-read transaction locks and rechecks the verifier, resolves enabled Identity/Tenant facts, captures Permission scope and commits the session. Concurrent verifier replacement is serialized with this final check; concurrent role/grant edits cannot mix different database snapshots. Serialization conflicts fail explicitly without hidden retry.

Authentication reads the stored session, validates its versioned captured authorization, expiry and logout marker, and returns the captured Principal. It does not reload human roles or Agent grants. Logout invalidates that session without cancelling Runs. Trusted verifier provisioning is explicit and is never startup seeding or a public registration endpoint.

The service requires an explicit lifetime and stores `expires_at`. It rejects an expired stored session and does not renew it implicitly. G006 product intake will supply the user-confirmed [fixed 24-hour login policy](../../proposed/architecture/2026-09-06-login-session-authorization.md), with no automatic renewal or cancellation of existing Runs. That product policy is not yet an implemented default of this minimal service.

## Alternatives considered

**Reload current roles on each operation.** Rejected by the approved login-session scope: edits affect the next login rather than cancelling current work.

**Capture under several read-committed reads.** Rejected because paginated grants and identity facts could describe different committed states.

**Hold a transaction during password hashing.** Rejected because CPU work would occupy scarce database connections.

## Consequences

New Run configuration will still resolve current Agent-owned capabilities inside the captured human scope; login does not freeze Tool/MCP/Skill installations. Product transport must validate tokens before accepting a Principal. There is no SSO, registration, password recovery, live revocation sweep or executable Run in this slice.

## Verification

Real PostgreSQL tests cover frozen scope after role edits, new scope on relogin, wrong passwords, Tenant isolation, expiration, logout, invalid stored authorization and deterministic concurrent password/role changes. KDF/token tests verify salting and digest-only storage. HTTP login, browser behavior, provider execution and load capacity remain later evidence.
