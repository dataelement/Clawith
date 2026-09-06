# Agent Note: Clean-Break Backend Source Disposition

Status: proposed — future capability reuse and owner rewrites remain proposals; the complete G002 legacy source and direct-dependency disposition is implemented, target application composition and database infrastructure are in place, and retained provider, conversion, Sandbox, email, and object-storage mechanics have no current product entry until a reviewed owner adopts them

## Problem

The current Backend contains the product capabilities that the target must account for, but its implementation joins Agent identity, LangGraph execution, checkpoints, Commands, Tool execution ledgers, product reconciliation, relationship labels and access metadata, quotas, approvals, compatibility paths, and channel delivery across the same models and services. The `backend/app/services/agent_runtime/` package alone contains about sixty Python files and thirty-four thousand lines. Incrementally reshaping those authorities would preserve the exact lifecycle and compatibility structures the target architecture removes.

The rewrite must not lose supported product capabilities merely because their current owner is wrong. It also must not retain an obsolete model, route, test, dependency, migration, or adapter merely because some useful behavior currently passes through it. This Note classifies current source by capability and disposition; the target architecture Notes remain the authority for replacement behavior.

## Proposal

### Classification

Each current source area receives one disposition:

- `delete`: the capability or compatibility behavior is absent from the accepted target and is not ported.
- `rewrite`: the product capability remains, but its current authority, persistence, API, or lifecycle is replaced.
- `reuse`: a bounded provider, transport, conversion, storage, or pure helper implementation may move behind a new owner after its imports and behavior are verified.
- `defer`: the product capability and its later contract, implementation, and test obligations remain in scope for the complete Backend rewrite, but they do not block the foundational Agent Runtime slice. Deferral does not preserve old source: Phase 0's 401/401 `disposition_approved` coverage rows collectively authorize G002 to delete the classified old authorities before their target contracts or implementations exist. That deletion does not cancel the capability, authorize target contract choices, or create compatibility.

No current ORM model, API response, internal service contract, migration, or test is automatically compatible with the target. Reuse is code-level implementation reuse, never authority reuse.

### Current target cutover state

The target branch no longer contains the legacy Agent execution authority. The removed source manifest is:

- `backend/app/services/agent_runtime/**`
- `backend/app/models/agent_run.py`
- `backend/app/models/agent_run_command.py`
- `backend/app/models/agent_run_event.py`
- `backend/app/models/agent_tool_execution.py`
- `backend/app/models/session_context_state.py`
- `backend/app/scripts/setup_langgraph_checkpoints.py`
- `backend/tests/test_agent_runtime_*.py`
- `backend/tests/test_setup_langgraph_checkpoints.py`
- the dedicated old-authority tests `test_runtime_schema.py`, `test_session_context_service.py`, `test_tool_exchange.py`, `test_tool_execution.py`, `test_model_capabilities.py`, `test_runtime_model_settings_resolution.py`, `test_chat_session_runtime_state.py`, `test_unified_runtime_group_migration.py`, and `test_websocket_runtime_chat.py`

`backend/app/runtime/` remains the target Runner/Loop implementation boundary. The category sections below record the staged deletion boundaries leading to G002, not a requirement to retain their intermediate consumers. The completed target state is summarized by this Note's status and the migration/composition/dependency section. Historical references to then-pending consumers do not authorize restoring removed sources.

The target `app.dao` boundary is an empty static namespace: `app/dao/__init__.py` remains zero-byte, and the directory contains no Python modules, repositories, exports, or dynamic package hooks. Each target owner keeps its ORM models and repositories private under `app/modules/<owner>/`; cross-owner consumers use typed public services. One shared guard enforces the empty initializer and absence of the retired generic DAO modules, while category guards independently reject their exact deleted identities, definitions, and export names.

The target branch also no longer contains the old Context authority:

- `backend/app/services/agent_context.py`
- `backend/tests/test_agent_context.py`

The structured Experience authority is also removed:

- `backend/app/api/experience.py`
- `backend/app/models/experience.py`
- `backend/app/models/experience_reference.py`
- `backend/app/services/experience_retrieval.py`
- the dedicated Experience API, citation/RAG, and revision-migration tests

The old Model and LLM execution authority is removed as a separate category:

- `backend/app/models/llm.py`
- the entire `backend/app/services/llm/` package, including Model resolution,
  fallback, the monolithic caller, finish protocol, single-step execution,
  Provider clients, and multimodal request assembly
- the dedicated Model persistence and tenant-scope, runtime Model settings,
  resolution, fallback, finish, single-step, Provider request-shape, capability
  probe, and multimodal tests

The Persistent Task authority is also removed as a separate category:

- `backend/app/models/task.py`
- `backend/app/api/tasks.py`
- `backend/app/services/task_executor.py`
- the dedicated Task CRUD/intake and execution tests

This removal does not remove or implement the target Task Tool. In the accepted target, delegated work is represented by the parent Tool Call, Child Run Input, and Child Run outcome rather than a separate Task or TaskLog lifecycle object.

The old Tool authority is also removed as a separate category:

- `backend/app/models/tool.py`
- `backend/app/api/tools.py`
- `backend/app/services/agent_tools.py`
- `backend/app/services/builtin_tool_definitions.py`
- `backend/app/services/tool_config.py`
- the already-absent `backend/app/services/tool_exchange.py` import identity
- `backend/app/services/tool_seeder.py`
- every `backend/tests/test_agent_tools_*.py` file present at cutover: `agentbay_a0`, `deadlines`, `deploy_contracts`, `email_contracts`, `feishu_f0_contracts`, `legacy_contract_compatibility`, `okr_contracts`, `remaining_typed_outcomes`, `storage_workspace`, `tool_config_logging`, `typed_agentbay_reads`, `typed_bitable`, `typed_content_outcomes`, `typed_deploy_reads`, `typed_deploy_simple_writes`, `typed_dynamic_mcp`, `typed_e2b_outcome`, `typed_email_read`, `typed_email_write`, `typed_feishu_approval`, `typed_feishu_calendar`, `typed_feishu_doc_drive`, `typed_feishu_remaining`, `typed_feishu_wiki`, `typed_image_outcomes_v2`, `typed_okr_jobs`, `typed_okr_transactions`, `typed_search_outcomes`, and `typed_vercel_deploy`
- the dedicated old Tool contract files `test_builtin_tool_contracts.py`, `test_custom_image_tool.py`, `test_deploy_tools.py`, `test_human_send_tools.py`, `test_query_directory_tool.py`, `test_roster_human_resolver.py`, `test_tool_tenant_scope.py`, and `test_tools_category_config.py`
- the mixed legacy files `test_feishu_channel_runtime.py`, `test_mcp_oauth_authorization.py`, `test_sandbox_execution_policy.py`, `test_trigger_config_updates.py`, and `test_workspace_reconciliation.py`
- `test_smithery_recovery_does_not_store_auth_required_connection` from `test_mcp_recovery.py`

These deleted tests instantiated `Tool`/`AgentTool`, called the old Tool management API, asserted the monolithic builtin definition and seeding catalogs, or executed and patched the `agent_tools` exposure/dispatch/configuration facade. The user explicitly approved deleting all legacy `agent_tools`-era tests, including mixed files and assertions that directly exercised retained MCP, Feishu, Sandbox, AgentBay, Trigger, or Workspace helpers through the old authority. No old test is extracted, moved, or adapted during this deletion. Each retained owner must receive new target-contract tests when it is implemented. The independent MCP transport error test remains in `test_mcp_recovery.py` because it imports and exercises only `MCPClient`.

This removal does not implement the target `modules/tool` owner or Capability Market. `mcp_client.py`, MCP OAuth helpers, isolated provider transports, migrations, and dependency declarations remain staged candidates. The later Channel category removes Atlassian and Channel authority, and the later resource-discovery category removes their mixed discovery consumer rather than repairing it; neither state authorizes compatibility. `tool_exchange.py` had already left the target tree with the old Agent Runtime cutover and is not recreated.

The old Skill authority is also removed as a separate category:

- `backend/app/models/skill.py`
- `backend/app/api/skills.py`
- `backend/app/services/skill_seeder.py`
- `backend/app/services/skill_creator_content.py`
- the complete generated and evaluation asset directory `backend/app/services/skill_creator_files/`
- `backend/tests/test_skill_seeder_sync.py`
- `backend/tests/test_skills_api.py`

The deleted tests asserted the old global/tenant Skill ORM, CRUD and direct file mutation API, default-Skill database seeding and repair, and import compatibility. They are not moved or adapted during deletion. The future Workspace and Capability Market owners must write fresh tests from their approved target contracts, including controlled Market/Admin installation and Workspace Skill package behavior.

This removal does not implement Capability Market or remove independently owned capability/resource discovery, MCP transport, Workspace/file/storage behavior, target Tool modules, provider/Channel adapters, templates, migrations, or dependencies. Remaining model import lists and product consumers stay staged for their own minimum owner/category deletions; none authorizes recreating the old Skill identities. Agent-authored creation, evaluation assets, direct database-backed file mutation, and the old Skill import/install facade are gone.

The dedicated OpenClaw/Gateway authority is also removed as a separate category:

- `backend/app/api/gateway.py`
- `backend/app/models/gateway_message.py`
- `backend/app/services/agent_manager.py`
- `backend/tests/test_gateway_runtime_a2a.py`
- `backend/tests/test_agent_manager_soul.py`

The Gateway API, queued remote-message model, API-key polling/report/heartbeat/send-message protocol, OpenClaw container lifecycle, and the combined legacy Agent file-initialization manager no longer exist as importable target authorities. The two deleted tests asserted the retired Gateway protocol and behavior embedded in that combined manager; they are not moved or adapted during deletion. The target Agent, Workspace, Session, A2A, and Channel owners must write fresh tests from their approved contracts.

This minimum deletion deliberately leaves mixed residual branches for their own owner/category commits: OpenClaw fields and API-key/container routes in `models/agent.py` and `api/agents.py`; Gateway queueing in `api/websocket.py`; file initialization calls in `api/onboarding.py` and `services/agent_seeder.py`; Gateway model imports in cleanup/backfill scripts; and mixed storage/API tests that still import `app.services.agent_manager`. Those dangling consumers do not authorize restoring `app.api.gateway`, `app.models.gateway_message`, or `app.services.agent_manager`. Discord's independently owned connection mode and generic Sandbox publication-owner terminology are not classified as OpenClaw authority by this removal.

The old Agent Credential authority is also removed as a separate category:

- `backend/app/models/agent_credential.py`
- `backend/app/dao/agent_credential_dao.py`
- `backend/app/api/agent_credentials.py`
- `backend/app/schemas/agent_credential.py`
- the `agent_credential_dao` compatibility export from `backend/app/dao/__init__.py`

These modules owned the Agent-scoped cookie record, direct DAO, CRUD transport, encryption-on-write behavior, and legacy request/response shapes. They are deleted rather than migrated. No dedicated legacy Credential tests remain in the target tree, and no legacy test is extracted or adapted during this deletion. The target Credential owner must write fresh model, persistence, authorization, Secret-handling, and transport tests from its approved contract.

The later AgentBay and Channel categories below remove the control, cookie-injection, Channel configuration, and Atlassian consumers that remained at the Credential deletion boundary. Tenant cleanup still names the old table, legacy Alembic revisions still create and alter it until the target baseline replaces the full chain, and other identity-provider, Agent, Tool/MCP, and Provider Secret residuals remain staged. Those residuals do not authorize recreating `app.api.agent_credentials`, `app.dao.agent_credential_dao`, `app.models.agent_credential`, or `app.schemas.agent_credential`.

The overloaded old Agent aggregate authority is also removed as a separate category:

- `backend/app/models/agent.py`
- `backend/app/api/agents.py`
- `backend/app/dao/agent_dao.py`
- `backend/app/dao/agent_access_dao.py`
- `backend/app/services/agent_seeder.py`
- the `agent_dao` and `agent_access_dao` compatibility exports from `backend/app/dao/__init__.py`
- `backend/tests/test_agent_delete_api.py`
- `backend/tests/test_agent_model_step_limit.py`
- `backend/tests/test_agent_permission_candidates.py`
- `backend/tests/test_agent_seeder_storage_repair.py`
- `backend/tests/test_agent_visibility.py`
- `backend/tests/test_timezone_validation.py`

These sources combined Agent identity and CRUD with creator ownership, access modes, visibility and management grants, permission candidates, soft deletion, execution/container status, start/stop and API-key operations, OpenClaw fields, runtime and quota counters, template bootstrap, default-Agent seeding and storage repair, and relationships to Runtime, Task, Channel, Model, and User state. The deleted tests asserted only those retired aggregate contracts, including the old `AgentUpdate` Tool-round limit and timezone fields and the removed Agent detail API's effective-timezone fallback. The Tool-round limit contradicts the accepted target contract, which has no maximum Model Step, model-turn, or renamed Tool-round counter. These tests are not moved or adapted; the target Agent, Model System, and Permission owners must write fresh tests from their approved contracts when implemented.

`AgentPermission`, `AgentTemplate`, and `AgentUserOnboarding` were physically declared in the removed `models/agent.py`, but they are not accepted as facts owned by the target Agent aggregate. Permission grants, Agent Template, and Onboarding must be reimplemented by their separate target owners only after those owner contracts are reviewed and approved, with new persistence and service tests. Directory, Metrics, Onboarding, Identity, Organization, Workspace, object-storage infrastructure, mixed `schemas.py`, migrations, dependency declarations, Frontend, and target module packages remain staged for their own minimum commits. Their dangling imports and relationships are evidence of incomplete source disposition, not authorization to recreate the removed aggregate or add a compatibility shim.

The old Identity/Tenant aggregate authority is also removed as a separate category:

- `backend/app/models/user.py`
- `backend/app/models/tenant.py`
- `backend/app/models/tenant_setting.py`
- `backend/app/api/users.py`
- `backend/app/api/tenants.py`
- `backend/app/dao/identity_dao.py`
- `backend/app/dao/user_dao.py`
- `backend/app/dao/tenant_dao.py`
- the `identity_dao`, `user_dao`, and `tenant_dao` compatibility exports from `backend/app/dao/__init__.py`
- the old Tenant model/API validation assertions formerly removed from the later-deleted mixed `backend/tests/test_timezone_validation.py`

These sources combined a global login Identity, tenant-scoped User membership, Tenant configuration and sparse Tenant settings with CRUD, tenant switching and assignment, self-create and join, quota counters and limits, logo storage, registration configuration, SSO-domain lookup, Tenant deletion, and compatibility association proxies. The removed tests asserted only retired Tenant persistence or API schemas. They are not adapted; the target `identity_tenant` owner must write fresh Account, Membership, Tenant, Tenant Principal, and Platform Principal tests from its approved contract.

