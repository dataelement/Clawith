# Agent Note: Credential encryption and owner access

Status: implemented — Credential services provide encrypted storage and owner-scoped metadata; execution adapters remain later consumers.

## Problem

Tenant, Agent and Membership credentials need shared storage without exposing plaintext through configuration views or treating permission to use an Agent as permission to replace its account credentials.

## Decision

Credential owns encryption, versioned payload decoding, metadata and availability checks. Composition explicitly injects an AES-GCM keyring; there is no generated default key or plaintext fallback. A fresh nonce and authenticated data bind each ciphertext to its Credential ID, Tenant and payload version. Records retain the encryption key version so a keyring containing retained keys can read older data. Unknown formats, missing keys and authentication failure fail explicitly. `Secret` has a redacted representation; only the explicit resolved-owner reveal port returns plaintext to trusted execution consumers.

Tenant administrators manage Tenant and Agent credentials. Members may manage their own Membership credentials; captured Agent use access does not grant Credential read or mutation access. List queries apply this scope before pagination. Model configuration validates a Tenant-owned Credential through public metadata without obtaining the Secret. Rotation retains identity and replaces encrypted bytes; revocation preserves the record and prevents actual use without introducing Run cancellation.

The dependency declaration makes the already locked `cryptography` package direct; it adds no package to the resolved dependency graph.

## Alternatives considered

**Plaintext in Model or Agent configuration.** Rejected because configuration, logs and Context projections must not become Secret stores.

**Grant Credential administration with Agent use.** Rejected because an ordinary user could replace or revoke a shared Agent account.

**Filter metadata after pagination.** Rejected because inaccessible rows could produce an empty page before accessible rows have been considered.

## Consequences

Transport must not expose the trusted reveal port or serialize `Secret` values. G004 capability owners must supply the authorized binding; arbitrary request fields are not authorization. Key deployment and retention are explicit composition responsibilities. The current API surface remains health-only.

## Verification

Tests cover nonce freshness, redacted representation, encryption roundtrip, rotation, incorrect keys/Tenants, corruption, unknown payload versions, revoked credentials, cross-Tenant lookup denial, Agent-use mutation denial, self-owned Membership credentials and permission filtering before pagination. Real Provider/MCP use and deployment key rotation remain unverified.
