# G003 foundation contract review

Reviewed on 2026-09-06 against base `e9523eacfa392fb9cd9b13408052470aad79da8d` and the current preparation diff.

Contract: `specs/backend-foundation.md`

Contract SHA-256: `b55de94780a126d55e2ae9ef7dfa4f3d1b7d2c4e1433559b231b277d89f70017`

## Reviewed scope

The contract covers G003 implementation of Identity/Tenant, Credential, Model configuration, Agent, Permission, minimal Auth and Audit; Run/Context schema only; and contract-only prerequisites for Workspace, Tool and Capability Market. It does not approve full Auth product workflows, SSO, later product modules, G004 execution dependencies or G005 Runtime implementation as completed work.

Human authorization is fixed for a login session. Current Agent-owned execution configuration is resolved before each new Run and fixed in its Snapshot, allowing new installations to appear in a subsequent Run without another login. Login expiry is required, with 24 hours only a candidate. Live authorization generations, dependency projections and revocation cancellation sweeps are excluded. Explicit cancellation and ordinary missing-resource failures remain.

The review also covers one metadata registry and shared transaction ownership, private owner persistence with public service boundaries, private execution Snapshot versus model-visible Context, Credential-only Secret storage, ordinary attachments outside Workspace, and the Waiting/input handoff. Routine implementation details remain subject to the corresponding owner tests, rather than requiring a new product decision for each field or helper.

## Independent verdicts

- Architecture lane `login_auth_g003_arch_review`: CLEAR; approved for owner binding after structural gate checks. The login-versus-Run scope question was resolved by the user and recorded in the contract. Requested wording corrections distinguish expired login sessions from password credentials and SSO flow ownership from Auth-owned login sessions; both are applied.
- Code/contract lane `g003_login_contract_consistency`: APPROVE; no additional product-architecture blocker, duplicate owner or dependency cycle. Preparation scripts and their cumulative receipt behavior are separately tested before handoff.

## Evidence boundary

This record approves the scoped design, not its domain implementation. Per-owner receipts bind this contract and review record. G003 remains incomplete until its real PostgreSQL schema and foundation service tests pass. No production database, external Provider, hosted CI, frontend, 50-Agent load or Runtime E2E is certified here. Receipt integrity is not a replacement for semantic review or implementation acceptance.
