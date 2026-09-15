# G004 execution dependency contract review

Contract: `specs/backend-execution-dependencies.md`.

The user approved Agent-only Skills, shared package updates affecting shared consumers and private updates affecting only their Agent, complete temporary preparation before publication, optional MCP authentication and Agent-default versus explicitly authorized personal account selection. Sandbox remains deferred. Audit follows its separately reviewed observation contract.

Independent code/security review `g004_preflight_code_review`: APPROVE for the implementation contract. Current owner privacy, bounded inputs, account-scoped discovery, no implicit account fallback and unchanged Tenant constraints are preserved. The shared/private Skill distinction does not create another Workspace subject or authorize arbitrary Skill editing.

Independent architecture review `g004_preflight_arch_review`: APPROVE / CLEAR. Workspace owns current package content/bindings; Market owns discovery. Complete content exists before package/binding publication. Reader-safe cleanup and detached Audit metadata are explicit acceptance requirements. S2 product-owner records remain schema-only, with core Runtime and product entry E2E assigned to G005/G006.

The reviewed schema preflight checks existing Run and Credential composite identity keys, same-Agent MCP and personal-connection constraints, Session input/reply ordering, Goal iteration links and separate result/delivery facts. The three G004 owners and six schema-only owners are covered; existing Model/Credential foundation storage remains in force outside the declared amendments.

This record approves implementation scope and public ownership, not running code, final column completeness or external compatibility. Real PostgreSQL schema/integration, adapter behavior tests, cumulative gates and independent implementation review remain required. Original G003 contracts and receipts stay unchanged; amended bindings append receipts to that history.