The Identity/Tenant deletion deliberately preserved the then-staged Auth routes and services together with SSO and identity-provider models and services, Organization, Invitation, Onboarding, Permission core, AgentBay, Channel, migrations, dependency declarations, Frontend, and target module packages. The old Auth authority is removed in the following category; remaining consumers that still import deleted model or DAO identities are staged for their own owner/category commits. Their dangling imports are evidence of incomplete source disposition, not authorization to recreate an old aggregate, package export, or compatibility shim.

The old Auth authority is also removed as a separate category:

- `backend/app/api/auth.py`
- `backend/app/services/auth_provider.py`
- `backend/app/services/auth_registry.py`
- `backend/app/services/registration_service.py`
- `backend/app/services/password_reset_service.py`
- `backend/app/services/email_verification_service.py`
- `backend/tests/test_auth.py`
- the old Auth Provider assertions removed from `backend/tests/test_auth_provider.py`
- the password reset and Auth API assertions removed from `backend/tests/test_password_reset_and_notifications.py`

These sources combined password login and registration, Account binding, tenant switching, JWT issuance, password change and reset, email verification, SSO callback/session orchestration, Provider construction, and cross-owner Identity/Tenant, Organization, Invitation, Onboarding, and notification mutations. Their tests asserted that retired orchestration and are deleted rather than adapted. The target Auth owner must receive fresh password, login, token, bind, reset, and verification tests after its approved contract is implemented.

Generic system-email transport tests remained after the Auth deletion until the later Enterprise and System Email disposition removed their final consumer and dedicated test; the Notification deletion removed only broadcast-notification assertions. The Auth deletion preserved the then-independent SSO and IdentityProvider authority so it could be removed in its own minimum commit. The later Sandbox decoupling removes `core/security.py` after its final data-decryption consumer receives an explicit decoder boundary.

Google Workspace Organization sync, Organization, relationship, Plaza, and cleanup-script sources still import one or more deleted Auth identities and remain staged for their own owner/category commits. The later Channel and OKR categories remove their old consumers rather than repairing them. The remaining dangling imports do not authorize recreating the old Auth API, Provider registry, registration orchestrator, password-reset lifecycle, email-verification lifecycle, or package exports.

The old SSO authority and its mixed identity-provider entry surfaces are also removed as a separate category:

- `backend/app/api/sso.py`
- `backend/app/api/google_workspace.py`
- `backend/app/models/identity.py`
- `backend/app/dao/identity_provider_dao.py`
- `backend/app/services/sso_service.py`
- `backend/app/services/sso_session_security.py`
- `backend/app/services/identity_provider_lookup.py`
- `backend/app/services/google_workspace_oauth.py`
- the `identity_provider_dao` compatibility export from `backend/app/dao/__init__.py`
- `backend/tests/test_identity_provider_and_google_workspace_oauth.py`
- `backend/tests/test_sso_session_browser_binding.py`
- `backend/tests/test_identity_id_mapping.py`
- `backend/tests/test_sso_toggle.py`

These sources combined SSO login and browser-session binding with IdentityProvider persistence and selection, Channel identity mapping, Tenant-domain resolution, platform SSO settings, and Google Workspace Organization administration and directory synchronization. `backend/app/api/google_workspace.py`, `backend/app/services/google_workspace_oauth.py`, and their deleted tests were mixed legacy entry surfaces: they joined Google Workspace administrator authorize URLs, OAuth state and callbacks, directory probe/proxy/sync behavior, and SSO browser-session completion. Their presence in this deletion does not assign those Organization capabilities to the target SSO owner. All listed legacy tests are deleted rather than adapted.

Fresh tests follow the target owner contracts: SSO owns provider login, provider selection and binding-policy tests using Auth-issued login sessions; Organization owns Google Workspace administrator authorize URL, OAuth state and callback, directory probe, proxy, and synchronization tests; Channel owns external identity mapping tests; `identity_tenant` owns Tenant-domain resolution tests; and `platform_administration` owns platform SSO settings and toggle tests. Each owner writes those tests only after its approved contract is implemented.

This minimum deletion preserves Organization sync adapters and services, Invitation, Onboarding, generic email, `core/security.py`, migrations, dependencies, Frontend, and the empty target `modules/sso` package. The later Channel category removes provider-specific Channel APIs rather than repairing their deleted IdentityProvider imports. Other retained sources may still import deleted SSO identities, and the Organization adapter may still import the deleted Google Workspace OAuth proxy constant. These dangling consumers are staged evidence for their own owner/category commits; they do not authorize restoring the old SSO API, model, DAO, services, Google Workspace OAuth entrypoint, or compatibility export.

`backend/tests/architecture/test_deleted_authorities.py` makes every removed Python import identity absent as both a module file and a same-named package directory. Its negative fixtures prove that recreating either form fails the target guard. The generated Skill creator-files directory is independently guarded as a forbidden path; deleted DAO, Auth, SSO, Organization/Relationship, or Invitation exports cannot return through static or dynamic re-exports; and ordinary Backend tests cannot statically import identities covered by their category test-reference guards. Dotted string references are rejected only by category helpers that explicitly call the shared dotted-reference guard with their deleted identities; each such helper has category-owned negative and unrelated-reference fixtures. This executable helper coverage is authoritative, so adding a guarded category does not require a separate prose enumeration. Surviving legacy callers remain staged evidence for their own deletion category; they do not justify compatibility modules, fallback Context assembly, Experience projections, an old Model execution facade, Persistent Task persistence, OpenClaw/Gateway authority, old Agent Credential authority, the overloaded old Agent aggregate, the old Identity/Tenant aggregate, old Auth orchestration, old SSO authority, the overloaded old Organization/Relationship aggregate, old Invitation persistence, Plaza persistence or transport, AgentBay control or Session-registry authority, or the legacy Tenant Knowledge publication adapter.

The overloaded legacy Organization/Relationship aggregate is removed as its own minimum category:

- `backend/app/models/org.py`
- `backend/app/api/organization.py`
- `backend/app/api/relationships.py`
- `backend/app/dao/org_member_dao.py`
- `backend/app/services/org_sync_adapter.py`
- `backend/app/services/org_sync_service.py`
- `backend/app/services/access_relationships.py`
- the `org_member_dao` compatibility export from `backend/app/dao/__init__.py`
- `backend/tests/test_org_sync_adapter.py`
- `backend/tests/test_organization_tenant_scope.py`

These sources made `OrgDepartment`, `OrgMember`, `AgentRelationship`, and `AgentAgentRelationship` one shared authority for provider directory synchronization, Tenant membership administration, relationship labels and creator-management rules, and access metadata. The relationship Workspace regeneration hook was already a no-op compatibility concept; this deletion removes that compatibility surface and does not remove a live Workspace projection. These legacy facts do not remain as a compatibility aggregate, and their dedicated tests are deleted rather than adapted.

The Phase 0 disposition is approved for deleting this legacy aggregate. The target owner assignments are disposition-approved, but their owner contracts remain unreviewed; this deletion does not authorize implementation. After those contracts are reviewed and approved, `identity_tenant` rewrites Tenant membership facts and membership tests, while Auth/Account owns global login fields and their mutation tests. Organization owns departments, external-directory facts, provider synchronization orchestration, and their tests. Permission owns explicit Membership/Agent visibility grants, access resolution, and denial-path tests without relationship labels or creator-management metadata. Directory composes those owners only through their public services and tests that composition. Workspace tests only its own mutation boundary and does not receive a relationship projection. No target owner may restore `OrgDepartment`, `OrgMember`, `AgentRelationship`, `AgentAgentRelationship`, or `org_member_dao` as a shared legacy persistence contract.

The Organization/Relationship category deliberately excludes Enterprise Info persistence and API ownership, Invitation codes, the Directory API and service, Participant and Group, Onboarding, Channel, templates, migrations, dependency declarations, Frontend, and the empty target `modules/organization`, `modules/permission`, `modules/directory`, and `modules/okr` packages. Retained Directory, Onboarding, Permission-core, and maintenance-script sources still import one or more deleted Organization/Relationship identities; the later Group, Channel, and OKR categories remove their old consumers rather than repairing them. Those dangling imports are staged evidence for later owner/category commits and are not repaired here; they do not authorize a compatibility module, DAO export, implicit relationship lookup, or relationship Workspace regeneration.

The deleted-authority guard now covers every removed Organization/Relationship module and same-named package representation, restoration of the exact `org_member_dao` package export under the static `app.dao` policy, and ordinary Backend test imports of any deleted identity. After contract review and approval, `identity_tenant`, Auth/Account, Organization, Permission, Directory, and Workspace must write their own boundary tests rather than importing or renaming these legacy tests.

The legacy Invitation persistence authority is removed as its own minimum category:

- `backend/app/models/invitation_code.py`
- `backend/app/dao/invitation_code_dao.py`
- the `invitation_code_dao` compatibility export from `backend/app/dao/__init__.py`

These sources made `InvitationCode` and its active-code lookup the shared persistence contract for registration gating, Tenant invitation-code administration, platform company creation, batch user invitation, listing, CSV export, and deactivation. The Phase 0 disposition preserves Invitation as a product capability but assigns its replacement to the separate target `invitation` owner; that owner contract remains unreviewed, so this deletion does not authorize implementation or preservation of the old table contract.

No dedicated Backend test protected the old InvitationCode persistence, invitation-code CRUD/export, or invite-user persistence flow. `backend/tests/test_enterprise_invites.py` tested only the mixed Enterprise transport's System Email enabled/disabled preflight and is deleted with that transport rather than adapted. After contract review and approval, the target Invitation owner must receive fresh persistence, lifecycle, Tenant-scope, limit, registration-consumption, and delivery-boundary tests; generic email configuration remains owned and tested separately.

This minimum deletion preserves Onboarding, generic email provider mechanics, migrations, dependency declarations, Frontend, and the empty target `modules/invitation` package. Their surviving imports of `app.models.invitation_code` are deliberate staged evidence for later owner/category commits and are not repaired here. They do not authorize restoring the old model, DAO, DAO export, table contract, or compatibility shim.

The deleted-authority guard covers both removed Invitation import identities as module and same-named package forms, restoration of the exact `invitation_code_dao` package export under the static `app.dao` policy, and ordinary Backend test imports. Fresh target Invitation tests must exercise the new owner contract rather than rename the later-deleted System Email preflight test or restore an old fixture.

The legacy Onboarding authority is removed as its own minimum category:

- `backend/app/models/onboarding.py`
- `backend/app/api/onboarding.py`
- `backend/app/services/onboarding.py`
- `backend/tests/test_onboarding.py`

These sources combined two obsolete facts: `UserTenantOnboarding` tracked company-entry progress and personal-assistant creation, while the service tracked per-user Agent greeting and calibration phases through the already deleted `AgentUserOnboarding` fact. The API also coupled Onboarding completion to old Agent creation, relationship projection, Agent-file initialization, and container startup. The dedicated test file protected only those old prompts, phase transitions, bootstrap-field absence, and file/focus finalization instructions, so it is deleted instead of carried into the target.

Onboarding remains an S3 product capability, but its owner contract remains unreviewed and this deletion does not authorize target implementation or preservation of either old state machine. After contract review and approval, the target `onboarding` owner receives fresh Tenant-scoped lifecycle, idempotency, completion, and Agent-creation orchestration tests. The later S3-wave Agent and Workspace owners receive their own fresh boundary tests; Onboarding tests must consume those public boundaries rather than restore direct Agent-file or container control.

Agent Template is independently owned by `agent_template`, not by Onboarding. Its old DAO and seeder are removed under the Agent Template category below rather than preserved as part of the deleted Onboarding phases. Generic Auth and Email, Directory, Workspace, migrations, dependencies, and Frontend remain staged; the later Channel category removes its old Onboarding consumers. Surviving imports or references are deliberate source-disposition evidence for later minimum owner/category commits and are not repaired here; they do not authorize restoring `app.models.onboarding`, `app.api.onboarding`, `app.services.onboarding`, or the deleted tests.

The deleted-authority guard makes all three old Onboarding import identities absent as modules and same-named packages, with negative fixtures for both representations and ordinary Backend-test imports. Fresh target tests must use the approved S3 owner contracts rather than rename the old prompt, phase, bootstrap, or file-initialization fixtures.

The legacy Directory authority is removed as its own minimum category:

- `backend/app/api/directory.py`
- `backend/app/services/agent_directory.py`
- `backend/tests/test_agent_directory_api.py`

These sources joined a read-only human/Agent roster query with Custom Directory maintenance. The API directly queried and mutated the deleted Organization/Relationship aggregate and old Agent Permission persistence, while the service directly combined Agent, Permission, Organization, IdentityProvider, ChatSession, and Channel-contact readiness facts. Its sole dedicated test imported the deleted API directly and protected only old route shapes, Organization-backed candidate SQL, roster filtering, and error translation, so it is deleted instead of carried into the target.

Directory remains an S3 composition owner, but its owner contract remains unreviewed and this deletion does not authorize a replacement implementation or preservation of the old route and payload contracts. After contract review and approval, the target `directory` owner receives fresh composition tests over public Identity/Tenant, Organization, Permission, Agent, Group/Participant, and Channel contracts. Those tests must cover bounded search, Tenant isolation, visibility, contactability, and unavailable-target behavior without restoring direct imports of private persistence models.

There was no separate Directory DAO, helper module, package export, or production router registration to delete. The empty `backend/app/modules/directory` target-owner package remains. Permission core, mixed query DAOs, migrations, dependencies, and Frontend remain staged; the later Group and Channel categories remove `participant_identity.py`, `channel_user_service.py`, and the old identity-mapping and delivery paths. Surviving Directory wording or dangling imports are evidence for later owner/category commits and do not authorize restoring `app.api.directory`, `app.services.agent_directory`, or the deleted tests.

The deleted-authority guard makes both old Directory import identities absent as modules and same-named packages, prevents static or dynamic API/service package re-exports, and rejects ordinary Backend-test imports. Fresh S3 tests must exercise the approved Directory public composition rather than rename the old API fixture or couple to Group, Channel, Permission, Organization, or Agent persistence.

The legacy Focus authority is removed as its own minimum category:

- `backend/app/models/focus.py`
- `backend/app/dao/focus_dao.py` and its `app.dao` package export
- `backend/app/api/focus.py`
- `backend/app/services/focus_service.py`
- `backend/tests/test_focus_service.py`

These sources made database-backed `AgentFocusItem` rows, legacy `focus.md` migration, item upsert/completion, model-context rendering, and the Agent-scoped Focus HTTP routes one coupled authority. The sole dedicated test imported the deleted service and protected only its legacy migration and DAO orchestration, so it is deleted instead of adapted.

Focus remains a later S3 product owner, but its owner and product contracts remain unreviewed. After both contracts are reviewed and approved, the target `focus` owner must receive fresh persistence, Tenant and Agent scope, bounded list, upsert, completion, authorization, migration-disposition, API, and model-context tests. The old service test is not renamed or used to infer the target contract.

