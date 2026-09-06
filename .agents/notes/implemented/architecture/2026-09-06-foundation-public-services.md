# Agent Note: Model, Agent and Permission public services

Status: implemented — typed foundation services operate on caller-owned transactions; product HTTP and Run composition remain unavailable.

## Problem

The clean-break Backend needs reusable identity and configuration services before Run execution. Reintroducing private cross-module ORM access or recreating permission decisions inside consumers would leave multiple authorities for the same facts.

## Decision

Model, Agent and Permission keep persistence private and expose typed services through `public.py`. They consume [Identity principals](2026-09-06-identity-public-transactions.md) and the caller's [shared transaction](2026-09-06-foundation-schema-and-transactions.md), without accessing another owner's tables.

Model exposes Tenant-administrator configuration management over a same-Tenant Tenant-owned Credential. Provider identity, endpoint, hard context/output limits, capability source, capabilities and non-Secret settings are explicit. Capabilities and settings use configuration version 1, finite JSON values, normalized Secret-field rejection, deep-copy boundaries and owner-enforced limits of 8 levels, 100 items and 16384 encoded UTF-8 bytes. Provider endpoints require HTTP(S) with a host and reject URL user information and explicitly Secret-bearing query parameters. Model enablement requires explicit Tool Calling support. The Tenant default is resolved only when creating an Agent without an explicit Model; the Agent stores the resolved Model ID and later default changes do not rewrite it. Model archival disables and retains the record. Provider execution, fallback Models, quotas and Model-step limits remain absent.

Agent exposes Tenant-administrator creation, read, bounded listing, update, enablement and archival for the core Agent record. Creation requires a selectable Model, non-empty Soul and valid IANA timezone. Optional avatar, description and greeting fields distinguish omission from explicit `None`, which clears the field. Archival disables and retains the Agent. Agent creates no Workspace, Tool, capability installation or visibility grant. Permission reads only bounded, explicitly Tenant-scoped Agent metadata through Agent's public contract.

Permission owns `tenant` and `restricted` Agent visibility plus retained, revocable same-Tenant Membership and source-Agent grants. It resolves `none`, `use` or role-derived `manage`, and is the only owner that captures admitted Agent IDs into a login Principal. Administrators retain role-derived all-Agent management without enumerated IDs. Member capture contains at most 1000 active visible Agent IDs and scans at most 10000 visibility rows; overflow fails explicitly without truncation. Grant changes do not mutate an already captured Principal. Autonomous intake resolves current same-Tenant source-Agent visibility, while Runner/model steps perform no live reauthorization, generation projection or cancellation sweep.

## Alternatives considered

**Cross-owner ORM imports.** Rejected because consumers would become coupled to private schema and could bypass the owner's mutation and Tenant rules.

**Live role checks in every operation.** Rejected by the accepted login-session authorization decision; human scope changes apply at the next login.

**Provider-specific settings choices in G003.** Rejected because Provider adapters and their exact request options remain G004 work. Model configuration instead accepts one bounded, versioned, finite, non-Secret JSON object without claiming support for unimplemented Provider options.

## Consequences

Public provisioning and configuration methods are trusted application ports, not unauthenticated product endpoints. Transport must authenticate callers before constructing Principal values. G003 service verification does not authorize HTTP exposure, registration, SSO, Workspace/Tool provisioning, Provider execution or Run execution.

## Verification

Model tests verify the Credential owner matrix, explicit/default binding, enable/archive rules, version rejection, JSON bounds at and above their limits, non-finite/non-JSON rejection, Secret-field normalization, deep-copy isolation and endpoint Secret handling. Agent tests verify creation-time default resolution, Soul/timezone validation, optional-field clearing and retained archival. Permission tests verify cross-Tenant denial, human and autonomous grants, fixed authorization after grant edits, role-derived administrator scope, retained revocation and explicit capture overflow. Browser, product API, Provider and execution workflows remain later-stage evidence.
