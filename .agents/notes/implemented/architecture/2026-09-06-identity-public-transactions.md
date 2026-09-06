# Agent Note: Explicit Identity provisioning and captured principals

Status: implemented — Identity/Tenant exposes public services with caller-owned transactions.

## Problem

Foundation consumers need Tenant-scoped identities without acquiring another owner's private persistence or rebuilding human authorization from live role queries.

## Decision

Identity owns Account, Tenant, Membership, their identity/role views and the shared captured Principal value. Public provisioning is trusted application work and never implicit startup seeding. Login identity resolution checks current enabled facts. Administrative operations consume a captured administrator Principal and constrain queries and mutations by Tenant. They flush but do not commit; the outer transaction publishes the operation.

The shared Principal carries admitted Agent IDs but Identity does not resolve them. Permission supplies that scope; Auth persists and decodes it. Role edits and disablement preserve records and do not mutate already issued principals. Platform principals target a Tenant explicitly and cannot pass ordinary Tenant authorization helpers.

## Alternatives considered

**Cross-owner table access.** Rejected because identity constraints and mutations need one authority.

**Live permission re-resolution in every service.** Rejected by the accepted login-session lifetime.

## Consequences

These methods are not public registration or unauthenticated HTTP endpoints. Transport must establish the caller before using a Principal. The service adds no Account recovery, SSO or organization workflow.

## Verification

Real PostgreSQL tests cover Membership uniqueness, cross-Tenant reads/mutations, disabled identity rejection, captured scope after role changes and public Membership metadata lookup. Transaction tests observe pre-commit invisibility, commit, rollback, cancellation and released connections. Product HTTP and browser integration remain deferred.