This minimum deletion preserves activity persistence and observability services, the target Trigger package and later Trigger product obligations, retained conversion and object-storage tests that mention `focus.md`, migrations, dependencies, Frontend, and the empty target `modules/focus` and `modules/okr` packages. The later OKR category removes the old OKR consumer rather than repairing its Focus imports. Other surviving imports of the deleted Focus service or model are deliberate staged evidence for later owner/category commits and do not authorize restoring the old model, DAO, DAO export, API, service, file-migration path, or compatibility shim.

The deleted-authority guard makes all four old Focus import identities absent as modules and same-named packages, prevents restoration of the exact `focus_dao` package export under the static `app.dao` policy, and rejects ordinary Backend-test imports. Fresh S3 Focus tests must exercise the approved target owner rather than preserve the legacy database/file hybrid.

The legacy Notification authority is removed as its own minimum category:

- `backend/app/models/notification.py`
- `backend/app/api/notification.py`
- `backend/app/services/notification_service.py`
- the Notification broadcast assertions removed from the former mixed `backend/tests/test_system_email_and_notifications.py`

These sources made one `Notification` table and service the shared authority for human and Agent inbox persistence, unread counts, read state, Tenant broadcast fan-out, approval/autonomy notices, Plaza mentions and comments, Heartbeat draining, and OKR oneshot-failure reporting. The HTTP layer also coupled in-app broadcast persistence to generic System Email delivery. Those legacy persistence and delivery contracts are deleted rather than adapted.

Notification remains an S3 product owner, but its owner and product contracts remain unreviewed. After both contracts are reviewed and approved, the target `notification` owner must receive fresh persistence, Tenant and recipient scope, unread/read lifecycle, bounded listing, authorization, post-commit publication, and delivery-outcome tests. Approval-driven behavior is not restored through Notification: the clean-break target removes the old Approval Request and L1/L2/L3 autonomy contract.

The former `BroadcastEmailRecipient` DTO, `deliver_broadcast_emails` helper, and per-recipient broadcast test were part of the deleted Notification broadcast path and are removed rather than retained as generic email authority. The later Session, Group, and Channel categories remove chat messages, group realtime publication, and Channel delivery; the later System Email category removes its database-backed product transport while retaining explicit SMTP mechanics in `core/email.py` and `email_service.py`. Activity persistence, observability services, enterprise notification-bar settings, migrations, dependencies, Frontend, and the empty target `modules/notification` package remain staged.

Cleanup-script and other retained production consumers still import the deleted Notification model or service. The later OKR category removes its old consumer rather than repairing it. Those dangling imports are deliberate source-disposition evidence for later owner/category commits and do not authorize restoring the table, API, service, inbox, broadcast, approval-notice path, or a compatibility shim. The former mixed Autonomy test is removed with the old Autonomy/Approval protocol.

The deleted-authority guard makes all three legacy Notification import identities absent as modules and same-named packages and rejects ordinary Backend-test imports and dotted dynamic string references, including monkeypatch and dynamic-import targets. Fresh S3 tests must exercise the approved Notification owner and its explicit consumers rather than rename the removed broadcast assertions or preserve old approval persistence.

The old Autonomy/Approval protocol is removed as one behavior-chain category:

- `backend/app/services/autonomy_service.py`
- the `ApprovalRequest` ORM and its `approval_requests` table and `approval_status_enum` declarations from the mixed `backend/app/models/audit.py`
- `GET /enterprise/approvals`, `POST /enterprise/approvals/{approval_id}/resolve`, and the approval count from the mixed `backend/app/api/enterprise.py`
- `default_autonomy_policy` template API fields and approval metrics from the mixed `backend/app/api/advanced.py`
- `ApprovalRequest` metric queries and result fields from `backend/app/dao/agent_metrics_dao.py`
- Agent `autonomy_policy`, `ApprovalRequestOut`, and `ApprovalAction` transport shapes from the mixed `backend/app/schemas/schemas.py`
- the Runtime-specific `FeishuService.send_approval_card` notification helper from the retained generic Feishu transport
- all `default_autonomy_policy` L1/L2/L3 blocks from the twenty-two `backend/agent_templates/*/meta.yaml` files present at cutover
- `backend/tests/test_autonomy_service_runtime_delete.py`

These sources implemented one old protocol: an Agent action resolved an L1/L2/L3 autonomy level, L3 persisted an Approval Request, a human resolve call directly executed the action or resumed the exact waiting Run, and Notification or Feishu could publish approval notices. The target first release has no autonomy levels, Approval Request persistence, approval API, approval-driven Waiting/Resume, or template default for that policy. The dedicated test protected only this retired protocol, so it is deleted rather than adapted.

This deletion preserves the native Feishu `create_approval_instance`, `query_approval_instances`, and `get_approval_instance` transport methods, Permission and Need Input boundaries, architecture-artifact approval tests, migrations, dependencies, and every non-autonomy Agent Template field. The later Observability/Audit persistence category removes `AuditLog`, `EnterpriseInfo`, Activity persistence, and their DAOs rather than treating them as target facts. The deleted `send_approval_card` helper was specific to the retired Runtime protocol and had no remaining producer. This change does not implement a Permission approval workflow or change Need Input. If approval is added later, Permission must own its policy, persistence, approver selection, and coordinated Run behavior under a separately approved contract.

Frontend `autonomy_policy`, approval tab, Enterprise pending-approval count, and related parser consumers remain staged for the approved full Frontend rewrite; they are not compatibility contracts and this Backend deletion does not edit them. The old Alembic chain also remains unchanged until the single clean-break baseline replaces all legacy tables and enums together.

The deleted-authority guard makes `app.services.autonomy_service` absent as a module and same-named package, rejects ordinary Backend-test static imports and dotted dynamic references, and structurally rejects the exact deleted classes, imports, fields, dictionary and lookup keys, route decorators, functions, response references, table and enum identifiers, and `send_approval_card` method in the six retained mixed Python owners. It does not treat arbitrary local variables or prose containing `approvals` as protocol restoration. The guard parses every Agent Template metadata file as a valid top-level YAML mapping and rejects the deleted policy key whether quoted or unquoted. Positive fixtures preserve native Feishu approval-instance transport, Audit, Enterprise activity, metrics, schemas, and Agent Templates without autonomy policy.

The legacy Published Page authority is removed as its own minimum category:

- `backend/app/models/published_page.py`
- `backend/app/api/pages.py`

The model stored a public short identifier, Agent, User and Tenant ownership fields, a Workspace-relative source path, title, view counter, and creation time in `published_pages`. The API served stored HTML without authentication at `/p/{short_id}`, incremented its view count, applied sandbox and content-type response headers, and exposed an authenticated Agent-scoped list. These old persistence and transport contracts are deleted rather than adapted. No dedicated Backend test imported or exercised them at cutover, and the target application composition did not mount either router.

Published Page remains an S3 product owner, but its owner and product contracts remain unreviewed. After both contracts are reviewed and approved, the target `published_page` owner must receive fresh tests for the approved persistence, Tenant and Agent scope, authorization, bounded listing, publication source, public rendering, view accounting, content isolation, missing-source, and deletion contracts. The accepted contract, not the old route or table shape, decides whether rendering reads a Workspace snapshot or another owned artifact.

This minimum deletion preserves the old `published_pages` Alembic revision until the clean-break baseline replaces the full migration chain, isolated local and S3 object-storage mechanics, Workspace files, HTTP composition, dependencies, Frontend, and the empty target `modules/published_page` package. These staged surfaces do not authorize restoring the old model, API, table contract, route payloads, direct storage access, or a compatibility shim. There was no API or model package export, dynamic registration, or mounted route to remove.

The deleted-authority guard makes both legacy Published Page import identities absent as modules and same-named packages and rejects ordinary Backend-test imports and dotted dynamic string references. Fresh S3 tests must exercise the approved Published Page owner and its public contracts rather than recreate the old unauthenticated renderer or Agent-scoped list as fixtures.

The legacy Plaza authority is removed as its own minimum category:

- `backend/app/models/plaza.py`
- `backend/app/api/plaza.py`

The model owned the `plaza_posts`, `plaza_comments`, and `plaza_likes` tables, including author snapshots, optional Tenant scope, denormalized counters, and post-comment cascading. The API directly joined the deleted Agent, Identity/Tenant, Auth, and Notification authorities to list and retrieve posts, calculate feed statistics, create and delete posts, create comments, send mention/comment notifications, and toggle likes while checking for an existing like. It also embedded company-visible Agent policy and platform-admin Tenant override behavior in the transport layer. These old persistence, authorization, visibility, social-interaction, and notification contracts are deleted rather than adapted. No Plaza-owned Backend test existed at cutover, and the target application composition did not mount the legacy router.

Plaza remains an S3 product owner, but its owner and product contracts remain unreviewed. After both contracts are reviewed and approved, the target `plaza` owner must receive fresh persistence, Tenant and author scope, bounded feed, authorization, Agent visibility, post/comment/like lifecycle, counter consistency, notification outcome, and API composition tests. The deleted Heartbeat assertions that mentioned retired `plaza_*` Tool names do not define the future Plaza Tool contract.

This minimum deletion preserves the legacy Alembic chain, the target Heartbeat package and later Heartbeat product obligations, generic query infrastructure, dependencies, Frontend, and the empty target `modules/plaza` and `modules/okr` packages. The later OKR category removes its old Plaza consumer rather than repairing it. The Alembic chain contains no dedicated Plaza or Plaza-table revision. Those dangling consumers do not authorize restoring the old model, API, tables, route payloads, social Tool names, notification coupling, or a compatibility shim.

The deleted-authority guard makes both legacy Plaza import identities absent as modules and same-named packages and rejects ordinary Backend-test imports and dotted dynamic string references. Fresh S3 tests must exercise the approved Plaza owner rather than recreate the old feed, company-Agent filter, or direct Notification coupling as fixtures.

The legacy Agent Template authority is removed as its own minimum category:

- `backend/app/dao/agent_template_dao.py` and its `app.dao` package export
- `backend/app/services/template_seeder.py`

The DAO exposed unbounded category-filtered template listing plus generic create, get, and delete operations over the already removed `AgentTemplate` ORM fact. The seeder merged four Python-defined templates with folders under `backend/agent_templates/`, updated existing built-ins, created missing built-ins, and deleted retired built-ins only when the deleted Agent aggregate no longer referenced them. These old CRUD, persistence, folder-loading, merge-precedence, and database-seeding contracts are removed rather than adapted.

No dedicated Backend test imported or exercised the old Agent Template DAO or seeder at cutover. Agent Template remains an S3 product owner, but its owner and product contracts remain unreviewed. After both contracts are reviewed and approved, the target `agent_template` owner must receive fresh tests for the approved inventory source, persistence, Tenant and visibility scope, bounded listing, installation or creation authority, lifecycle, bootstrap ownership, and Agent-creation consumption. The old folder and Python seed shapes do not select the target contract.

This minimum deletion preserves the target Heartbeat package and later Heartbeat product obligations, `backend/agent_template/`, `backend/agent_templates/`, the legacy Alembic chain including Agent Template column revisions, dependencies, Frontend, and the empty target `modules/agent_template` package. These staged consumers and inventory assets do not authorize restoring the deleted ORM fact, DAO, DAO package export, seeder, database-seeding behavior, old CRUD behavior, or a compatibility shim. Their dangling imports and obsolete calls remain evidence for later minimum owner disposition commits and are not repaired here.

The deleted-authority guard makes both removed Agent Template import identities absent as modules and same-named packages, prevents restoration of the exact `agent_template_dao` package export under the static `app.dao` policy, and rejects ordinary Backend-test imports and dotted dynamic references. Fresh S3 Agent Template tests must exercise the approved owner contract rather than recreate the old DAO or seeder fixtures.

The legacy Agent Run Event DAO compatibility seam is removed as its own minimum category:

- `backend/app/dao/agent_run_event_dao.py`

The file defined no DAO or query behavior. It only re-exported the `agent_run_dao` object from `backend/app/dao/agent_run_dao.py`, while `app.dao` did not export the compatibility module and no current runtime or test imported it. The duplicate import identity is deleted rather than preserved as a compatibility path.

This minimum deletion preserves `backend/app/dao/agent_run_dao.py`, its `app.dao` package export, the `AgentRunEvent` model, its queries, callers, tests, migrations, and all remaining Run authority for later Run-owner disposition. Removing the unused compatibility module does not decide or advance that later disposition.

The deleted-authority guard makes `app.dao.agent_run_event_dao` absent as a module and same-named package and rejects ordinary Backend-test static imports and dotted dynamic references. Positive fixtures preserve static and dotted references to `app.dao.agent_run_dao`; there was no `agent_run_event_dao` package export to remove or guard.

The legacy OKR Agent relationship Hook is removed as its own minimum category:

- `backend/app/services/okr_agent_hook.py`

The Hook queried the deleted Agent and Organization relationship aggregates to bind new Organization members and company-visible Agents to a system Agent named `OKR Agent`. No current runtime, startup path, package export, or test imported or registered the Hook, so it had no effective execution path. Its implicit relationship mutation and startup-style backfill are deleted rather than adapted.

This minimum deletion preserves legacy Alembic revisions, Frontend, the empty target `modules/okr` package, and all later OKR product obligations. The complete OKR category below removes the old model, API, services, helper, and dedicated test rather than repairing their deleted dependencies. These retained surfaces do not authorize restoring the deleted relationship aggregate, implicit membership binding, system-Agent lookup, or backfill Hook. OKR remains a deferred S3 owner whose Product and owner contracts decide any future Agent integration.

The deleted-authority guard makes `app.services.okr_agent_hook` absent as a module and same-named package and rejects ordinary Backend-test static imports and dotted dynamic references. Positive fixtures preserve only the target OKR package and generic timezone-name validation.

The complete legacy OKR authority is removed as one category:

- `backend/app/models/okr.py` and `backend/app/api/okr.py`
- `backend/app/services/okr_daily_collection.py`, `okr_reporting.py`, `okr_scheduler.py`, and `business_calendar.py`
- `backend/tests/test_okr_daily_collection_runtime.py`

Together these sources owned the eight legacy OKR and report ORM records, tenant settings, objective and key-result CRUD, alignment and progress mutation, reporting periods, business-day policy, daily member collection through the deleted Heartbeat oneshot helper, company report aggregation, legacy Trigger synchronization, direct file writes, and the dedicated daily-collection test. They are deleted rather than adapted because their persistence, API, scheduling, collection, reporting, and cross-owner calls depend on deleted Agent, Organization, Focus, Notification, Session, Trigger, Heartbeat, Workspace, and Runtime authorities.

OKR remains a deferred S3 product capability for objectives, key results, alignment, progress, daily collection, member and company reports, and future Agent integration. Both its Product contract and owner contract remain unreviewed. This deletion does not approve either contract, select target persistence, APIs, business-calendar policy, scheduling, prompts, delivery, authorization, reporting hierarchy, or Agent behavior, or preserve the old implementation as compatibility. Fresh implementation begins only after both contracts are reviewed and approved.

