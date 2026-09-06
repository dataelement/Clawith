# Agent owner

This module owns the Tenant-scoped Agent core record: identity, presentation fields, Soul, timezone, required Model binding, enabled/archive state, and creation attribution.

- `models.py` and `repository.py` are private. Other owners import only `public.py`.
- Agent creation resolves an explicit or current Tenant default Model once and persists the resulting Model ID. Later default changes never rewrite existing Agents.
- Creation and management require a captured Tenant administrator Principal. Soul is required and timezone values use the IANA timezone database.
- Update distinguishes omitted optional presentation fields from explicit `None`; `None` clears avatar, description, or greeting.
- Archival disables the Agent and preserves the record. This owner exposes no hard-delete operation and creates no Workspace, Tool, capability, or permission grant.
- Permission may consume only the bounded, explicitly Tenant-scoped `AgentMetadataView` queries. It does not import Agent persistence.
