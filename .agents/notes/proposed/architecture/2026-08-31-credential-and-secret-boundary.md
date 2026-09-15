# Agent Note: Credential and Secret Boundary

Status: proposed — the first-release credential ownership and execution boundary is agreed for the clean-break Backend but is not implemented

## Problem

The current Backend stores third-party authentication material in LLM rows, Channel columns, Tool and Tenant JSON configuration, Identity Provider configuration, Agent browser-cookie records, environment settings, and capability-specific helpers. Encryption and plaintext fallback differ by path, and some execution code reads Secret-bearing configuration directly. This prevents one enforceable rule for Tenant scope, Agent authorization, redaction, rotation, revocation, upgrade, and model-visible data.

The first target release needs one small Credential boundary without introducing a generic Connection platform, Secret Manager service, approval workflow, fine-grained OAuth Scope engine, or multi-level inheritance system.

## Proposal

### One Credential table

All product-managed Secret material enters one `credentials` table:

```text
Credential
  - id
  - tenant_id
  - membership_id, optional
  - agent_id, optional
  - kind
  - provider
  - label
  - schema_version
  - encrypted_payload
  - encryption_key_version
  - expires_at
  - revoked_at
  - created_at
  - updated_at
```

Every Credential belongs to one Tenant. A row with neither optional owner is Tenant-owned; a row with `membership_id` is personal to that Tenant Membership; a row with `agent_id` belongs to that Tenant Agent. A `num_nonnulls(membership_id, agent_id) <= 1` check forbids both optional owners, and composite foreign keys enforce that the selected Membership or Agent belongs to the same Tenant. Group does not own Credential in the first release.

Platform infrastructure Secrets, including database and Redis credentials, JWT signing material, the Credential encryption master key, and internal service authentication, remain in deployment Secret configuration. The first release has no database-managed platform-global Credential.

The target database URL remains a `SecretStr` throughout Settings validation, representation, serialization, and application composition. `reveal_database_url` is the one typed reveal boundary: it converts the Secret into SQLAlchemy's password-masking `URL` value. Application engine construction and Alembic connection construction are the two authorized consumers of that value. Alembic stores only the masked rendering in its configuration diagnostics. Its failure boundary distinguishes connection setup, migration execution, and disposal cleanup while retaining only a safe category, exception class, validated SQLSTATE, and validated revision when available; it never copies the original message, URL, password, SQL, or provider payload into diagnostic fields and suppresses the original exception chain from CLI output. Cleanup failure is attached to an existing connection or migration failure rather than replacing that primary diagnostic. Validation may inspect the same typed URL but never includes the input or a chained parser error in its diagnostic. The earlier commit directive that limited `get_secret_value` to application engine construction was too narrow; Alembic is an equally necessary database connection owner, while direct Secret revelation outside `reveal_database_url` remains unauthorized.

### Capability-owned references

Credential stores authentication material, not Tool, Model, Channel, SSO, or browser business behavior. The owning product table stores non-Secret configuration and references `credential_id` directly:

```text
LLM Model ------------> Credential
Agent Tool Grant -----> Credential, optional for non-MCP Tool
Agent MCP Connection -> Agent-owned default Credential, optional for unauthenticated services
Channel Config -------> Credential
Identity Provider ----> Credential
Agent browser account -> Agent-owned Credential
```

No generic Connection or Credential Grant table is introduced. Agent MCP Connection is a concrete capability owner required for one Agent's Token against one Tenant-shared MCP item. An Agent Tool Grant identifies the non-MCP Tool for which its optional Credential may be used or references the same Agent's MCP Connection. Model, Channel, Identity Provider, browser, and MCP capabilities own their binding semantics. A Credential reference never authorizes a different capability merely because the Secret could technically authenticate it.

The first release uses a closed Credential-owner compatibility matrix:

```text
Tenant LLM Model -----------------> Tenant Credential
Agent non-MCP Tool Grant ---------> Tenant Credential or same-Agent Credential
Agent MCP default Connection -----> same-Agent Credential when authentication is required
Agent browser account ------------> same-Agent Credential
Membership-Agent-Tool Connection -> same-Membership Credential
Agent Channel Config -------------> Tenant Credential or same-Agent Credential
Tenant Identity Provider ---------> Tenant Credential
```

Same Tenant alone never satisfies an incompatible owner kind. Each capability binding persists the expected Credential owner kind and identity and enforces it with a binding-specific database check and composite foreign key to the Credential ownership key. Membership Credential cannot be placed on a shared Model, Agent Tool Grant, MCP connection, browser account, Channel, or Identity Provider. A Channel related to one Agent may use a Tenant Credential or that same Agent's Credential. A Tenant Identity Provider may use only a Tenant Credential.

Membership-owned Credential is supported through an explicit `membership_agent_tool_connections` relation containing Tenant, Membership, Agent, Tool Definition, Credential, non-Secret label and capability metadata, enabled state, and timestamps. It means that Membership authorizes that Agent to use that personal account for that Tool under an eligible Run; it never becomes an Agent's shared Credential. This relation also supports personal MCP invocation without storing Membership Credential on the Agent's shared MCP connection. MCP uses the Agent account by default; selecting a personal account requires an explicit user request, an authorized Membership connection and an eligible resolved task scope. The model receives only stable non-Secret connection references, owner kind, label, and capabilities. Credential failure never falls back to an Agent, Tenant, or another Membership account.

An MCP service that does not require authentication needs no Credential. Platform Agent grants still apply. Account-specific Tool discovery is resolved using the selected connection; one account's discovery is not authorization for another account and cannot silently redefine its Tools.

### Run and execution boundary