This minimum deletion preserves the empty target `modules/okr` package, the generic `validate_timezone_name` helper in `timezone_utils.py`, legacy Alembic revisions, all 22 OKR coverage rows, Frontend, and the later OKR product obligations. Administration, activity, Notification, Channel, and Workspace sources remain staged for their own categories. Those retained surfaces do not authorize restoring any removed OKR module, class, table mapping, report workflow, scheduler, business-calendar policy, or compatibility shim.

The deleted-authority guard makes all six removed OKR import identities absent as modules and same-named packages and rejects ordinary Backend-test static imports and dotted dynamic references. A definition-level AST scan across all application Python rejects restoration of the exact `OKRObjective`, `OKRKeyResult`, `OKRAlignment`, `OKRProgressLog`, `WorkReport`, `MemberDailyReport`, `CompanyReport`, and `OKRSettings` classes or their eight table mappings under alternate paths. Positive fixtures preserve only the target `modules/okr` package, an unrelated `OKRPolicy` target declaration, and generic `validate_timezone_name` use. Fresh OKR tests must exercise reviewed and approved Product and owner contracts rather than rename the deleted model, API, service, helper, or dedicated test.

The legacy timezone resolution policy is removed from `backend/app/services/timezone_utils.py`. `COMMON_TIMEZONES`, the implicit `Asia/Shanghai` default, Agent-to-Tenant fallback queries, the synchronous object resolver, and the UTC-fallback clock helper had no current application or test consumer after the Agent and OKR authority deletions. They are deleted instead of preserving an apparent owner for target timezone choices or importing deleted Agent, Tenant, and DAO authorities.

The module retains only the independently pure `validate_timezone_name` helper, now without a production consumer. It validates caller-supplied IANA names without selecting a default or resolving owner policy. Focused tests preserve its accepted IANA names, invalid and empty-name error, and non-string `TypeError`; any future timezone default or effective-timezone resolution requires an approved owning contract and current consumer.

The legacy Token Tracker is removed as its own minimum category:

- `backend/app/services/token_tracker.py`

The module normalized provider usage dictionaries, estimated token counts, and attempted to update deleted Agent counters plus `DailyTokenUsage` through an independent database session. No current runtime, package export, or test imported any of its types or functions, so neither its normalization nor its write path could execute. The orphan tracker is deleted rather than retained as an apparent accounting authority.

This minimum deletion preserves the `DailyTokenUsage` model, administrator reporting queries, their migration history, API response contracts, and Frontend token-usage presentation. Those retained read surfaces remain staged for their own owner disposition and do not imply that the deleted tracker still produces current usage facts. A future usage-accounting producer requires an approved owner, explicit Run attribution, transaction semantics, provider normalization, and focused tests.

The deleted-authority guard makes `app.services.token_tracker` absent as a module and same-named package and rejects ordinary Backend-test static imports and dotted dynamic references. Positive fixtures preserve references to `DailyTokenUsage` and administrator reporting.

The dead standalone WeCom service is removed as its own minimum category:

- `backend/app/services/wecom_service.py`

The module implemented direct access-token retrieval and one text-message send call, but no current API, Channel adapter, package export, runtime path, or test imported either function. It did not participate in the active WeCom callback or stream-client paths. The unused facade is deleted rather than retained as a second apparent WeCom transport authority.

The later Channel category removes `backend/app/api/wecom.py`, `backend/app/services/wecom_stream.py`, their two tests, and the old WeCom configuration surface rather than repairing them. Legacy migration records and Frontend remain staged. That later deletion does not authorize restoring the dead standalone access-token or send-message facade.

The deleted-authority guard makes `app.services.wecom_service` absent as a module and same-named package and rejects ordinary Backend-test static imports and dotted dynamic references. The later Channel guard now rejects the former WeCom API and stream identities as well; no positive fixture blesses them.

The legacy AgentBay authority is removed as its own minimum category:

- `backend/app/api/agentbay_control.py`
- `backend/app/services/agentbay_client.py`
- `backend/app/services/agentbay_live.py`

The client wrapped the AgentBay SDK for browser, desktop, code, file, screenshot, shell, login, and live-link operations while also resolving Agent- or Tool-scoped API keys, injecting stored cookies, restoring remote sessions, and owning an in-process cache and lock registry keyed by Agent and Session/Run scope. The control API and live-preview helper reached directly into that private registry to lock automation, forward mouse and keyboard input, navigate, capture screenshots, and expose live browser or desktop state. Because all three sources shared one private Session registry and lifecycle, deleting only one would leave a broken partial authority. These old provider, Credential resolution, Session caching, human-control transport, and live-preview contracts are removed together rather than adapted.

No current target application router, package export, startup hook, target module, or Runtime registration consumed these files at cutover, and no dedicated Backend AgentBay test remained after the separately approved Tool-era test deletion. The removed source therefore provided no effective target execution path. Its presence alone did not constitute a supported provider integration.

This minimum deletion preserves the AgentBay SDK dependency and its `uv.lock` entry for a separate serialized dependency decision, the generic SDK logging guard, legacy Alembic revisions, Phase 0 disposition and owner ledgers, Frontend AgentBay settings and control panels, and the empty target `modules/agentbay` package. The later Channel category removes the application-owned Channel configuration enum, and the later vision-injection category removes the orphaned Tool/AgentBay compatibility helper rather than adapting it. Sandbox, Credential, Tool, Agent, Workspace, Run, migrations, and dependency declarations remain staged. These cross-owner surfaces do not authorize restoring the old API, SDK client facade, private Session registry, cookie injection, live-preview helper, control endpoints, or a compatibility shim.

The target AgentBay product capability remains deferred under the `agentbay` owner. After its Product and owner contracts are reviewed and approved, AgentBay must use public Credential, Permission, Agent, Run, Tool, and Workspace contracts and receive fresh provider lifecycle, authorization, bounded-result, cancellation, cleanup, and control-path tests. The deleted-authority guard makes all three legacy AgentBay import identities absent as modules and same-named packages and rejects ordinary Backend-test imports plus dotted dynamic string references, with unrelated-reference fixtures proving the guard remains scoped.

The legacy Tenant Knowledge publication adapter is removed as a separate minimum category:

- `backend/app/services/enterprise_sync.py`
- `backend/tests/test_enterprise_info_tenant_isolation.py`

`enterprise_sync.py` combined `EnterpriseInfo` creation and update, Redis publication, Agent selection, role filtering, and JSON writes under each Agent's `enterprise_info/` directory. Its test mixed Enterprise Info CRUD and Tenant isolation with publication into the deleted Agent aggregate and old Agent-file layout. The adapter and mixed test are deleted rather than carried into the target.

The target owner is definitively `tenant_knowledge`, but its owner contract remains unreviewed and this deletion does not authorize implementation. Enterprise Info persistence and its mixed API routes remain staged source evidence. After contract review and approval, `tenant_knowledge` owns CRUD, Tenant isolation, source facts, and their tests. Agent and Context test consumption and source attribution through the public Product Context consumer boundary; Product Context is never an alternate owner. Workspace owns and tests only its own mutation boundary and does not own Tenant Knowledge or publish it into Agent files.

The deleted-authority guard makes `app.services.enterprise_sync` absent as both a module and a same-named package, with negative fixtures for both restoration forms. The former mixed Enterprise transport is removed below rather than repaired; its prior dangling import did not authorize restoring the publication adapter.

The legacy direct Session substrate is removed as one complete authority category:

- `backend/app/models/chat_session.py`
- `backend/app/dao/chat_session_dao.py` and `backend/app/dao/chat_message_dao.py`, including both `app.dao` package exports
- `backend/app/services/chat_session_service.py` and `backend/app/services/channel_session.py`
- `backend/app/api/chat_sessions.py` and `backend/app/api/websocket.py`
- the `ChatMessage` ORM declaration, `chat_messages` table mapping, and `chat_role_enum` declaration from the mixed `backend/app/models/audit.py`
- `ChatMessageOut` and `ChatSend` from the mixed `backend/app/schemas/schemas.py`
- `backend/tests/test_chat_session_dao.py`, `backend/tests/test_chat_session_service.py`, `backend/tests/test_chat_sessions_api.py`, and `backend/tests/test_channel_session.py`

Together these sources owned the old mutable `ChatSession` and `ChatMessage` persistence, direct-session primary election and soft deletion, Channel conversation-to-session lookup, WebSocket intake, queued message execution, history reconstruction, checkpoint-driven streaming, direct Tool reconciliation, Session reply persistence, and the corresponding CRUD and transport payloads. Their tests protected only those legacy tables, services, routes, and WebSocket mechanics, so they are deleted rather than adapted. `ChatMessageOut` and `ChatSend` had no non-legacy Backend consumer at cutover.

Direct Session remains a required S2 owner and product capability. Once approved, its target owner contract must introduce immutable human Session Input, cutoff, Main Run initiation or resume, and atomic Session Reply through the target Session, Run, Context, Permission, and transaction boundaries. This deletion neither selects the target schema or API nor extracts compatibility behavior from the old services.

The old `app.api.websocket` local connection manager is deleted with the Web Chat entry that owned it. The subsequent Group and Channel categories remove their socket, realtime, protocol-adapter, and delivery-outbox consumers rather than repairing deleted Session imports; the later OKR category removes its old Session consumer. The target Trigger package, administration and maintenance scripts, every legacy Alembic revision, dependencies, Frontend, and empty target `modules/session` and `modules/okr` packages remain staged. Remaining production files may still refer to deleted Session identities; those dangling references are deliberate source-disposition evidence for later owner/category commits and do not authorize restoring the old Session model, DAO, service, HTTP or WebSocket transport, payload schemas, table, enum, connection manager, or a compatibility shim.

The deleted-authority guard makes all seven old Session substrate import identities absent as modules and same-named packages, prevents static restoration of `chat_session_dao` and `chat_message_dao`, and relies on the repository-wide static `app.dao` rule to reject dynamic package export hooks. A definition-level AST scan across legacy model and schema roots plus every target owner module rejects restoration of `ChatMessage`, `chat_messages`, `chat_role_enum`, `ChatMessageOut`, and `ChatSend` under any Python file path without treating comments, prose, or target `SessionInput` and `AgentReply` declarations as restoration. Its test-reference scope remains limited to the definitions and identities that this category owns; later category guards reject their own deleted Session-dependent fixtures. Fresh Session tests must exercise the eventual approved target owner and assembled product-input path.

The legacy Group/Participant authority is removed as one complete category:

- `backend/app/models/group.py` and `backend/app/models/participant.py`
- `backend/app/dao/group_dao.py` and `backend/app/dao/participant_dao.py`, including both `app.dao` package exports
- `backend/app/api/groups.py` and `backend/app/api/group_websocket.py`
- `backend/app/services/group_chat_service.py`, `group_message_service.py`, `group_file_service.py`, `group_realtime.py`, and `participant_identity.py`
- `backend/tests/test_group_api.py`, `test_group_chat_service.py`, `test_group_file_service.py`, `test_group_message_service.py`, `test_group_realtime.py`, `test_group_workspace_reconciliation.py`, and `test_participant_identity.py`

Together these sources owned the old Group and Participant persistence, membership and announcement CRUD, Group chat and WebSocket intake, mention planning and execution, Group message publication, Group Workspace file access and reconciliation, realtime subscription identity, and User/Agent Participant creation. Their tests protected those legacy tables, services, routes, connection semantics, and Workspace coupling, so they are deleted rather than adapted.

Group remains a required S2 owner and product capability. Once approved, its target owner contract must introduce Group administration, membership, announcements, Group Session, Group Workspace, group realtime transport, and external-group Channel mapping through the target Identity/Tenant, Agent, Permission, Session, Run, Workspace, Channel, and transaction boundaries. This deletion does not select the target persistence, API, realtime event, participant identity, or Workspace reconciliation contracts.

The later Channel category removes Channel configuration, outbound delivery, protocol adapters, their tests, `channel_user_service.py`, and `feishu_group_targets.py` rather than repairing deleted Group imports. Object-storage mechanics, the Workspace model and collaboration services, the target Trigger package, every legacy Alembic revision, dependencies, Frontend, and the empty target `modules/group` package remain staged. Those staged consumers do not authorize restoring Group or Participant models, DAOs, routes, services, tests, package exports, or a compatibility shim.

The deleted-authority guard makes all eleven old Group/Participant import identities absent as modules and same-named packages, prevents static restoration of `group_dao` and `participant_dao`, and relies on the repository-wide static `app.dao` rule to reject dynamic package export hooks. It rejects ordinary Backend-test imports and dotted dynamic references to the deleted identities. Positive fixtures preserve object-storage and Workspace mechanics and the target Trigger package; the later Channel guard rejects the removed Channel-user and Feishu group-target adapters. Fresh Group tests must exercise the eventual approved target owner rather than recreate the deleted aggregate or its cross-owner orchestration.

The legacy Schedule authority is removed as one complete category:

- `backend/app/models/schedule.py`
- `backend/app/api/schedules.py`
- `backend/app/services/scheduler.py`
- `backend/app/scripts/migrate_schedules_to_triggers.py`
- the schedule-only `schedule_occurrence_id` and `enqueue_schedule_runtime` branches from `backend/app/services/heartbeat_runtime.py`
- `backend/tests/test_schedule_runtime_intake.py`, `backend/tests/test_schedule_scheduler.py`, and `backend/tests/test_schedule_scheduler_startup.py`, plus the schedule-only assertions in `backend/tests/test_heartbeat_runtime.py`

Together these sources owned the mutable `AgentSchedule` row and `agent_schedules` table mapping, schedule CRUD and manual execution API, in-process cron polling and claim loop, Schedule-to-Trigger data conversion, Schedule-to-Heartbeat Runtime intake, application-startup scheduler registration, and their dedicated tests. They are deleted rather than adapted because Schedule is not a second target authority beside Trigger.

Scheduled execution remains a required product capability under the target Trigger owner. After its owner contract is approved, Trigger must define cron configuration, due-occurrence claiming, Run initiation, execution result, delivery, cancellation, and recovery through its public contracts. This deletion neither selects those contracts nor preserves the legacy Schedule API, table, scheduler loop, occurrence identity, migration script, or Runtime payload as compatibility behavior.

This minimum deletion preserves the empty target `modules/trigger`, `modules/heartbeat`, and `modules/okr` packages, later Heartbeat and OKR product obligations, the generic timezone-name validator, the legacy `agent_schedules` Alembic history, dependency declarations including `croniter`, Frontend, and all Trigger product obligations. The later Channel category removes Feishu group-target resolution, and the later OKR category removes the old OKR scheduler and business-calendar helper rather than treating either as target Trigger authority. Those staged consumers and migration records do not authorize restoring `AgentSchedule`, the `agent_schedules` application mapping, old Schedule modules, dedicated tests, or a compatibility shim.

