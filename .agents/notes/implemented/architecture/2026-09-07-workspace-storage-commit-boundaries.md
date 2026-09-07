# Agent Note: Workspace storage commit boundaries

Status: implemented — Workspace conditional mutation, Skill publication and application resource composition are implemented; Run consumers remain G005 work.

## Problem

Concurrent file writes must not overwrite a newer revision. Skill publication must expose complete packages without holding business transactions during filesystem or network work. Local directories and S3 prefixes have different mutation primitives.

## Decision

Ordinary content uses storage-owned revisions and conditional replacement through the [storage primitives](2026-09-07-storage-resource-primitives.md). Workspace does not write a second database revision after the storage fact commits. Permission scope selects the readable spaces and output direction; humans remain preview-only. The Main-only distillation operation changes generalized Agent Memory without granting arbitrary Agent file or Skill writes.

Workspace directory operations capture a bounded manifest of paths and revisions. Copy and deletion check individual file revisions and return explicit copied, deleted, remaining and uncertain paths. A multi-file operation is not an atomic tree transaction. A partial copy does not authorize deleting uncopied source files. Cleanup removes only empty directories or S3 markers and never blindly deletes concurrently introduced children. Oversized manifests fail before mutation.

Skill publication prepares complete content before committing package metadata and bindings. Reader/publication locks protect the selected current package from concurrent cleanup. Shared-package mutation requires the same administrator authority on every public path; Agent self-install does not authorize shared refresh. A private update affects only its Agent binding.

Skill discovery fixes identities for a Run. Explicit loads of those identities resolve the currently published complete package; new installation changes the next discovery. Catalog-backed discovery uses an injected bounded source-availability query in the existing transaction. Existing discovery is not reauthorized through current Catalog enablement during each load.

## Alternatives considered

Blind recursive deletion was rejected because directory metadata does not capture concurrent child-content changes. Treating a Move as all-or-nothing was rejected because destination publication can succeed before source removal conflicts. Git/history and a transactional virtual filesystem remain outside the approved design.

## Consequences

File and package preparation never holds a business database transaction. Audit observes committed facts without deciding the result. Directory operations expose partial outcomes instead of promising atomic tree replacement. Package cleanup failure remains an explicit outcome, not a rollback of successful publication.

## Verification and gaps

Tests cover file CAS, reader-safe package publication, shared/private behavior, member denial, source disappearance after destination publication, bounded directory manifests, concurrent changed/new files, partial copy, nested locks, cancellation and unrelated-resource progress. Local tests and controlled S3 responses do not establish live S3 behavior or a deployment-shaped connection-pool configuration.

The [G004 contract](../../../../specs/backend-execution-dependencies.md) remains authoritative. [Application lock-pool composition](2026-09-07-application-execution-resources.md) has separate integration tests. Sandbox materialization/write-back, hosted storage verification and full-platform concurrency acceptance remain later work. This Note does not by itself declare G004 complete.
