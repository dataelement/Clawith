# Agent Note: Explicit Builtin provisioning

Status: implemented — persistent provisioning converges under concurrent registration; actual Agent creation routing remains separate.

## Problem

Code-owned executors do not establish Agent permission to use them. A creation flow needs explicit persisted grants in its own transaction, while simultaneous Agents must share one Tenant definition instead of failing on the same canonical name.

## Decision

Application composition supplies `provision_builtin_tools` to the authenticated Agent-creation flow. It requires administrator authority, checks the Agent first, and invokes only public owner services. Code-owned Definitions and explicit grants share the caller's transaction. Startup never provisions or repairs data.

Tool registration inserts an absent canonical name with PostgreSQL conflict handling, reads the authoritative winner and checks definition compatibility. Concurrent compatible registrations converge without replacing descriptions, schemas or accounts. Incompatible definitions remain conflicts. This is an owner-local persistence operation, not an external operation retry.

Provisioning is idempotent for matching active grants, including concurrent calls for the same Agent. A revoked or differently bound Credential/connection remains a conflict; invoking setup again does not silently restore permission. Unknown grant configuration versions or nonempty v1 settings fail explicitly. Run role eligibility and direct-versus-searchable exposure are resolved separately from persisted grants.

## Alternatives considered

Implicit Builtin permission would bypass the explicit grant contract. Startup repair would make deployment a hidden policy writer. Retrying an entire initialization after a unique-name conflict would repeat unrelated work rather than repair the registration owner.

## Consequences

Builtin registration does not create a new permission authority or deployment migration. Callers must propagate failures to their enclosing transaction. Added capabilities still require explicit provisioning rather than a startup repair path.

## Verification and gaps

Real PostgreSQL tests cover persistence, repeated provisioning, another Agent remaining ungranted, transaction rollback, revoked-grant preservation, unsupported persisted configurations, administrator enforcement and simultaneous definition/grant registration. Independent code and architecture reviews found no remaining blocker in this slice. The actual Agent HTTP creation route and Runner remain later-stage consumers. This does not claim complete application assembly or G004 completion.