The deleted-authority guard makes all four old Schedule import identities absent as modules and same-named packages, rejects ordinary Backend-test static imports and dotted dynamic references, and scans all application Python definitions for restoration of the exact `AgentSchedule` class or `agent_schedules` table mapping under another path. Positive fixtures preserve target Trigger, Heartbeat, and OKR modules, generic timezone-name validation, and target Trigger-policy or Heartbeat definitions; the later Channel guard rejects Feishu group-target restoration. Fresh scheduled-execution tests must exercise the target Trigger owner after its contract is reviewed and approved rather than rename the deleted Schedule fixtures.

The legacy Trigger/Webhook authority is removed as one complete category:

- `backend/app/models/trigger.py` and `backend/app/models/trigger_execution.py`
- `backend/app/dao/trigger_dao.py`, including its `app.dao` package export
- `backend/app/api/triggers.py` and the generic Trigger intake `backend/app/api/webhooks.py`
- `backend/app/services/trigger_daemon.py` and the entire `backend/app/services/trigger_runtime/` package
- `backend/tests/test_a2a_trigger_eval.py`, `backend/tests/test_trigger_runtime_intake.py`, `backend/tests/test_trigger_runtime_queue.py`, `backend/tests/test_trigger_runtime_scheduling.py`, and `backend/tests/test_webhooks_api.py`

Together these sources owned the mutable `AgentTrigger` and `TriggerExecution` rows, `agent_triggers` and `trigger_executions` application mappings, Trigger CRUD, generic external-webhook token intake and rate limiting, occurrence scheduling and deduplication, pending-execution claim and lease behavior, Trigger-to-Run registration, polling and message evaluation, OKR-specific Trigger dispatch, and their dedicated tests. The A2A-named test exercised the old Trigger evaluator's message query rather than an A2A request or result contract, so it is deleted with Trigger rather than preserved as A2A evidence.

Trigger remains a required S2 owner for schedules-as-Triggers, webhook and polling inputs, execution results, Run initiation, and delivery. Its owner contract remains unreviewed; this deletion does not approve that contract, implement the target owner, or preserve the legacy tables, DAO, APIs, daemon, Runtime package, lease, payload, or evaluator as compatibility behavior. Fresh implementation begins only after the Trigger contract is reviewed and approved.

This minimum deletion preserves the empty target `modules/trigger`, `modules/heartbeat`, and `modules/okr` packages, later Heartbeat and OKR product obligations, generic timezone-name validation, all legacy Alembic revisions, dependency declarations including `croniter`, Frontend, and later Trigger product obligations. The later Channel category removes Feishu group-target resolution, Channel-specific webhook handlers, and protocol adapters, while the later OKR category removes the old OKR Trigger consumers rather than repairing them. Those staged consumers and migration records do not authorize restoring the old Trigger/Webhook authority or a compatibility shim.

The deleted-authority guard makes all seven old Trigger/Webhook import identities absent as modules and same-named packages, prevents restoration of the exact `trigger_dao` export under the static `app.dao` policy, rejects ordinary Backend-test static imports and dotted dynamic references, and scans all application Python definitions for the exact `AgentTrigger`, `TriggerExecution`, `agent_triggers`, and `trigger_executions` facts under alternate paths. Positive fixtures preserve the target Trigger and Heartbeat packages; the later Channel guard rejects the removed Channel-specific webhook names. Fresh Trigger tests must exercise the reviewed and approved target contract rather than rename the removed model, queue, evaluator, or generic webhook fixtures.

The legacy Heartbeat authority is removed as one complete category:

- `backend/app/services/heartbeat.py` and `backend/app/services/heartbeat_runtime.py`
- `backend/app/scripts/migrate_legacy_heartbeat_template.py`
- `backend/agent_template/HEARTBEAT.md`
- `backend/tests/test_heartbeat_runtime.py` and `backend/tests/test_migrate_legacy_heartbeat_template.py`

Together these sources owned Agent heartbeat eligibility, active-hour and interval policy, the polling loop and occurrence claim, custom root-file instruction loading, activity and inbox context assembly, Heartbeat-to-Run registration, the shared legacy oneshot entry used by OKR, default prompt behavior, old template-file migration, and their dedicated tests. They are deleted rather than adapted because the target Heartbeat owner must not inherit an Agent-model loop, Workspace root-file convention, legacy Runtime intake, or hardcoded Tool guidance.

Heartbeat remains a required S2 owner for explicit configuration, scheduling, Session or independent Run initiation, result handling, and observability. Its owner contract remains unreviewed; this deletion does not approve that contract, implement the target owner, or preserve the old service loop, Runtime payload, oneshot helper, template migration, root-file convention, prompt, or eligibility policy as compatibility behavior. Fresh implementation begins only after the Heartbeat contract is reviewed and approved.

This minimum deletion preserves the empty target `modules/heartbeat` and `modules/okr` packages, generic timezone-name validation, all legacy Alembic revisions, `app/templates/HEARTBEAT.md` as unconsumed staged template inventory, Frontend, and later Heartbeat and OKR product obligations. The later Channel category removes the old Channel adapters, and the later OKR category removes its old oneshot callers and dedicated test rather than repairing them. The Sandbox subprocess backend no longer recognizes a staging-root `HEARTBEAT.md` or binds that file to `/HEARTBEAT.md`; its existing `focus.md` and `soul.md` root binds remain staged for their own ownership decisions. Sandbox execution-lease heartbeat tasks and Workspace lock heartbeat counters are distinct lifecycle terms and remain outside this authority. These retained sources do not authorize restoring legacy Heartbeat behavior or a compatibility shim.

The deleted-authority guard makes both old Heartbeat service identities and the migration-script identity absent as modules and same-named packages, rejects the exact `agent_template/HEARTBEAT.md` path, rejects Sandbox recognition of `HEARTBEAT.md` or `/HEARTBEAT.md`, and rejects ordinary Backend-test static imports and dotted dynamic references without an exception. Positive fixtures preserve the target Heartbeat package, Sandbox execution-lease heartbeat and Workspace lock heartbeat terminology, and the distinct staged `app/templates/HEARTBEAT.md` path. Fresh Heartbeat tests must exercise the reviewed and approved target contract rather than rename the deleted service, Runtime, migration, or template fixtures.

The Channel provider-transport preflight isolates reusable Feishu and DingTalk operations before the Channel authority is removed. `backend/app/services/feishu_service.py` now owns only explicit-credential provider transport: validated tenant-token retrieval, bounded HTTP operations, `FeishuAPIError`, native approval-instance calls, and the CardKit SDK client cache capped at fifty credential pairs. Tenant-token rejection and malformed success fail with a bounded provider error instead of returning an application token or empty fallback. The cache key is an in-memory credential tuple, while eviction diagnostics identify only the non-secret application ID. The service no longer loads application configuration, stores default credentials or an app token, exposes a generic application-token method, exchanges browser authorization codes, creates or looks up users, or imports Security, DAO, Identity Provider, Identity, User, Organization, or registration authorities. `get_tenant_access_token` requires both `app_id` and `app_secret`; callers resolve credentials before entering this transport.

`backend/app/services/dingtalk_service.py` retains its explicit-credential access-token and outbound HTTP operations. Its unconsumed `download_dingtalk_media` wrapper is removed because it delegated to the legacy `dingtalk_stream` connector without adding a transport contract; that connector is deleted with the Channel authority below. The retained `send_dingtalk_message` defaults of `use_robot=True` and `agent_id=app_id` are legacy transport conveniences, not approved target delivery policy; G006 must resolve delivery mode and agent identity through the reviewed Channel owner instead of inheriting those defaults. The preflight does not select a Channel model, API, connector, inbound protocol, delivery policy, identity mapping, credential persistence, Session/Group behavior, or target module.

The nine independent Feishu contact-search, Feishu provider-API, and MCP transport tests remain and collect without deleted application authorities; focused tenant-token and cache-diagnostic regressions extend that retained provider suite. The old Feishu group-target test is deleted with the Channel authority rather than adapted into a target contract. The deleted-authority guard rejects every absolute or relative application import in both provider transports together with restoration of Feishu identity methods, default credential state, optional tenant-token credentials, and the DingTalk stream wrapper, while preserving provider-library imports, explicit operations, and native Feishu approval-instance methods. This code-level reuse boundary does not approve the unreviewed target Channel owner contract, select its public API or persistence, or authorize compatibility with the legacy Channel implementation.

The complete legacy Channel authority is removed as one category:

- `backend/app/models/channel_config.py` and `backend/app/models/channel_delivery.py`
- `backend/app/api/atlassian.py`, `dingtalk.py`, `discord_bot.py`, `feishu.py`, `slack.py`, `teams.py`, `wechat.py`, `wecom.py`, and `whatsapp.py`
- `backend/app/services/atlassian_tool_service.py`, `channel_user_service.py`, `dingtalk_stream.py`, `discord_gateway.py`, `feishu_group_targets.py`, `feishu_ws.py`, `wechat_channel.py`, and `wecom_stream.py`
- `backend/scripts/remove_legacy_atlassian_agent_tool_secrets.py`; `ChannelConfigCreate`, `ChannelConfigOut`, and their recursive Channel-secret redaction helper from the mixed `backend/app/schemas/schemas.py`
- `backend/tests/test_channel_config_schema.py`, `test_channel_delivery_migration.py`, `test_feishu_group_targets.py`, `test_http_channel_runtime.py`, `test_remove_legacy_atlassian_agent_tool_secrets.py`, `test_stream_channel_runtime.py`, `test_wechat_channel_context.py`, `test_wechat_channel_runtime.py`, `test_wecom_channel_api.py`, and `test_wecom_stream.py`

Together these sources owned plaintext-bearing provider configuration, connection state, a retryable Channel delivery outbox, per-provider configuration and webhook routes, inbound message normalization, external identity lookup, Session and Run initiation, Group target projection, stream and gateway lifecycle managers, Atlassian Tool synchronization and credential cleanup, and tests for those old tables, payloads, delivery mechanics, connectors, and cross-owner behavior. They are deleted rather than adapted because they combine Channel transport with deleted Agent, Credential, Tool, Permission, Session, Group, Run, Organization, and Identity authorities.

Channel remains a required S2 owner for configuration references, explicit delivery policy, inbound Product Input, external identity mapping, connector lifecycle, delivery outcomes, and provider health. Its owner contract remains unreviewed; this deletion does not approve or implement that contract, preserve the old tables or APIs, select provider defaults, or create compatibility. Fresh Channel persistence, services, adapters, APIs, and tests begin only after the owner contract is reviewed and approved.

This minimum deletion preserves the isolated `feishu_service.py`, `feishu_contact_search.py`, `dingtalk_service.py`, `dingtalk_token.py`, `dingtalk_reaction.py`, `mcp_client.py`, and empty target `modules/channel` package. Legacy Alembic revisions, Frontend Channel surfaces, cleanup or backfill callers outside this category, and dependency declarations remain staged for their own source-disposition categories. The later OKR category removes its old Channel consumers. Other dangling imports, route calls, table names, and provider terminology are evidence of incomplete G002 disposition, not authority to restore a Channel model, API, service, connector, cleanup script, schema, package export, or compatibility shim. The implemented Atlassian credential-boundary Note is archived as historical evidence because its owner and enforcement path no longer exist in the target tree; it is not current Channel authority.

The deleted-authority guard fixes the inventory at exactly nineteen application import identities, rejects each as a module or same-named package, rejects the Atlassian cleanup-script path, static or dynamic API/model/service package re-exports, and ordinary Backend-test static imports or dotted references. A definition-level scan across all application Python rejects restoration of `ChannelConfig`, `ChannelDelivery`, `ChannelConfigCreate`, `ChannelConfigOut`, `channel_configs`, `channel_deliveries`, `channel_type_enum`, and the removed shared-schema secret-redaction symbols under alternate paths. Positive fixtures preserve only the isolated provider transports, contact search, token and reaction behavior, MCP, native Feishu approval operations, and the target Channel package.

The legacy Workspace authority is removed as one category:

- `backend/app/models/workspace.py`
- `backend/app/api/files.py` and `backend/app/api/upload.py`
- `backend/app/services/workspace_collaboration.py`, `workspace_locking.py`, and `workspace_reconciliation.py`
- `backend/tests/test_agent_files_api.py`, `test_files_api.py`, `test_files_api_storage.py`, `test_upload_api.py`, and `test_workspace_scope_schema.py`

Together these sources owned `WorkspaceFileRevision` and `WorkspaceEditLock`, the `workspace_file_revisions` and `workspace_edit_locks` mappings, Agent and Group file APIs, upload orchestration, revision history, edit leases, optimistic conflict checks, storage-to-Workspace reconciliation, and their dedicated tests. They are deleted rather than adapted because they combine Workspace mutation with deleted Agent, User, Group, Permission, Tool, Skill, Experience, Runtime, and storage-policy authorities.

Workspace remains a required S2 owner for Membership, Agent, and Group workspaces, their `memory/`, `skills/`, and `files/` trees, content-addressed mutation, publication, authorization, and bounded storage use. Its owner contract remains unreviewed. This deletion does not approve that contract, implement content-addressed storage or a target API, preserve the old tables, revision or edit-lock protocols, or create compatibility. Fresh Workspace persistence, services, APIs, and tests begin only after the owner contract is reviewed and approved.

The later Sandbox decoupling removes `workspace_paths.py` after confirming that no external consumer remains. It keeps the exact path-normalization algorithm private to Sandbox workspace policy and the exact root-containment algorithm private to the subprocess backend; the Enterprise and Agent-visible Workspace path behavior is deleted. The same slice removes `_verify_and_merge_outputs.record_revisions` and its database/Workspace branch after every internal caller was proven to pass literal `False`; ordinary merge, isolated output, publication ownership, persistent sessions, bwrap isolation, and callbacks remain unchanged. The isolated `infrastructure/object_storage/` mechanics, `sandbox/`, `text_extractor.py`, every legacy Alembic revision, Frontend Workspace surfaces, and the empty target `modules/workspace` package remain staged. Other remaining calls, table names, migration records, and Workspace terminology are source-disposition evidence, not authority to restore the old model, API, collaboration, locking, reconciliation, upload, or storage-facade modules.

The upload API's adversarial-filename regression is retained as a direct `text_extractor.extract_text` test because byte-preserving format dispatch is conversion behavior independent of the deleted upload policy. The deleted-authority guard fixes the inventory at exactly seven application import identities including `workspace_paths.py`, rejects each as a module or same-named package, rejects ordinary Backend-test static imports and dotted references, and scans all application Python definitions for the deleted Workspace models, tables, path classes, and public path helpers under alternate paths. A separate structural guard rejects restoration of the dead Sandbox revision argument, database/Workspace imports, or revision calls. Positive fixtures preserve object-storage infrastructure, private Sandbox workspace policy, and the empty target Workspace package.

