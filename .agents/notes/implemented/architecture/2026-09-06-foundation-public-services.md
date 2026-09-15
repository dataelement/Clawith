# Agent Note: Model, Agent and Permission public services

Status: implemented — typed foundation services operate on caller-owned transactions; Run composition uses these services and product HTTP remains later-stage work.

## Problem

The clean-break Backend needs reusable identity and configuration services before Run execution. Reintroducing private cross-module ORM access or recreating permission decisions inside consumers would leave multiple authorities for the same facts.

## Decision

Model, Agent and Permission keep persistence private and expose typed services through `public.py`. They consume [Identity principals](2026-09-06-identity-public-transactions.md) and the caller's [shared transaction](2026-09-06-foundation-schema-and-transactions.md), without accessing another owner's tables.

Model exposes Tenant-administrator configuration management over a same-Tenant Tenant-owned Credential. Provider identity, endpoint, hard context/output limits, capability source, capabilities and non-Secret settings are explicit. Capabilities and settings use configuration version 1, finite JSON values, normalized Secret-field rejection, deep-copy boundaries and owner-enforced limits of 8 levels, 100 items and 16384 encoded UTF-8 bytes. Provider endpoints require HTTP(S) with a host and reject URL user information and explicitly Secret-bearing query parameters. Enabled configuration requires matching [Model execution acceptance](2026-09-07-model-execution-implementation.md), including its explicit protocol and verified Tool Calling behavior. The Tenant default is resolved only when creating an Agent without an explicit Model; the Agent stores the resolved Model ID and later default changes do not rewrite it. Model archival disables and retains the record. Fallback Models, quotas and Model-step limits remain absent.

Agent exposes Tenant-administrator creation, read, bounded listing, update, enablement and archival for the core Agent record. Creation requires a selectable Model, non-empty Soul and valid IANA timezone. Optional avatar, description and greeting fields distinguish omission from explicit `None`, which clears the field. Archival disables and retains the Agent. Agent creates no Workspace, Tool, capability installation or visibility grant. Permission reads only bounded, explicitly Tenant-scoped Agent metadata through Agent's public contract.

Permission owns `tenant` and `restricted` Agent visibility plus retained, revocable same-Tenant Membership and source-Agent grants. It resolves `none`, `use` or role-derived `manage`, and is the only owner that captures admitted Agent IDs into a login Principal. Administrators retain role-derived all-Agent management without enumerated IDs. Member capture contains at most 1000 active visible Agent IDs and scans at most 10000 visibility rows; overflow fails explicitly without truncation. Grant changes do not mutate an already captured Principal. Autonomous intake resolves current same-Tenant source-Agent visibility, while Runner/model steps perform no live reauthorization, generation projection or cancellation sweep.

## Alternatives considered

**Cross-owner ORM imports.** Rejected because consumers would become coupled to private schema and could bypass the owner's mutation and Tenant rules.

**Live role checks in every operation.** Rejected by the accepted login-session authorization decision; human scope changes apply at the next login.

**Provider-specific settings choices in G003.** Rejected because Provider adapters and their exact request options remain G004 work. Model configuration instead accepts one bounded, versioned, finite, non-Secret JSON object without claiming support for unimplemented Provider options.

## Consequences

Invalid IANA timezone names and invalid timezone path forms both become the same bounded `InvalidInput`; library `ValueError` and raw path input do not escape the Agent service. Creation and update use the same validation boundary.

`AgentService.get_for_execution` supplies Agent configuration to an already authorized human execution path. It checks the captured Principal's Agent IDs or administrator scope, Tenant identity and Agent availability without granting management access or refreshing login permissions. Tool capture and personal-account binding use this execution read, while administrative `get` remains administrator-only. Ordinary members can execute a visible Agent without acquiring its management API.

`get_for_agent_execution(tenant_id, agent_id)` is the trusted autonomous intake read. It requires an active, unarchived Agent in the explicit Tenant without constructing a human administrator Principal. The product owner must already authorize the autonomous source; this read does not grant A2A visibility or management rights. `require_execution_ids` validates a captured human target set in one Agent query, rejecting more than 1001 supplied IDs before SQL. Missing, disabled, archived or other-Tenant targets fail the entire batch; unauthorized IDs fail before database access. Neither helper refreshes human Permission scope or adds a Runtime authorization layer.

Public provisioning and configuration methods are trusted application ports, not unauthenticated product endpoints. Transport must authenticate callers before constructing Principal values. G003 service verification does not authorize HTTP exposure, registration, SSO, Workspace/Tool provisioning, Provider execution or Run execution.

## Verification

Model tests verify the Credential owner matrix, explicit/default binding, enable/archive rules, version rejection, JSON bounds at and above their limits, non-finite/non-JSON rejection, Secret-field normalization, deep-copy isolation and endpoint Secret handling. Agent tests verify creation-time default resolution, Soul/timezone validation, optional-field clearing and retained archival. Permission tests verify cross-Tenant denial, human and autonomous grants, fixed authorization after grant edits, role-derived administrator scope, retained revocation and explicit capture overflow. Browser, product API, Provider and execution workflows remain later-stage evidence.

Independent Agent intake tests use PostgreSQL to verify autonomous lookup without administrator authorization, wrong-Tenant and inactive rejection, one SQL statement for a 21-Agent target batch, the inclusive batch limit and pre-SQL overflow/visibility rejection. These owner checks do not establish any autonomous product source's permission policy.
