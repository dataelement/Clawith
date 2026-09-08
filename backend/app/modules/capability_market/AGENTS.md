# Capability Market owner

`public.py` owns bounded Platform/Tenant discovery and explicit Agent installation orchestration. `repository.py` and `models.py` are private. The [G004 contract](../../../../specs/backend-execution-dependencies.md#capability-market) owns sharing and activation semantics.

Registration does not grant execution. Partial unique indexes deduplicate each source; Agent activation uses public Tool/Workspace services after external preparation, in a separate short database phase. Do not move source content, account policy or Skill bindings into Catalog JSON. Audit observes committed facts and never decides installation success.

Self-install ports accept only the trusted `AgentInstallScope` injected by an explicitly granted executor; never construct that scope from model input. They register missing Tenant sources and activate only that Agent, not administrator mutations, shared refresh or another Agent's credentials. Catalog schema v1 carries only its version marker; bounded discovery identity and display metadata occupy typed columns, not arbitrary manifest payloads.

Tool/Skill configuration injects the bounded `enabled_source_ids` read port. It queries only Catalog facts through the caller's `TransactionContext`, never borrows a nested connection, commits, or invokes Tool/Workspace. Disabled sources affect new configuration discovery, not already resolved Run bindings. Source-backed consumers must fail explicitly if this port is absent.

Only Agent Skill installation exists. Shared publication affects remaining shared bindings; private publication rebinds only its Agent through Workspace. No User/Group Skill namespace, install state machine or historical package archive is added.

Catalog-backed Skill publication requires Market's active-source guard inside the final Workspace transaction. Source locking orders publication against concurrent disablement; external preparation remains outside the transaction. Template materialization never bypasses the existing Tenant source's disabled state.
