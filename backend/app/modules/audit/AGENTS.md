# Audit owner

This module is the sole owner of append-only Audit records and their actor attribution.

- Other owners use `AuditService` and actor/view types from `public.py`; `models.py` and `repository.py` are private.
- Required Audit writes use the authoritative mutation's `TransactionContext`. Audit never commits independently, so mutation and attribution succeed or roll back together.
- Every record names one explicit Tenant and exactly one Membership, Platform Account, Agent, or System actor. Agent actors may identify a corresponding same-Tenant Run.
- Metadata is versioned, JSON-only, byte-bounded, structurally Secret-free, and does not duplicate product payloads or Run History.
- Queries require a captured Tenant administrator Principal, apply that Principal's Tenant scope, and enforce bounded pagination.
- Audit exposes no update, delete, direct HTTP write, Runtime, or authorization-generation behavior.