The legacy A2A collaboration authority is removed as one category:

- `backend/app/services/collaboration.py`
- `DelegateRequest`, `InterAgentMessage`, and the `list_collaborators`, `delegate_task`, and `send_inter_agent_message` handlers from `backend/app/api/advanced.py`
- `GET /agents/{agent_id}/collaborators`, `POST /agents/{agent_id}/collaborate/delegate`, and `POST /agents/{agent_id}/collaborate/message`

Together these sources listed unrelated live or stopped Agents without a Tenant or explicit authorization boundary, created the deleted persistent Task model for delegation, recorded collaboration AuditLog entries, and wrote inter-Agent messages directly to `<agent>/workspace/inbox/*.md`. The service had no other Backend production consumer and no dedicated test consumer. The direct inbox write disappears rather than moving behind storage or Workspace because it is not the accepted A2A request, target Main Run, correlation, result, or authorization contract.

A2A remains a required S2 owner for `notify`, `consult`, and `task_delegate`, one-way and asynchronous request-result delivery, bounded input and authorization transfer, target Main Run initiation, stable request correlation, and result routing. Its owner contract remains unreviewed. This deletion does not approve or implement that contract, preserve the old list/delegate/message APIs, create a target Task or Workspace write, or add compatibility. Fresh A2A persistence, services, Tools, APIs, and tests begin only after the owner contract is reviewed and approved.

This minimum deletion preserves isolated object-storage mechanics, legacy Alembic revisions, the dangling collaborators request in `frontend/src/services/api.ts`, the disposition ledger and generator evidence, and the empty target `modules/a2a` package. The removed service was the final `store_agent_bytes` call site; that helper and both legacy storage facades are removed by the object-storage extraction below rather than retained as A2A compatibility. The Frontend caller is incomplete source disposition rather than an API compatibility promise, and other collaboration terminology does not constitute the deleted authority.

The deleted-authority guard fixes the collaboration-service inventory at exactly one application import identity, rejects it as a module or same-named package, and rejects ordinary Backend-test static imports or dotted references. The advanced transport guard below preserves the deleted A2A facts after the mixed file disappears. Positive fixtures preserve ordinary collaboration terminology and require the empty target A2A package to remain distinct from the deleted service.

The residual `backend/app/api/advanced.py` transport is removed after its A2A routes are already gone. The deleted remainder exposed unmounted legacy Agent Template list/get/create/delete routes, creator-identity handover, and Agent metrics assembled from deleted Agent, Task, Gateway, Audit, User, database, permission, and DAO authorities. No production or test module imported this API at deletion.

Agent Template, Observability, Agent management, and Permission remain future product obligations under their own target owners. Their owner contracts remain unreviewed, so this deletion does not preserve the old request/response schemas, mutable creator handover, metrics shape, routes, or DAO orchestration and does not add target transport or compatibility. The advanced API guard rejects the module or same-named package, ordinary Backend-test static and dotted references, and restoration under another `app/api` path of its exact legacy A2A, template, handover, or metrics schemas, handlers, and routes. Target owner implementations begin only after their contracts are reviewed and approved.

The legacy `backend/app/api/activity.py` transport is removed as a separate category. Its three routes exposed Agent activity rows and per-Agent chat-history conversation and message views by calling the mixed `activity_dao` over deleted Agent, User, Session, Participant, ChatMessage, permission, and database authorities. The target application did not mount the router, and no production or test module imported it at deletion.

Observability remains a future product obligation whose owner contract is unreviewed. This deletion does not preserve the old activity response dictionaries, conversation identity, query limits, routes, DAO joins, or compatibility and does not implement target activity or Run-history transport. The later service and persistence categories remove the logger, model, and DAOs rather than adapting them. The guard rejects the Activity API module or same-named package, ordinary Backend-test static and dotted references, and restoration under another `app/api` path of the three exact handlers or routes.

The legacy `backend/app/api/messages.py` transport is removed as a separate category. Its inbox and unread-count routes queried the deleted Agent, User, Session, Participant, and ChatMessage facts directly, performed per-message Participant lookups, inferred managed Agents from mutable creator identity, and returned a hard-coded unread count without read-state ownership. The target application did not mount the router, and no production or test module imported it at deletion.

Notification inbox and unread state remain future product obligations under the Notification owner, while Session and external participant identity remain separate owners. Those owner contracts are unreviewed. This deletion does not preserve the old inbox dictionaries, N+1 query path, creator-derived visibility, unread placeholder, routes, or compatibility and adds no target implementation. The guard rejects the Messages API module or same-named package, ordinary Backend-test static and dotted references, and restoration under another `app/api` path of the two exact handlers or routes.

The mixed `backend/app/api/admin.py` Platform Administration transport is removed as a separate category. It combined Tenant company lifecycle, first-admin Invitation creation, Identity and User counts, Agent execution and token metrics, leaderboards, enhanced operational metrics, and global platform settings in one unmounted router over deleted Tenant, User, Identity, Agent, Invitation, database, Security, and generic settings authorities. No production or test module imported it at deletion.

Platform Administration, Invitation, Identity/Tenant, Observability, Agent, and Enterprise Settings remain separate future owners whose contracts are unreviewed. This deletion does not preserve the old schemas, global queries, company toggle semantics, invitation side effect, metrics calculations, settings keys, routes, or compatibility and adds no target implementation. The guard rejects the Admin API module or same-named package, ordinary Backend-test static and dotted references, and restoration under another `app/api` path of its exact company, metrics, or platform-settings schemas, handlers, and routes.

The mixed `backend/app/api/enterprise.py` transport, its sole production schema dependency `backend/app/schemas/schemas.py`, and the two dedicated Enterprise preflight tests are removed together. A complete consumer search found no other production import of the monolithic schema module after the advanced and administration transports were deleted. Enterprise combined Model administration and testing, Tenant Knowledge, Audit, quota policy, System Email and templates, Runtime model settings, public and private system settings, SSO and Identity Provider administration, Organization directory synchronization, invitation issuance and export, and related security and persistence in one unmounted router. The shared schema module combined deleted Auth, Identity/Tenant, Agent, Task, Model, Enterprise, Audit, and Gateway DTOs without a target owner boundary.

Model, Audit, Tenant Knowledge, Enterprise Settings, SSO, Organization, Invitation, Observability, Platform Administration, Identity/Tenant, and other represented product capabilities remain future obligations whose owner contracts are unreviewed. This deletion does not approve or preserve any old endpoint, DTO, quota, setting, provider, synchronization, invitation, email, or error contract and adds no target transport, schema, or compatibility layer. The legacy models, DAOs, services, and other helpers remain staged for their own minimum commits.

The guard rejects `app.api.enterprise` and `app.schemas.schemas` as modules or same-named packages, their two deleted test paths, and ordinary Backend-test static imports or dotted references. Alternate route or schema-name guards are deliberately omitted: the deleted files mixed many future owners, and blocking common DTO names outside those exact legacy identities would pre-approve or constrain their unreviewed target contracts. Fresh tests begin from each approved owner contract rather than adapting the two deleted Enterprise transport fixtures.

The orphan `backend/app/services/activity_logger.py` and `audit_logger.py` services are removed together after a complete caller search found no production or test consumer. The first swallowed all failures while writing deleted `AgentActivityLog` persistence through the global DAO facade. The second exposed broad identity, role, Tenant, and Agent audit actions, bypassed an owning Audit service with raw SQL, mutated caller-provided detail dictionaries, and swallowed every write failure.

Observability and Audit remain future owners whose contracts are unreviewed. Their legacy models and DAOs are removed in the following persistence category rather than being treated as implementations. This deletion adds no target logging or compatibility behavior. The guard rejects both service identities as modules or same-named packages and rejects ordinary Backend-test static imports or dotted references.

The legacy Observability and Audit persistence category removes `activity_dao.py`, `agent_metrics_dao.py`, `models/activity_log.py`, and the mixed `models/audit.py` after their transports and logger services are gone. These sources owned `AgentActivityLog`, `DailyTokenUsage`, `AuditLog`, and `EnterpriseInfo`, their four tables, deleted Agent, User, Tenant, Session, Participant, and Task joins, and the two DAO package exports. No surviving production or test consumer remains outside the removed chain.

Observability, Audit, and Tenant Knowledge remain future product obligations whose owner contracts are unreviewed. This deletion adds no target model, repository, metric, audit, or compatibility behavior. The guard rejects all four identities as modules or same-named packages, both DAO exports, ordinary Backend-test static imports and dotted references, and restoration anywhere under `app` of the four exact legacy classes or table mappings.

The legacy Run/Settings persistence category removes `agent_run_dao.py`, `system_setting_dao.py`, and `models/system_settings.py` after the old Run models, transports, services, and Enterprise settings consumers are gone. `AgentRunDAO` was an orphan facade over already deleted Run, Command, and Event models. `SystemSettingDAO` and `SystemSetting` retained one unowned global JSON key-value table with invitation and SSO defaults after those product paths were removed. No surviving production or test consumer imports either DAO or model.

Run, Enterprise Settings, Platform Administration, Invitation, and SSO remain future product obligations whose contracts are unreviewed. This deletion does not choose target Run persistence, a settings registry, configuration precedence, invitation policy, or SSO redirect policy. The old Alembic revision reference remains staged until the single clean-break baseline replaces the legacy chain. The guard rejects all three identities as modules or same-named packages, both DAO exports, ordinary Backend-test static imports and dotted references, and restoration anywhere under `app` of the `SystemSetting` class or `system_settings` table mapping.

The old cross-cutting compatibility category removes `core/middleware.py`, `core/permissions.py`, `core/error_contract.py`, and their dedicated HTTP error-contract test after every mounted product route and old permission consumer is gone. The middleware combined a legacy JWT Tenant context with the global DAO ContextVar and a trace/error wrapper that the target application never registered. The permission facade encoded deleted Agent, User, Organization, Relationship, and creator-management facts with direct ORM queries and compatibility call signatures. The error contract wrapped old endpoint detail shapes and trace middleware behavior but had no target application consumer.

Permission, Identity/Tenant, Auth, and target HTTP error mapping remain future obligations. This deletion does not choose the target Principal union, visibility grants, authorization generation, middleware order, trace propagation, or public error payload. `core/email.py` remains untouched because retained provider mechanics still consume it. The guard rejects the three deleted identities as modules or same-named packages, the obsolete error-contract test, ordinary Backend-test static imports and dotted references, and restoration anywhere under `app` of the exact legacy middleware, permission facade, and error-handler entry facts.

The old `core/logging_config.py` is removed after repository-wide static, dotted, test, startup, and setup searches found no consumer. Importing it globally mutated Loguru handlers, standard-library logger handlers, transport log levels, and the AgentBay SDK logger, but the target application never imported it and no retained Sandbox or provider module depended on its trace ContextVar or configuration functions. This deletion does not choose target observability, trace propagation, logging format, handler lifecycle, or provider logging policy. Adjacent `core/email.py` and direct provider Loguru usage remain untouched. The guard rejects the module or same-named package, ordinary Backend-test static and dotted references, and restoration elsewhere under `app` of its exact global state and function definitions while allowing adjacent core modules and independently named provider logging mechanics.

The old `tests/test_base_dao.py` is removed separately because it asserts the retired global database registry, implicit session ContextVar, request Tenant ContextVar, and session-level Tenant query injection contract. Those authorities were removed earlier, so the test cannot collect and is not adapted to target infrastructure. The Sandbox decoupling then removes `dao/base.py`, `dao/query_dao.py`, and `core/security.py` while retaining a zero-byte static `dao/__init__.py` namespace consistent with the Backend layout. Sandbox configuration accepts an explicit optional secret decoder from its future owning composition boundary and fails before validation when a configured encrypted API key lacks that decoder or cannot be decoded. It does not read a target `SECRET_KEY`, import Auth/JWT/User behavior, or preserve a compatibility crypto facade. No current product entry supplies encrypted Sandbox configuration; the injection point preserves the mature configuration behavior without choosing future Credential ownership. The DAO directory's path-specific `AGENTS.md` forbids recreating a global persistence facade or package export. Guards reject all removed module and package identities, any nonempty DAO initializer, ordinary Backend-test static or dotted references, and restoration of the exact legacy Security/DAO definitions.

The orphan `backend/app/services/platform_service.py` is removed after a complete caller search found no production or test consumer. It combined environment, incoming Request, Tenant SSO domain, host parsing, and a hard-coded public URL into one fallback policy despite having no current owner. Platform Administration, Enterprise Settings, SSO, and Identity/Tenant contracts remain unreviewed, so this deletion adds no replacement URL policy or compatibility. The guard rejects the service as a module or same-named package and rejects ordinary Backend-test static imports or dotted references.

The orphan `backend/app/services/quota_guard.py` is removed under the accepted no-quota target. It had no remaining production or test consumer and combined User message quotas, Agent expiry, Model-call caps, Agent-creation limits, Heartbeat interval mutation, implicit administrator exemptions, hidden reset periods, and direct legacy persistence writes. This deletion adds no quota, expiry, or compatibility behavior. The guard rejects the service as a module or same-named package and rejects ordinary Backend-test static imports or dotted references; target owner contracts must not reintroduce quota policy through a renamed facade.

The legacy `backend/app/services/realtime.py` facade and complete `realtime_runtime/` package are removed after a caller search found no surviving production or test consumer. They owned Agent, Session, User, and Group Redis channels, global configuration and singleton state, subscriber lifecycles, presence reads, and product-shaped payload routing rather than generic transport mechanics. Session, Group, Channel, and Notification realtime behavior remains deferred under unreviewed owners, so no replacement or compatibility is added. The later Sandbox decoupling removes `core/events.py` and injects a minimal typed Redis client into `SandboxExecutionLeaseStore`; the lease preserves its exact Tenant/Agent/Session key, NX acquisition, millisecond TTL, owner-checked renew/release scripts, one-third-TTL heartbeat, publication-window renewal, and fail-closed `ownership_lost` behavior without adopting global Redis configuration or Pub/Sub. The guard rejects the old events identity as a module or same-named package, ordinary Backend-test static or dotted references, and restoration of its exact global client and Pub/Sub definitions while allowing the injected lease protocol. The Realtime guard continues to reject its two removed product identities.

The orphan `backend/app/services/resource_discovery.py` is removed after a complete caller search found no production or test consumer. It combined deleted Tool and AgentTool persistence, legacy database sessions, Tool configuration, Runtime-style outcome shaping, credential requirements, MCP and Atlassian discovery, and cross-provider materialization in one 1,261-line facade. Tool, Capability Market, Credential, and provider integration contracts remain unreviewed, so no replacement discovery service or compatibility is added. Retained MCP and provider mechanics remain isolated. The guard rejects the service as a module or same-named package and rejects ordinary Backend-test static imports or dotted references.

