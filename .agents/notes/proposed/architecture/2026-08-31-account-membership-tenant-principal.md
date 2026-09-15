# Agent Note: Account, Membership, Tenant, and Principal

Status: proposed — the clean-break identity and Tenant boundary required by the first Backend implementation is agreed but not implemented

Authorization timing follows [Login-Session Authorization](2026-09-06-login-session-authorization.md). Auth owns login expiry, with twenty-four hours as a candidate; Account, Membership, Tenant and Principal ownership remains unchanged.

## Problem

The current Backend approximates one global natural person plus one Tenant-specific User row, but nullable Tenant membership, global and Tenant roles, authentication data, quotas, compatibility proxies, and product profile are mixed across `Identity` and `User`. The target needs one unambiguous identity for login, one unambiguous identity for Tenant product participation, and one request contract consumed by product and Runtime code without completing the later Auth, SSO, and organization-sync redesign first.

An ambiguous "User" owner is also unsafe for Workspace, Session, Group, Agent creation, Credential, and Audit references. A global Account Workspace would cross Tenant boundaries, while a Tenant Membership Workspace preserves the required isolation.

## Proposal

### Tenant

Tenant is the mandatory business isolation boundary. Membership, Agent, Group, Session, Workspace, Credential, Tool Grant, Model, and other Tenant business facts carry a non-null Tenant relation. Cross-Tenant relations are denied at their real read or mutation boundary and use composite database constraints where the relationship itself must remain in one Tenant.

### Account

Account is one global natural person and authentication subject. It owns global enabled state and an optional platform role but no Tenant role, Tenant profile, Workspace, Session, Group membership, Agent permission, or other Tenant product fact. An Account may temporarily have no Membership during registration or company setup.

Email, phone, password, OAuth identity, SSO identity, login session, verification, recovery, and token issuance belong to the later Auth and Identity Provider design. Agent Runtime depends only on the resolved Account identity and never authenticates those methods itself.

### Membership

Membership is one Account's product identity in one Tenant:

```text
Membership
  - id
  - tenant_id
  - account_id
  - display_name
  - avatar
  - title
  - role
  - enabled
  - joined_at
  - updated_at
```

`tenant_id` and `account_id` are non-null, and `(tenant_id, account_id)` is unique. The first Tenant role set is `tenant_admin` and `member`. Platform administration belongs to Account and does not become a Tenant role. Product language may call Membership a User, but code, persistence, authorization, and cross-layer contracts use Membership when identity matters.

The target has no nullable-Tenant Membership, mixed platform/Tenant role enum, identity association proxy, Membership password or contact proxy, registration compatibility field, or Membership-owned Agent, Trigger, Token, or message quota counter.

### Principal

Principal is an authenticated request value and a closed union, not a table:

```text
Tenant Principal
  - account_id
  - membership_id
  - tenant_id
  - tenant_role

Platform Principal
  - account_id
  - platform_role
  - target_tenant_id
```

At login, Auth selects one Membership, reads current Account, Membership and Tenant enabled state and roles, and resolves the human authorization scope through Permission. Subsequent requests derive Tenant Principal from the valid login session without refreshing those permissions. Platform operations use their authenticated platform identity and explicit target Tenant without fabricating a Membership. Product intake resolves Agent-owned execution configuration before Runner receives its fixed Run scope.

Platform Principal is accepted only by explicit platform-administration application services and cannot create an ordinary Session, enter an Agent Run, read a Membership or Group Workspace, or become model-visible Context. Platform administration uses its audited target Tenant without gaining ordinary Tenant product identity. Ordinary Agent use always requires Tenant Principal for a Membership in the target Tenant.

### Tenant switching and references

One Account may have multiple Memberships. Tenant switching selects another Membership and request context; it never changes an existing Membership's Tenant or shares Tenant roles, Sessions, Groups, or Workspaces.

Target references use Membership rather than Account for Tenant product identity:

```text
Direct Session owner ----> Membership
Group member ------------> Membership
Agent created_by --------> Membership, audit only
Membership Credential ---> Membership
User Workspace ----------> Membership
```

Same-Tenant relations use `(tenant_id, membership_id)` foreign keys where appropriate. Historical references survive Membership disablement rather than being silently reassigned.

### Audit attribution

