# G003 foundation contract

Status: architecture boundary agreed; implementation preflight and owner approval remain pending.

## Scope and authority

The target remains one modular Backend, one SQLAlchemy Base and PostgreSQL database, and one application composition. G003 implements Identity/Tenant, Credential, Model configuration, Agent, Permission, Audit, and minimal Auth. Auth moves to S1/phase 2 under the existing owner; registration, recovery, SSO, invitations and onboarding retain their later product decisions. Run and Context register schema only. Workspace, Tool and Capability Market receive contract review only; their services and schema remain G004. Runner and Agent Loop remain G005.

The controlling decisions are [target architecture](../.agents/notes/proposed/architecture/2026-08-28-target-agent-execution-architecture.md), [login-session authorization](../.agents/notes/proposed/architecture/2026-09-06-login-session-authorization.md), [Credential](../.agents/notes/proposed/architecture/2026-08-31-credential-and-secret-boundary.md), [Model](../.agents/notes/proposed/architecture/2026-08-28-model-system-provider-boundary.md), [Runner](../.agents/notes/proposed/architecture/2026-08-27-agent-runner-lifecycle-and-history.md), and [Context](../.agents/notes/proposed/architecture/2026-08-28-context-source-and-assembly-model.md). Login-session authorization supersedes older live-revocation clauses. No authorization-generation column, Run authorization-dependency table, cancellation sweep, or model-step permission poll is implemented.

This is an implementation contract, not an Agent Note; implementation does not move it or alter its approved hash. Semantic amendments require review and an explicit rebinding of affected ledger references. An approval receipt proves artifact identity and approval execution, not semantic correctness.

## Shared operation boundary

Every owner keeps `models.py` and its repository private. Cross-owner callers use `public.py` typed values and services. Infrastructure provides a TransactionContext around one AsyncSession, selected from control or execution resources by the caller. The outer application operation alone commits or rolls back; repositories flush but never commit. Cancellation rolls back and closes the session. No Model, Provider or Tool network operation holds a transaction open.

Queries require explicit Tenant scope and bounded pagination. Same-Tenant foreign keys use `(tenant_id, id)` targets. UUID identities are global; timestamps are timezone-aware. Closed string alternatives use database CHECKs. Authoritative identities are disabled or archived, not hard-deleted. No legacy data conversion, compatibility lookup or schema repair runs at startup. G003 schema verification creates and drops complete metadata only in disposable PostgreSQL; Alembic remains unavailable until G008.

Authentication and initial authorization occur before a business operation. Owner methods consume the resolved Principal and enforce its fixed scope, including Tenant identity; they do not reread current roles/grants to invalidate a login session. Ordinary resource absence and provider failure remain errors. A passed Principal is a trusted in-process value, never deserialized from arbitrary request fields.

## Identity/Tenant

S0 registers `accounts`, `tenants`, and `memberships`. Account contains ID, enabled state, optional `platform_admin` role, and timestamps. Tenant contains ID, name, enabled state and timestamps. Membership contains ID, Tenant, Account, display name, optional avatar/title, `tenant_admin` or `member`, enabled state, joined and update timestamps. `(tenant_id, account_id)` is unique. Domain/slug, logo management, registration and organizational workflows remain product slices rather than generic fields invented in S0.

Public services create explicit identities/memberships, read identity facts for login, and expose bounded Tenant membership listing and administration. Login resolution checks current Account, Tenant and Membership enabled state. Tenant switching selects another Membership; it never moves one. TenantPrincipal identifies Account, Membership, Tenant and captured Tenant role. PlatformPrincipal identifies Account, platform role and explicit target Tenant and is excluded from ordinary Tenant execution. G003 test provisioning is explicit and never implicit application startup seeding.

## Credential

S1 registers `credentials` with Tenant, at most one Membership/Agent owner, kind/provider/label, encrypted payload, payload/key versions, expiry/revocation metadata and timestamps. Same-Tenant ownership is database-constrained. Public results expose metadata only; private Secret results are usable only by the owning execution adapter. A payload envelope is explicitly versioned; unsupported schemas, unknown keys or authentication failure fail without plaintext fallback. Encryption uses the already installed cryptography package's AES-GCM API, a fresh nonce, and AAD binding Credential ID, Tenant and format version. Keys are injected by composition, never generated as an implicit persistent default.

Secret rotation changes payload bytes under the same identity. Permission edits do not cancel Runs. A missing/deleted Secret or third-party rejection may prevent actual use. Tenant Model bindings allow only Tenant-owned Credentials; Agent capability bindings allow only their documented owner matrix. Membership-Agent-Tool connections are Tool-owned S2 relations, not G003 Credential schema. G003 supplies the typed metadata/owner validation contract for them.

## Model configuration

Model owns `llm_models`, a Tenant default-model relation, and required per-Run `provider_continuation_states`. Models require a same-Tenant Tenant-owned Credential, Provider/model identity, endpoint, validated hard context/output limits, capability source, versioned non-Secret settings, enabled/archive metadata and timestamps. Capability source is provider metadata, built-in catalog or explicit administrator input. Missing hard capabilities prevent enablement. There is no fallback model, step limit or Token quota.