The legacy `backend/app/services/system_email_service.py` and its dedicated timeout test are removed after Enterprise transport deletion leaves no production consumer. The service combined database-backed settings resolution, hidden enable/disable policy, product templates, invitation delivery, compatibility parameters, SMTP transport, and silent-skip outcomes. `core/email.py` and the storage-decoupled `email_service.py` retain the explicit SMTP/IMAP mechanics and their focused tests. Enterprise Settings, Invitation, and Auth remain future owners with unreviewed contracts, so no System Email replacement or compatibility is added. The guard rejects the service as a module or same-named package, its old test path, and ordinary Backend-test static imports or dotted references.

The orphan `backend/app/services/vision_inject.py` and three one-shot maintenance scripts for department paths, duplicate Feishu users, and Plaza social Tools are removed together after complete production, test, setup, and startup searches find no consumer or lifecycle invocation. The vision helper retained a process-global image cache, AgentBay Tool-name coupling, old Workspace screenshot compatibility, and model-visible injection behavior. The scripts imported deleted database, Organization, Identity, Plaza, Tool, and configuration authorities and were not registered migrations or operator entrypoints.

Tool, AgentBay, Workspace, Organization, Identity/Tenant, Plaza, and maintenance ownership remain unreviewed or separately staged. This deletion adds no vision, cleanup, backfill, Tool mutation, or compatibility behavior and does not change migrations. The guard rejects all four identities as modules or same-named packages and rejects ordinary Backend-test static imports or dotted references. Generic image and document conversion remains outside this deletion.

The remaining orphan maintenance scripts `backend/remove_old_tool.py`, `backend/update_schema.py`, and `backend/scripts/backfill_chat_message_tenant_id.py` are removed after direct imports, dynamic imports, Shell, YAML, setup, deploy, test, and documentation searches find no consumer beyond the backfill script's own usage docstring. The first two directly mutated deleted Tool and PluginTool persistence through obsolete database modules. The backfill directly mutated the deleted ChatMessage/ChatSession schema through the removed global database session. None is an Alembic revision, registered operator command, or current migration entrypoint.

This deletion adds no Tool mutation, schema repair, Chat Message backfill, or compatibility behavior. Existing Alembic revisions remain frozen and unchanged until G008 replaces the legacy chain. The guard rejects both root identities and the script identity as modules or same-named packages, ordinary Backend-test static or dotted references, and actual Shell or executable YAML/TOML invocations. It parses the command position, Python script or `-m` operand, and configured entrypoint instead of rejecting inspection commands or inert descriptions that merely name a deleted file. Positive fixtures preserve current Backend scripts, Alembic invocation, and `rg`/`grep`/`test` checks without treating legacy revision files as mutable G002 source.

The retained `backend/app/services/email_service.py` is decoupled from legacy Workspace and storage attachment behavior before object-storage disposition. Its `send_email` operation now accepts only explicit provider configuration, recipients, subject, plain-text body, and optional CC. The removed `attachments`, `workspace_path`, and `agent_id` inputs no longer trigger global storage selection, Agent-prefixed key assembly, broad exception suppression, or an unbounded local-disk fallback. No Backend production or test caller used that signature at this cutover.

The same module retains provider presets, explicit SMTP transmission, bounded provider error text, IMAP reads, replies, and connection checks as staged email protocol mechanics. It is not a target Tool, Credential owner, Workspace consumer, or approved public contract. Email attachments require a reviewed Tool contract and an approved Workspace read/reference boundary before they can return; this deletion does not add an object-storage reader or select attachment size, count, content-type, authorization, or lifetime policy.

Focused tests preserve explicit SMTP configuration, plain-text MIME construction, CC recipient delivery, missing-credential rejection, and the existing 200-character provider-error bound. The structural guard rejects `email_service.py` imports of `app.services.storage` or `app.services.storage_runtime` and restoration of the three removed `send_email` parameters, while a positive fixture permits the independent `core.email` SMTP mechanic.

The legacy seed and schema-bootstrap authority is removed as one category:

- `backend/seed.py`
- `backend/app/scripts/bootstrap_db.py`
- the seed execution stage and related progress and failure text from `setup.sh`

These paths imported the deleted monolithic model registry, called `Base.metadata.create_all`, created a default Tenant and built-in Agent Templates, conditionally created demo Agents, directly materialized `<AGENT_DATA_DIR>/<agent>/{workspace,memory,skills}`, `soul.md`, and `memory/memory.md`, and applied a best-effort sequence of inline `ALTER TABLE`, index, and data-repair statements. Schema patches swallowed each failure and continued, so startup success did not establish a known schema. The setup script made this obsolete source executable during first-time installation.

The target application does not seed product data, create tables, repair schema, or materialize Workspace and Soul paths during setup or process startup. `setup.sh` now synchronizes `backend/.env` from the target template, prepares only the `clawith_target` database, and installs Backend dependencies without executing application bootstrap code. `restart.sh` holds an atomic restart lock and directly launches one `.venv/bin/uvicorn app.main:app` worker before verifying `/api/health`. Its per-invocation evidence includes process PID, start identity, command identity, and opaque startup ID; cleanup signals only that invocation's verified child and removes shared evidence only while it still matches. Replaced evidence is preserved, and a child that cannot stop remains tracked through primary or startup-scoped unsettled evidence that blocks later restart pending manual recovery. Health success is accepted only when the response and evidence match that launch. It does not start the Frontend, Docker, product workers, connectors, migrations, or legacy Runtime roles. The canonical container entrypoint follows the same single-worker boundary. Structural guards expand chained assignments and split Shell command segments before classifying inert output, so a safe `echo` cannot hide a following executable command. Helm resources require the exact deferred guard rather than a substring-compatible variable or constant-true expression. The legacy Alembic revision chain remains unchanged until G008 replaces it with the approved target baseline.

Every retained Backend database value in runtime Settings, the coverage authority, root, CI, CD, deploy, and Helm configuration resolves to the single `clawith_target` namespace. The inventory builder imports the Settings-owned name and rejects a divergent manifest. The accepted capability matrix records the current 401 G002 source-disposition decisions without changing their rows. All Compose services require the explicit `deferred-product` profile, and Helm defaults `g002Deferred` to true and renders no resources. These configurations are not a supported G002 product entry. The three CI scripts that ran legacy fresh-database migration, product deployment, and cross-version upgrade flows are removed; Drone and GitHub Actions call one shared gate that carries G000 and G001 checks into G002 architecture, full test, collection, Ruff, and Pyright validation. No G002 CI path runs Alembic, builds release artifacts, or deploys product containers.

Localized READMEs, contribution guidance, Backend Alembic guidance, Helm quickstarts, chart documentation, and release-deployment guidance now expose only the G002 health-only boundary and link back to the current root README. Their executable examples no longer restore legacy migration, Docker, Helm, Frontend, root-dotenv, deployment, or upgrade paths. `backend/alembic.ini` uses `clawith_target` and records that operator migration remains unavailable until G008; legacy revision files remain untouched.

The deleted-authority guard rejects the root seed script, the bootstrap module or same-named package, ordinary Backend-test static imports and dotted references, and setup or startup script references to either executable. The script guard also rejects restoration of `create_all`, inline schema patch/repair behavior, `AGENT_DATA_DIR` Workspace creation, or `soul.md` and `memory.md` materialization. Positive fixtures preserve explicit Alembic invocation and the canonical `app.main:app` startup without treating either as legacy bootstrap authority.

The legacy storage facade and fallback authority is removed while bounded object-storage mechanics move to infrastructure:

- `backend/app/services/storage.py` and the complete `backend/app/services/storage_runtime/` package are deleted, including global backend selection, local fallback migration, Agent and Tenant key policy, local-path materialization, and re-exports.
- `base.py`, `local.py`, `s3.py`, and `utils.py` move to `backend/app/infrastructure/object_storage/`; its `__init__.py` is empty and does not re-export implementations.
- `test_storage_conditional_atomicity.py` and `test_storage_s3.py` move under `backend/tests/infrastructure/`; the fallback test is deleted with the unsupported migration behavior.

The infrastructure contract retains object existence, directory and file checks, listing, byte and text reads and writes, deletion, stat/version facts, conditional mutation, and S3 presigning. `normalize_storage_key` now rejects every slash- or backslash-delimited `..` segment instead of resolving it. Local path containment resolves both paths and uses `Path.relative_to`, including rejection of sibling-prefix symlink escapes, and no longer raises FastAPI transport errors. Conditional mutation defaults fail closed with `NotImplementedError`; Local keeps its cross-process lock around check and mutation, while S3 keeps provider-native `IfMatch` and `IfNoneMatch` and implements unconditional writes and deletes explicitly. Temporary local copies, unowned temporary files, local-path exposure, unused write-worker configuration, and unused recovery helpers are removed.

This extraction does not approve Workspace behavior or expose object storage as a product service. Application composition and infrastructure may construct concrete backends; the target Workspace owner may import only `object_storage.base`; every other product owner and `app.runtime` must consume an approved Workspace public service. No current target product consumer is added by this slice.

G004 must resolve complete-operation bounds for object and byte reads, multi-page listing, tree deletion, and returned metadata before Workspace exposes them. The retained S3 `list_dir` currently materializes all pages without an entry, byte, page, or time limit; `delete_tree` handles one page and does not inspect per-object deletion errors; the synchronous boto client has no owning close lifecycle. These are explicit blockers for target Workspace use, not behavior approved by this extraction, and no speculative limit or lifecycle is selected here.

The deleted-authority guard rejects exactly `app.services.storage` and `app.services.storage_runtime` as modules or same-named packages, their three former test paths, and ordinary Backend-test static imports or dotted references. Positive fixtures preserve the infrastructure object-storage identities and target Workspace package. The import-boundary guard permits concrete backends only inside infrastructure and application composition, permits Workspace to import only `object_storage.base`, and rejects direct object-storage imports from every other product owner and `app.runtime`, with positive and negative fixtures for each boundary.

### Delete without porting

The following behavior and its dedicated source, schema, tests, configuration, and dependencies are removed:

| Removed behavior | Current source evidence |
|---|---|
| OpenClaw Agent type, API key, Gateway polling/report/send-message, remote online status, and Native/OpenClaw branching | `app/models/agent.py`, `app/models/gateway_message.py`, `app/api/gateway.py`, OpenClaw branches in `app/api/websocket.py`, Gateway and OpenClaw tests |
| Agent execution status, container identity, start/stop lifecycle, Agent expiry, `agent_type`, system-Agent runtime variants, and Agent-owned Runtime counters | current `Agent` fields and `app/api/agents.py` start/stop/API-key routes |
| LangGraph graph, PostgreSQL Checkpoint, Thread state, Checkpoint compatibility decoding, Command worker, execution takeover/replay, scheduling lanes, and checkpoint-side-effect reconciliation | `app/services/agent_runtime/graph.py`, `state.py`, `checkpointer.py`, `langgraph_driver.py`, `command_worker.py`, `checkpoint_side_effects.py`, `worker_service.py`, `scheduling_lane.py`; `agent_run_commands`; LangGraph dependencies |
| Generic Runtime Event, Tool execution Ledger, async Tool polling, Tool repair budget, and product reconciler | `agent_run_events`, `agent_tool_executions`, `event_stream.py`, `tool_result_store.py`, `async_tool_poll.py`, `tool_repair_budget.py`, `product_reconciler.py` |
| Old Prompt and Context authority, implicit relationship/setting queries, and fallback Context assembly | `app/services/agent_context.py`, its direct consumers and tests; replacement comes only from the target Context source contract |
| Persistent Task and Task Log lifecycle, Task CRUD/intake, Task completion projection, and Task execution service | `app/models/task.py`, `app/api/tasks.py`, `app/services/task_executor.py`, `task_completion.py`, related tests |
| Approval Request, L1/L2/L3 autonomy policy, approval-driven Waiting/Resume, and approval APIs | `ApprovalRequest`, Agent `autonomy_policy`, `autonomy_service.py`, approval routes in `agents.py` and `enterprise.py`, Runtime approval authorization |
| Model fallback, cross-Model failover, Model-step/Tool-round cap, Run duration cap, and Token/message/call quota enforcement | `fallback_model_id`, `app/services/llm/failover.py`, `quota_guard.py`, Agent and User quota fields, quota APIs and tests |
| Relationship labels, creator-management semantics, relationship Memory/access metadata, no-op `relationships.md` compatibility regeneration, and the legacy relationship API | `AgentRelationship`, `AgentAgentRelationship`, `app/api/relationships.py`, and `access_relationships.py`; the file-regeneration hook no longer projected live Workspace state, while explicit Membership/Agent visibility assignment is rewritten under Permission rather than removed |
| Structured Experience library, revision drafts, citation projection, and retrieval/RAG path | `ExperienceEntry`, `ExperienceReference`, `app/api/experience.py`, `experience_retrieval.py`, Runtime experience citation paths |
| Agent-authored Skill creation, evaluation loop, generated Skill assets, and direct Skill file mutation | `skill_creator_content.py`, `skill_creator_files/`, Agent-facing Skill write/browse routes; first release permits only controlled Market/Admin installation |
| Agent handover by changing creator identity | `app/api/advanced.py` handover route; target creation audit is immutable and Agent management belongs to Tenant administrator |
| Session Context State, background Session compaction authority, and checkpoint-derived Context state | `session_context_states`, `session_context_*` services and their tests |
| Legacy schedule object separate from Trigger | `AgentSchedule`, `app/api/schedules.py`, `scheduler.py`; schedule behavior is re-expressed by Trigger ownership |
| Startup schema repair, default-Tenant repair, inline data migration, backfill scripts, old bootstrap patches, and the existing Alembic chain | migration and repair blocks in `app/main.py`, `app/scripts/migrate_*`, `backfill_*`, `setup_langgraph_checkpoints.py`, every current `alembic/versions/*` migration |
| Storage compatibility fallback and old key/path fallback | `app/services/storage_runtime/fallback.py` and compatibility reads of legacy Workspace or storage layouts |
| Monolithic Model/Tool authority facades | `app/services/llm/caller.py` and `app/services/agent_tools.py`, which combine old ORM, Prompt, permission, fallback, Tool exposure/dispatch, approval, Ledger, plaintext-Secret compatibility, and loop behavior |

Tests whose only purpose is to preserve one of these deleted contracts are deleted with it. A useful scenario is rewritten against the new owner rather than retaining an old fixture or compatibility adapter.

### Rewrite as foundational modules

These capabilities are required by the first implementation slices and receive new modules, tables, services, APIs, and tests:

