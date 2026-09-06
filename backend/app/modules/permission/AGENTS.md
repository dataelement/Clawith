# Permission owner

This module owns Agent visibility policy, explicit Membership/source-Agent grants, and the common `none`/`use`/`manage` resolver.

- `models.py` and `repository.py` are private. Other owners import only `public.py` and pass the caller-owned `TransactionContext`.
- Tenant administrators manage visibility and grants. Their all-Agent authority remains role-derived and `freeze_principal` never enumerates Agent IDs for them.
- A member login captures at most 1000 active visible Agent IDs. Resolution scans at most 10000 visibility rows; either exceeded bound fails explicitly and never truncates access silently.
- Human authorization is fixed in `TenantPrincipal` for the login session. Later role or grant edits do not mutate that value. New login resolution captures current policy.
- Autonomous intake resolves current same-Tenant source-Agent visibility. Runner and model steps do not poll permissions or create generation projections, cancellation sweeps, or live reauthorization.
- Permission reads Agent state only through Agent's bounded public metadata queries. It never imports Agent persistence or writes Agent-owned records.
- Grants are revoked and retained. This owner exposes no hard-delete operation, custom roles, ABAC, approval catalog, or per-Agent management grant.
