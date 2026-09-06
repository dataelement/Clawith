# Model configuration owner

This module owns Tenant Model configuration, the Tenant default relation, and Provider continuation persistence.

- `models.py` and `repository.py` are private. Other owners import only `public.py` and construct `ModelService` with their caller-owned `TransactionContext`.
- Model bindings accept only an active same-Tenant Tenant-owned Credential through Credential's public metadata contract. Model configuration never reads Secret bytes.
- Hard context/output limits and a recognized capability source are explicit. An enabled Agent Model must explicitly support Tool Calling; missing capabilities fail before enablement.
- Capabilities and non-Secret settings use the explicit version 1 JSON contract. Each object rejects normalized Secret-bearing fields, is limited to 8 levels, 100 items, and 16384 encoded UTF-8 bytes, contains only finite JSON values, and is copied at input and output boundaries.
- Provider endpoints require HTTP(S) and a host, and reject URL user information and explicitly Secret-bearing query parameter names. Ordinary endpoint paths and non-Secret query configuration remain valid. Provider endpoints and Credential references are private execution policy inputs and never belong in model-visible Context profiles.
- The Tenant default is resolved only when an Agent is created without an explicit Model. The Agent persists the resolved ID, so later default changes do not rewrite it.
- Archival disables and retains a Model. No hard-delete, Provider execution, fallback Model, Token quota, Model-step limit, or implicit capability probing is exposed here.