| Target module | Current capability to inventory, not preserve | Replacement owner |
|---|---|---|
| Identity and Tenant | `Identity`, `User`, `Tenant`, auth middleware, tenant switching | Account, Membership, Tenant, Tenant Principal, Platform Principal |
| Minimal Permission | Agent access modes, `AgentPermission`, current relationship assignment APIs, scattered route checks | one Permission Resolver, explicit Membership/Agent visibility-grant mutation surface, login-scoped human authorization and per-Run Agent configuration |
| Credential and Audit | Agent credential table, Secret-bearing Channel/Tool/LLM JSON, audit logger | one Credential store with binding-specific owner matrix; closed Audit actor union |
| Agent | overloaded `Agent` row, templates and bootstrap fields | narrow Tenant Agent identity, Soul, greeting, model relation, enabled/archive controls |
| Model System | LLM rows, caller/client, runtime settings, capability probing, failover | fixed per-Run Model Policy, Provider adapters, normalized result, required continuation state |
| Tool and Capability | `Tool`, `AgentTool`, builtin definitions, MCP discovery, Skill database and ClawHub paths | Registry, Tenant Tool Definitions and Grants, Capability Market, per-Agent MCP connections, Workspace Skill packages |
| Workspace | Agent files, group files, Skill browse/write, Experience Memory, revision and edit-lock tables | Membership, Agent, and Group Workspaces with `memory/`, `skills/`, `files/`, CAS mutation and one-way publication |
| Agent Runner and Loop | the entire `app/services/agent_runtime/` execution authority | Run, immutable Snapshot, append-only History, Context Projection, one lightweight Runner and Loop |
| Context | `agent_context.py`, Runtime context builders, Session Context State, implicit relationship/settings lookup, and old Base Prompt | explicit Platform Instructions, Agent Identity/Soul, Product Input, Run Context, Workspace Discovery, Tool Exposure, Retrieved Content, and Model Context Profile sources |
| Direct Session | `ChatSession`, `ChatMessage`, WebSocket chat intake and delivery | immutable human Session Input, cutoff, Main Run initiation/resume, atomic Session Reply |
| Task, Todo, A2A, Goal | current Task tables, planning services, A2A Runtime and Gateway correlations | model-facing Tools and Session Goal configuration without Task/Todo/Goal lifecycle objects |
| Product handoff | checkpoint completion handlers and generic reconciler | owner-specific atomic result records and A2A pending delivery |

The old `app/services/agent_runtime/` package is not incrementally converted. New Runtime modules are built from the accepted contracts; only independently pure helpers may be copied after review. Once the new composition owns a path, the corresponding old Runtime files and tests are deleted rather than kept behind a compatibility switch.

### Reuse behind new owners

The following implementations carry useful bounded behavior and should be evaluated for extraction instead of rewritten automatically:

The retained Sandbox package has no current product, Tool, API, Runner, or application-composition entry. It is a tested reuse candidate, not an active target capability. The stale implemented venue-ownership Note is archived because its old `agent_tools` entry and formatter no longer exist; `2026-09-03-sandbox-reuse-candidate.md` records the preserved mechanics and the explicit future owner, secret, Redis, Workspace, authorization, and assembled-entry requirements.

| Reusable capability | Candidate source | Required adaptation |
|---|---|---|
| Sandbox providers and isolation | `app/services/sandbox/` including local Docker/subprocess and remote providers | preserve the tested venue, fallback, session, lease, isolation, and publication mechanics; activate them only through a reviewed owner with explicit dependencies and assembled-path tests |
| Local and S3 object operations | `app/infrastructure/object_storage/local.py`, `s3.py`, and infrastructure atomicity tests | expose only through the new Workspace owner; legacy facades, fallback, and product path helpers are removed |
| Document and text conversion | `document_conversion/` and `text_extractor.py` | register reviewed operations as ordinary Tools with bounded results; the orphan vision injection facade is deleted |
| Provider HTTP and multimodal encoding | individually named functions recovered from Git history for the former `app/services/llm/client.py`, `multimodal_content.py`, and narrow utilities | review and test each recovered function behind the target Provider Adapter; the old package and `llm/caller.py` are never restored |
| MCP transport and OAuth mechanics | `mcp_client.py` and current OAuth helpers | place behind Tenant Catalog materialization, Agent connection, Credential, and new Tool executor |
| External Tool protocol operations | capability-specific Atlassian, Feishu, Google Workspace, email, deployment, search, and document helpers | preserve supported operations but regenerate Definition/Grant registration and normalized Tool Result boundaries; `agent_tools.py` and `builtin_tool_definitions.py` remain inventory inputs and are not reusable facades |
| Channel protocol adapters | isolated Feishu and DingTalk provider transports; legacy WeCom, WeChat, Slack, Discord, Teams, WhatsApp and Atlassian adapters are deleted | reuse only the isolated explicit-credential provider operations; rebuild authentication, Product Input, Session/Group ownership, Run start, delivery, and connector lifecycle after Channel contract approval |
| Realtime transport | Redis pub/sub and WebSocket connection mechanics | publish only committed owner events; replace Runtime event/checkpoint payloads |
| Cross-cutting infrastructure | database engine/session and generic time-zone validation | retain only generic behavior; rewrite Tenant middleware, security, error mapping, and logging around approved target owners and the Principal union |

Reuse requires direct source and behavior review. Deleted Provider candidates may be recovered only from Git history; the immutable legacy checkout remains black-box behavior evidence and is never imported, copied from, or treated as a source tree. A candidate that imports deleted ORM models, Runtime contracts, checkpoint data, legacy permission, plaintext Secret fields, or fallback behavior is split or rewritten before use. Code formerly in `llm/caller.py`, `agent_tools.py`, or another authority aggregator may move only as an individually named and tested pure function or single-capability protocol operation; the original module, facade, initialization, fallback, discovery, and dispatch paths are always deleted.

### Preserve product capability but rewrite later

These currently exposed features are not prerequisites for the foundational Runner, but they are not silently deleted. Each becomes a later module slice with its own owner decision and target tests:

- SSO, OAuth identity binding, Google Workspace directory sync, invitations, registration, password recovery, and organization synchronization.
- Group administration, membership, announcement, Group Session, Group Workspace, group realtime transport, and external-group channel mapping.
- Tenant EnterpriseInfo, Tenant Knowledge Base files, administrator mutation, Agent read-only Tenant Knowledge Context, and replacement consumption. The definitive owner is `tenant_knowledge`, whose contract remains unreviewed; Product Context is only the public consumer boundary for Agent and Context, and Workspace is not an alternate owner.
- Heartbeat, schedules-as-Triggers, webhook and polling Triggers, Trigger execution results, and Focus.
- Feishu, DingTalk, WeCom, WeChat, Slack, Discord, Microsoft Teams, Atlassian, and other mounted Channel configuration, inbound message, outbound delivery, and connection health.
- OKR objectives, key results, alignment, progress, daily collection, member/company reports, and the OKR Agent product integration.
- Agent templates, onboarding, directory presentation, activity/usage observability, notifications, public pages, Plaza, enterprise settings, platform administration, email configuration, and AgentBay control.

`defer` preserves the product capability and its later contract, implementation, and test obligations, not the old implementation. Phase 0's 401/401 `disposition_approved` coverage rows collectively authorize G002 to delete the classified old authorities before replacement implementation. Per-category commits are reviewable execution slices of that collective disposition approval; they are not new approval states, boundaries, or ledgers. This sequencing does not cancel the capability, select its target persistence or API contract, or create compatibility between old and new identities.

### Migration, composition, and dependency disposition

G002 has no target Alembic baseline. The checked-in version chain remains frozen topology evidence, while `alembic/env.py` retains only target configuration and metadata identity plus fail-closed offline and online execution entries. G008 owns the reviewed one-time replacement with a target baseline. The target composition performs no schema mutation or product bootstrap; later owner integration may add schema verification, but startup never calls `create_all`, migrates files, patches existing records, or swallows bootstrap ownership failures.

The G002 startup boundary launches only `app.main:app` in one Uvicorn worker and no longer runs Alembic, LangGraph checkpoint installation, schema repair, process-role branches, Frontend startup, or Docker auto-selection. Local setup and restart read or write only `backend/.env` and use `clawith_target`; the root `.env.example` is not a Backend template. `alembic/env.py` reads metadata only from the target infrastructure registry, permits only `heads` and `history` topology inspection, and rejects console, `python -m`, and programmatic execution with the G008-unavailable diagnostic before connection or mutation. The Docker entrypoint only drops privilege and starts the worker. The existing legacy revision chain and head remain temporarily present for topology evidence until G008; they are not the target baseline or a supported target upgrade path, and this change does not generate or apply a new baseline.

FastAPI composition has one factory and one application lifespan. The lifespan owns separate control and execution SQLAlchemy engines against the validated target PostgreSQL database, creates isolated pools of twenty connections with zero overflow by default, and awaits disposal of both pools at shutdown. Target configuration loads dotenv values only from `backend/.env`, rejects unknown dotenv settings, requires a complete `postgresql+asyncpg` URL in the isolated `clawith_target` namespace, and fails when neither an explicit application version nor the non-empty `backend/VERSION` artifact is available. Each module later registers its own transport adapters and bounded lifecycle resources; current process-role branches, connector managers, schedulers, Runtime worker startup, and seeding blocks are not copied wholesale. The first release enforces one non-overlapping Runner deployment while connector and product background services retain their own bounded lifecycle owners.

The direct dependency set now follows surviving imports and startup drivers. Deleted-owner pins for Redis, Auth/JWT, schedules, extraction fallbacks, unused document/image libraries, deleted Channel SDKs, AgentBay, naming helpers, Markdown, LangGraph/Checkpoint, Psycopg, and the unused Teams identity extra are removed. FastAPI's standard bundle and HTTPX's SOCKS extra are also omitted because no retained path uses their optional features. HTTPX remains once as a runtime dependency; PyYAML is development-only because only architecture tests import it. `lxml-html-clean` remains direct because Sandbox imports `lxml.html.clean.Cleaner`; `boto3` and `aioboto3` remain direct because object-storage loads both at execution boundaries. The injected Sandbox lease protocol does not create a Redis client or justify a Redis runtime dependency. `backend/uv.lock` is tracked, and setup plus the dependency guard require it to remain current with `pyproject.toml` before frozen sync. The EnterpriseInfo upgrade compatibility test is deleted; `test_v1_11_4_tool_runtime_migration_merge.py` remains only as frozen revision-topology evidence until G008 replaces the chain.

### Test disposition

New tests are organized by target owner and contract. Runtime tests cover Run start/resume/cancel, Status and History atomicity, Child and product handoffs, Waiting, interruption, Context source reconstruction, Tool exposure/dispatch identity, Provider continuation, and concurrency. Database tests cover fresh baseline creation, composite Tenant foreign keys, partial uniqueness, owner checks, idempotency, authorization generation, and concurrent duplicate submission.

Existing pure tests for provider encoding and transport, Sandbox isolation, S3/local object-storage atomicity, document conversion, and external Tool behavior may be retained after their imports are moved to the new boundary. Tests for deleted Channel connectors, models, routes, fields, compatibility reads, fallback, quotas, approvals, Checkpoints, Commands, Ledger, Task persistence, relationship Memory, or OpenClaw are removed.

## Alternatives considered

### Incrementally refactor the current Agent Runtime

The current package makes Checkpoint, Command, Tool Ledger, scheduling lane, product reconciliation, and LangGraph Thread state central to execution. Preserving it while introducing Run History and the new owner boundaries would create two authorities and prolong compatibility work the clean break explicitly rejects.

### Delete every Backend file and recreate all provider code

Provider adapters, Channel protocol handling, Sandbox isolation, storage operations, document conversion, and external Tool implementations contain useful bounded behavior. Rewriting all of them simultaneously adds risk without changing their responsibility. They are reused only after separation from old authority.

### Keep every current product table until its frontend is rewritten

This would force new core modules to reference old User, Agent, permission, Task, Credential, and Workspace identities. Phase 0's collective disposition approval permits G002 to delete source for a deferred capability before the Frontend or replacement is ready; deferral does not require an old table to survive until then. The final Backend has one target schema and no cross-schema compatibility contract.

## Acceptance criteria

- Every current Backend capability is classified as delete, rewrite, reuse, or defer; omission does not decide product behavior.
- OpenClaw, LangGraph Checkpoint, Command, Runtime Event, Tool Ledger, persistent Task, Approval, fallback Model, quota enforcement, relationship labels/access metadata and no-op compatibility regeneration, Experience RAG, Session Context State, legacy Schedule, legacy Trigger/Webhook, legacy Heartbeat, startup repair, and old migration behavior have no target execution path.
- Explicit Membership/Agent visibility assignment is rewritten as the producer of `agent_visibility_grants`; deleting legacy relationship semantics does not remove this required Permission surface.
- Tenant EnterpriseInfo and Knowledge Base remain explicitly deferred under `tenant_knowledge` until its owner contract is reviewed and approved; Product Context remains a consumer boundary, and Tenant Knowledge is neither silently deleted nor placed under Workspace ownership.
- Agent handover through mutable creator identity is removed; creation identity remains immutable audit and Tenant administrator retains management authority.
- The foundational rewrite starts from new module owners; minimal Auth joins G003 and full Auth workflows remain a later product slice. Schema integration precedes the G008 target baseline. Login-scoped authorization supersedes the earlier generation/dependency-projection design.
- Sandbox, storage, conversion, Provider, MCP, external Tool, Channel, realtime, and infrastructure code is reusable only after removing imports and assumptions owned by deleted contracts.
- Every currently mounted product capability is either included in a rewrite slice or explicitly deferred; deferral preserves its later owner-contract, implementation, and test obligations even when G002 deletes the old authority first.
- Phase 0's 401/401 `disposition_approved` rows collectively authorize deletion of the classified old APIs, models, tests, configuration, and dependencies; per-category commits only slice that authorized execution for review, replacement implementation is not a prerequisite, and deletion does not authorize target contract choices or compatibility.
- No compatibility adapter, dual write, fallback read, startup repair, or legacy data migration connects the current Backend to the target.
- Implementation planning sequences owner prerequisites before consumers and verifies each cutover through the target contract rather than old test expectations.

## Risks and open questions

This source map is grounded in current route registration, models, services, migrations, tests, and startup composition, but dynamic external consumers and Frontend calls still require a separate cross-layer inventory before each API removal. A route with no Backend registration is not treated as supported solely because a file exists. `tenant_knowledge` is the definitive owner, but its unreviewed contract, persistence, API, source-attribution, and Product Context consumption boundaries remain unresolved implementation work.

The exact package tree, implementation slices, retained third-party dependencies, and temporary development branch cutover order remain implementation-planning decisions. No old persistence contract may leak into those decisions merely to reduce short-term code movement.

The final handoff for this deletion is to `identity_tenant` for Membership, Auth/Account for global login fields, Organization for departments and external-directory synchronization, Invitation for invitation lifecycle and consumption, Permission for visibility grants and authorization, Directory for public-service composition, and Workspace only for its independent mutation boundary after each owner contract is reviewed and approved.

The Tenant Knowledge publication handoff is to `tenant_knowledge` for CRUD, isolation, and source facts, Agent and Context for Product Context consumption and source-attribution tests, and Workspace only for its independent mutation boundary after the `tenant_knowledge` owner contract is reviewed and approved.