Run Snapshot may store the authorized capability or Tool Grant identity and its Credential reference, but never plaintext Secret, ciphertext, access token, encryption key, or Secret-store location. Direct Run resolves current Membership, Agent, and Tenant connections. Group and Agent-owned Runs resolve Agent and Tenant connections by default. Subagent Runs inherit the Parent Main Run's exact resolved connection set.

Heartbeat, Trigger, and A2A never acquire a Membership's personal Credential implicitly. They may use an explicitly selected Membership connection only when an authenticated Membership durably authorizes the exact product owner record, target Agent, Tool or capability, and scope. Trigger stores selected connection references on its configuration for its occurrences; Heartbeat stores them on its configuration; one A2A Request carries one request-scoped delegated reference for its target Run. The receiving Agent gets only the reference and execution authorization, never Token bytes, and cannot retain it as Agent Credential or reuse it in another Run.

At the real Provider, Tool, Channel or Identity execution boundary, the owning executor uses the pre-resolved Tenant, Credential owner and binding from the authorized scope, decrypts only for the external call, and excludes Secret material from Context, Run History, Tool Results, Workspace and ordinary logs. It does not poll current grants to invalidate a login session or Run. An unavailable, expired, undecryptable or externally rejected Credential produces a bounded non-Secret error; fixed authorization does not guarantee the resource remains usable.

### Rotation and revocation

Run fixes non-Secret route configuration and authorized binding references, not Secret bytes. Updating the encrypted payload rotates the Secret under the same Credential and affects the next external use without rewriting Snapshot or History. Administrative permission changes follow the login-scoped policy and do not trigger Run cancellation. New Runs resolve current Agent-owned connection configuration; historical references remain inspectable without exposing removed Secret material.

OAuth refresh and browser-cookie capture may update only the Credential owned by their capability. They do not grant a Run or Agent general Credential mutation access.

### Encryption and upgrade

Credential payload uses authenticated encryption such as AES-GCM. The encryption key remains outside the database. Each row records payload schema and encryption-key versions. There is no plaintext fallback: authentication failure, an unknown key version, or an unsupported payload version is a configuration failure.

API responses never return stored Secret material after acceptance; they return only non-Secret metadata and an optional mask. Key rotation keeps old decryption keys available while a verified maintenance operation re-encrypts rows to the new key version, then removes the old key only after every row is accounted for. Future target upgrades preserve and losslessly decode or deliberately migrate every authoritative Credential payload under the repository-wide forward-upgrade contract.

## Alternatives considered

### Keep Secrets in each owning product table

This would preserve inconsistent encryption, masking, fallback, rotation, and execution paths and leave Secret-bearing Tool or Channel configuration outside one enforceable boundary.

### Add generic Connection and Credential Grant tables

The first release already has Model, Tool Grant, Channel, Identity Provider, and browser owners that define what one Credential can do. Generic Connection and Grant records would duplicate those relationships and introduce a broad optional-field protocol before another consumer exists.

### Store platform-global product Credentials in the database

The first release can provide shared platform services through deployment Secret configuration. Adding another database owner and cross-Tenant grant model is deferred until a real administered platform Credential is required.

### Accept plaintext when decryption fails

Fallback makes corruption, key mismatch, and unencrypted legacy data indistinguishable and can silently expose or use unintended material. The clean-break target fails closed and has no plaintext compatibility path.

## Acceptance criteria

- Every database-managed product Secret is stored only in `credentials`; Model, non-MCP Tool Grant, Agent MCP Connection, Channel, Identity Provider, and browser records contain only non-Secret configuration plus a Credential reference.
- Every Credential has one Tenant and at most one Membership or Agent sub-owner, with database-backed same-Tenant constraints.
- Tenant Model, Agent Tool Grant, Agent MCP, Agent browser, Membership Tool connection, Agent Channel, and Tenant Identity Provider enforce the closed owner compatibility matrix; same-Tenant Membership Credential cannot enter a shared Agent or Tenant binding.
- Agents receive capability-owned bindings rather than a generic right to use a raw Credential.
- Membership Credential is available through an explicit Membership-Agent-Tool connection and only in the current Direct Run unless Heartbeat, Trigger, or A2A owner records a narrower authenticated Membership delegation.
- MCP defaults to the Agent account; personal MCP use additionally requires explicit user selection, Membership connection authorization and eligible task scope, without changing the Agent default binding.
- MCP authentication is optional only when the service does not require it; platform Agent grants remain necessary and account-specific discovery cannot broaden them.
- Run Snapshot and History contain only authorized references and never contain plaintext Secret, ciphertext, token, encryption key, or Secret-store location.
- Executors consume the pre-resolved Tenant and binding scope, decrypt only at the external boundary, and report actual Credential unavailability without live permission revalidation.
- Subagents inherit Parent grants; Group and autonomous work never gain Membership Credential implicitly; Heartbeat, Trigger, and A2A may carry only explicitly selected, product-scoped Membership connection references.
- Secret rotation affects later use without rewriting Run facts; permission edits do not trigger cancellation, while actual Credential failure remains explicit.
- Credential payload uses authenticated encryption with explicit payload and key versions, has no plaintext fallback, and remains losslessly readable across supported target upgrades.
- Deployment infrastructure Secrets remain outside the database, and the first release adds no platform-global Credential, generic Connection, generic Credential Grant, KMS integration, approval workflow, Scope engine, or Credential usage-history subsystem.
- Database Settings representations, dumps, validation errors, Alembic configuration diagnostics, and Alembic failures do not expose the database password; application and Alembic engines receive the same typed SQLAlchemy URL through `reveal_database_url`.

## Risks and open questions

Exact Credential kinds and typed payload schemas, ciphertext representation, AEAD library call shape, master-key loading, key-rotation command, OAuth refresh concurrency, masking format, dependent-Run lookup, and domain-table foreign keys remain implementation decisions under this fixed ownership and failure contract.
