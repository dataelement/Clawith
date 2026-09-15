# Agent Note: Minimal RBAC and Agent Visibility

Status: proposed — the first-release Permission owner and Agent visibility contract is agreed but not implemented

Authorization timing is owned by [Login-Session Authorization](2026-09-06-login-session-authorization.md): human access is fixed for a login session, while Agent-owned execution configuration is resolved at each new Run. Login expiry is required; twenty-four hours remains a candidate.

## Problem

Agent discovery, Session creation, A2A targeting, Agent Workspace preview, Capability installation, protected execution, all require one consistent permission boundary. Deferring the entire Permission domain would force each caller to invent its own Tenant and visibility checks and could allow a known Agent or Workspace identity to bypass discovery restrictions.

The first release needs only basic Tenant roles, Agent visibility, explicit Agent audience relations, one resolver and login-scoped human authorization. It does not need custom roles, Department ACL, ABAC, Approval, per-file policy, or a generic policy engine.

## Proposal

### Roles

Account may hold platform administration. Membership role is the closed set `tenant_admin` or `member` defined by [Account, Membership, Tenant, and Principal](2026-08-31-account-membership-tenant-principal.md).

Platform administrator performs explicit audited platform operations against a target Tenant and does not automatically receive that Tenant's ordinary Agent Context. Its audit actor is the global Account and does not require a fabricated target-Tenant Membership. Tenant administrator manages Tenant Memberships, Agents, Soul, Model, Tool and MCP grants, Market, Credential, Channel, Agent visibility, and Agent Workspace preview. Member may use and preview only visible Agents and its own Membership Workspace. The first release allows only Tenant administrator to create and manage Agent; Agent creator identity remains audit only.

### Agent visibility

Agent visibility is `tenant` or `restricted`. An enabled `tenant` Agent is visible and usable to active Memberships and Agents in the same Tenant. A `restricted` Agent is visible and usable only to explicitly granted same-Tenant Memberships or source Agents. Tenant administrator always receives management access.

`agent_visibility_grants` contains Tenant, target Agent, exactly one subject Membership or source Agent, granting Membership, revocation timestamp, and timestamps. A `num_nonnulls(subject_membership_id, subject_agent_id) = 1` check enforces exactly one subject. Separate partial unique indexes for Membership and Agent subjects enforce one effective target-subject relation without nullable uniqueness gaps. Composite foreign keys enforce that target, subject, and granting Membership belong to the recorded Tenant. A private product preset may create a restricted Agent plus one explicit Membership grant; it does not add another persistence mode.

### Permission Resolver

One Permission Resolver returns `none`, `use`, or `manage` for a Tenant Principal or Agent subject and target Agent. At authorization resolution it checks the subject, Tenant and applicable visibility grants. Platform Principal is excluded from ordinary Agent use. Tenant administrators retain management access; execution of disabled Agents and other administrator exceptions are product rules to settle during Permission implementation. The first release has no Agent-specific manage grant.

Agent list/search, Session creation, A2A target discovery, Agent Workspace preview, Run start and Capability installation consume the resolved scope. Backend entrypoints validate login-session validity and enforce its Tenant and admitted Agent scope without refreshing human permissions during that login. The owning intake resolves Agent configuration once per new Run. Caller-supplied identifiers, UI visibility and Prompt text cannot expand scope. Workspace does not store another visibility ACL.

### Capability installation

Agent may install a Market item only when its immutable Available Tool Set contains the explicitly granted `install_capability` Builtin. Installation may create or reuse Tenant Catalog data and may create only the executing Agent's connection and grants. It cannot grant another Agent, expose another Agent's Credential, disable shared Tenant items, or perform Tenant-admin mutations. Approval remains deferred; the first release evaluates basic allow or deny only.

### Authorization lifetime

Human identity, roles and admitted Agent access are fixed at login and refreshed on a subsequent login. Agent-owned Model, Tool/MCP bindings, Workspace scope and Skill indexes are resolved at each new Run under that human scope, then fixed for the Run. Autonomous inputs resolve receiver authorization at their own intake. Runner consumes the resolved scope without live permission revalidation.

The first release has no authorization-generation columns, Run authorization-dependency projection, revocation sweep or automatic Run cancellation on permission changes. Explicit Run cancellation and parent-child terminal cancellation remain Runner behavior. Missing resources and external credential rejection return their ordinary owned errors. Auth owns login-session expiry; its exact policy remains implementation work.

## Alternatives considered

### Defer all Permission work

The first release already exposes Agent, A2A, Workspace, Tool, Credential, and Market operations. Without one resolver those consumers would implement divergent security rules.

### Add generic Role and Permission tables

Two Tenant roles and one Agent visibility relation satisfy current consumers. Generic role assignment, permission catalogs, inheritance, and policy evaluation add unused choices and are deferred.

### Put use and manage levels on every Agent grant

The first release derives manage only from Tenant administrator and use from visibility. A per-Agent manage grant would create another role system without a current need.

### Treat Tenant equality as Agent visibility

Some Agents must be restricted within a Tenant. Tenant equality is necessary but does not authorize discovery, use, A2A, or Workspace preview by itself.

## Acceptance criteria

- The first-release Membership roles are only `tenant_admin` and `member`; platform role belongs to Account.
- Agent visibility is only `tenant` or `restricted`, with same-Tenant Membership or Agent grants for restricted visibility.
- Permission Resolver returns `none`, `use`, or `manage` and is the common decision for discovery, Session, A2A, Workspace preview, Run start, and Capability installation.
- Tenant administrator is the only Agent manager in the first release; Agent creator is audit only and no Agent manage grant exists.
- A known Agent or Workspace identity cannot expand the resolved scope; Backend boundaries preserve Tenant isolation and valid login-session identity.
- `install_capability` permits one Agent to install only for itself and never grants Tenant administration or another Agent's Credential.
- Human permissions remain fixed during the login session; Agent execution configuration is refreshed only for a new Run.
- No live revalidation, authorization-generation projection or revocation-triggered Run cancellation is required.
- Approval, custom roles, Department ACL, ABAC, per-file ACL, policy engine, and complex permission inheritance remain deferred.

## Risks and open questions

Exact permission interfaces, bounded scope representation, visibility storage, administrator exceptions and login expiry integration are settled during implementation under the login-scoped contract.