Audit records always carry a non-null target Tenant and one closed actor kind: `membership`, `platform_account`, `agent`, or `system`. A Membership actor identifies an ordinary human Tenant operation and must belong to the target Tenant. A Platform Account actor is valid only for an explicit platform-administration operation against the recorded target Tenant; it does not require or create a Membership. An Agent actor belongs to the target Tenant and may carry its same-Tenant Run reference so the initiating product input remains traceable. A System actor carries a bounded internal component identity and is used only for non-human platform operations such as bootstrap or lifecycle cleanup.

The audit row contains exactly the identity required by its actor kind: Membership, Account, Agent, or no database identity for System. A database `CHECK` rejects mixed or missing actor fields, and composite foreign keys enforce same-Tenant Membership, Agent, and Run references. The applicable resolved authorization scope is enforced before the audited mutation; the immutable audit actor remains historical attribution after a role, Membership, Agent, or Account is disabled or changed.

An Agent Run is audited as the Agent actor with its Run reference rather than as System or as a fabricated Membership. Direct and Group Run origin remains discoverable through the Run Snapshot and product input relation. Platform administration is audited as the global Account against an explicit Tenant and never borrows a Tenant Membership merely to satisfy the audit schema. Audit metadata is versioned, bounded, and Secret-free; it does not duplicate product or Run History payloads.

### Membership Workspace

The product term User Workspace means Membership Workspace. Each Membership owns exactly one Workspace in its Tenant, keyed by Tenant and Membership identity. The same Account's Memberships in different Tenants have different Memory, Skills, and Files. A Run never imports another Membership Workspace because the Account identity matches.

### Disablement

Account disablement is considered on subsequent login across its Memberships; Membership disablement applies only to its Tenant, and Tenant disablement prevents subsequent login into that Tenant. Existing login scopes and Runs are not cancelled by permission changes. Historical references remain intact. Auth expiry and explicit logout have their own login-session semantics.

## Alternatives considered

### Keep `Identity` and `User` names

Their current behavior approximates Account and Membership but their names and compatibility proxies obscure whether a caller refers to a global person or Tenant product identity. The clean-break target uses explicit names in code and persistence while retaining "User" only as product language.

### Put Tenant directly on Account

One natural person may join more than one Tenant. A Tenant-scoped Account would duplicate login identity and prevent explicit Tenant switching, while a mutable Account Tenant would mix otherwise isolated product facts.

### Give Account one global User Workspace

A global Workspace would allow an Agent authorized in one Tenant to observe the same person's private Memory or files from another Tenant. Workspace belongs to Membership.

### Store Principal

Principal is an authenticated union derived from the login session and assembled from Account and either Membership/Tenant role facts or platform role plus an explicit target Tenant. Persisting it would duplicate those authorities and duplicate login-session ownership.

## Acceptance criteria

- Account is the global natural person and authentication subject; Membership is the non-null Tenant product identity.
- One Account may have multiple Memberships, with at most one Membership per Tenant.
- The first Tenant roles are only `tenant_admin` and `member`; platform role belongs to Account.
- Product "User" means Membership, and all Tenant product foreign keys use Membership where the actor or owner is a user.
- Principal is a closed non-persisted union: Tenant Principal contains Account, Membership, Tenant, and Tenant role; Platform Principal contains Account, platform role, and an explicit target Tenant without Membership.
- Platform Principal enters only explicit platform-administration services and never ordinary Session, Agent Run, Workspace, or model Context.
- Tenant switching selects another Membership without moving or mutating an existing Membership's Tenant.
- User Workspace is one Membership Workspace keyed by Tenant and Membership; the same Account never shares it across Tenants.
- Direct Session, Group membership, Agent creation audit, personal Credential, and User Workspace reference Membership.
- Audit records always name a target Tenant and exactly one Membership, Platform Account, Agent, or System actor; platform administration never requires a fabricated Membership, and Agent actions retain their Run relation.
- Disablement affects subsequent authentication within its documented scope; existing login permissions and Runs follow the login-session authorization decision.
- Auth method, SSO, external identity, token, recovery, and organization-sync details remain outside this first identity boundary.

## Risks and open questions

Exact Account authentication tables, platform-role representation, token and login-session format, invitation and registration flow, profile edit ownership, Account merge, Membership removal, and external Directory Member linkage remain decisions for the later Auth, Permission, and Organization architecture. They must preserve this Account, Membership, Tenant, Principal, and Workspace boundary.