The default is resolved only when creating an Agent that does not explicitly select a Model; Agent then stores its required Model relation. Existing Agent and Run configuration is not changed by updating the default. Model Policy includes private fixed endpoint/settings/Credential references for Snapshot. Model Context Profile exposes only model-visible capabilities. Provider network adapters and actual continuation processing remain G004; continuation schema binds Tenant, Run and Model with explicit payload/encryption versions and no independent TTL.

## Agent and Permission

Agent owns `agents`: ID, Tenant, name/avatar/description/greeting, Soul, timezone, required Model, enabled/archive state, creation attribution and timestamps. It has no execution state or native/OpenClaw discriminator. Its G003 services create and manage the core record through the resolved administrator Principal. Full Workspace and default Tool provisioning is composed with their owners in G004 before offering executable Agent use; no placeholder Workspace or implicit grants are created in G003.

Permission owns Agent visibility policy (`tenant` or `restricted`) and explicit same-Tenant Membership/source-Agent grants. Granting Membership is audit attribution. Permission resolves `none/use/manage` and a secret-free authorization snapshot at login or autonomous execution intake. Tenant administrators retain management authority. Whether an administrator may execute a disabled Agent remains a later product rule; G003 does not expose Run execution. No extra Role/Permission catalogs, ABAC, Approval or live dependency projection is added.

The login snapshot fixes the human Membership role and admitted Agent access; subsequent human role/grant edits do not silently change it. Cross-Tenant access is always outside that scope. Login does not load or freeze all Agent Tool/MCP/Workspace/Skill configuration, and its human access representation must remain bounded. At each new Run's intake, public owner services resolve current Agent-owned execution configuration within the captured human authorization and freeze it in Run Snapshot. G004 supplies those Tool/Workspace/capability services without making Auth or Permission direct writers of their tables. An installation may become available in a new Run without another login, while the active Run keeps its fixed Tool set and discovery indexes; existing Skill package loads retain their documented freshness.

## Minimal Auth

Auth owns local login verifiers and opaque login sessions. It depends on public Identity/Tenant and Permission contracts. It verifies login credentials, resolves enabled identity facts plus authorization once, and returns an opaque random token; persistence stores a token digest, never the raw token. Each session binds Account, Membership, Tenant, captured authorization, creation, expiry and logout metadata. Password verifiers use a salted, versioned standard-library password KDF with bounded input, constant-time verification, and no plaintext logging. Token validation checks login-session validity and expiry, not current Membership roles or capability grants. Logout invalidates that login session without introducing Run cancellation.

Session lifetime is an explicit configuration input; 86,400 seconds is a test/candidate value, not an implicit product default. Absolute versus sliding policy and expiry effects on already-running or waiting executions remain decisions before Run integration. G003 rejects expired login sessions/tokens at its entry boundary and has no Runtime to terminate. Minimal Auth does not implement SSO, public registration, password recovery or a second Account owner.

## Audit

Audit owns append-only `audit_records`, with non-null Tenant, a closed actor union (Membership, platform Account, Agent with optional Run, or named System component), action, target reference, outcome, timestamp and bounded versioned secret-free metadata. Actor field CHECKs reject mixed identities; composite references enforce Tenant consistency and Agent/Run correspondence. Required audit writes share the authoritative mutation's TransactionContext and roll back together; external observability logs are not audit persistence. Queries are Tenant-scoped, permission-scoped and paginated. No update/delete API or dependency on private Agent/Run models is provided.

## Deferred execution schema and contracts

Run S1 schema comprises `agent_runs`, `agent_run_snapshots`, and `agent_run_history`; Context owns replaceable `run_context_projections`. Keys, six statuses, non-null start source identity, same-Agent Parent constraint, one Snapshot per Run, positive ordered History sequence, and explicit payload kinds/versions follow the Runner Note. G003 does not insert fake Running records or execute a Loop. Snapshot stores private non-Secret execution settings; Context consumes only selected visible fields. Unknown authoritative payload versions cannot be silently defaulted. Provider continuation remains Model-owned.

Workspace contract approval preserves Membership/Agent/Group ownership, ordinary message attachments outside Workspace, scoped file references, Memory/Skill discovery, CAS mutation and controlled Skill installation. Tool contract approval preserves Definition/Executor separation, role eligibility, immutable Available Tool Set and ordinary error results. Capability Market approval preserves Tenant registration, explicit per-Agent activation, Agent-specific MCP credentials and bounded install outcomes. These approvals do not claim their G004 implementation details or services are complete.

## Verification and completion

Before domain work, owner bindings and receipts must match the reviewed artifact and public dependency order. G003 verifies complete S0/S1 metadata with real PostgreSQL, constraint failures, Tenant isolation, transaction rollback/cancellation, captured authorization surviving role edits, expired/logout session rejection, Secret roundtrip and wrong-key failure, Model default resolution, Agent core management, and audit atomicity. Each owner has focused tests; cumulative G000-G003 gates and independent code/architecture review precede a completion claim.

Real login-plus-Run/Session workflows, external Providers, Workspace/Tool execution, 50-execution load and browser behavior are not G003 evidence. Their corresponding later gates remain required.
