# Identity and Tenant owner

This module is the sole owner of Account, Tenant, Membership, and the identity/role fields of human Tenant principals.

- `models.py` and `repository.py` are private. Other owners import only `public.py` and construct `IdentityService` with the caller's `TransactionContext`.
- Provisioning is explicit through service calls. Startup never seeds identities or repairs Identity/Tenant state.
- Repositories flush but never commit. The outer application operation owns commit, rollback, cancellation, and session cleanup.
- Identity constructs `TenantPrincipal` identity and captured role fields with no admitted Agent IDs. Permission is the sole producer of `allowed_agent_ids`; Auth only persists and decodes that fixed result and does not reevaluate it.
- Identity authorization helpers enforce captured administrator role and Tenant equality. Agent access policy belongs only to Permission.
- Every query or mutation is explicitly Tenant-scoped and bounded. Identities are disabled rather than hard-deleted.
- Platform principals target one explicit Tenant but are not ordinary Tenant execution principals.
- Authorized product invitation flows may use the bounded `invitation_candidates` projection of active same-Tenant membership IDs and display names. This does not expose account IDs or roles, replace the administrator-only membership list, or implement an organization directory.
