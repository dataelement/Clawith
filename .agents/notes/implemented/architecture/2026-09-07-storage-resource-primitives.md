# Agent Note: Bounded storage operations and resource locks

Status: implemented — Local and S3 adapters expose bounded reads, pagination and resource-scoped mutation primitives; application lock-pool composition remains separate.

## Problem

Workspace must obtain content and its revision coherently, stage replacements before publication, and coordinate package readers across processes. Unbounded materialization and a connection for every nested lock prevent useful concurrency.

## Decision

The storage base contract exposes bounded versioned reads, cursor pages, explicit directory creation, empty-directory removal and resource locks. Local reads obtain bytes and metadata from one opened file. Metadata revisions do not require hashing entire files. Local page scans reject more than 4096 directory entries rather than materializing arbitrarily large namespaces; cursors identify the observed directory metadata, not a snapshot.

Local writes prepare and synchronize a temporary file before the commit lock. Mutation locks share ancestor paths and exclusively lock the target, allowing unrelated commits while excluding parent deletion races. Preparation failure leaves visible content unchanged. Cross-process locks live outside the content namespace.

S3 reads retain the GET revision and bound bytes before materialization. Pagination consumes one bounded service page. Conditional writes and deletes use native object conditions. Empty-directory removal deletes only an empty marker, never concurrent children. Recursive deletion is reserved for owner-controlled cleanup and checks every page and provider deletion error.

`InputFileStorage` supplies physical attachment operations over the same backend without owning product authorization or closing shared resources. Its publication guard uses the separate `input-file-publication/` lock namespace, allowing conditional backend mutations inside that guard without reacquiring a non-reentrant lock. Immutable creation accepts an identical-content retry and rejects conflicting content. Inspection returns a coherent revision, byte size and SHA-256; range reads reject stale revisions and still bound the complete underlying read to four MiB. Conditional deletion cannot remove a replacement revision. Missing inspection returns no object, while reading a missing object remains an explicit I/O failure.

The cached synchronous S3 client has one thread-safe initialization and disposal boundary. Concurrent first reads cannot create clients that are then overwritten and leaked. Async entry points offload initialization rather than blocking the event loop. Every cached-client SDK operation drains its worker before propagating cancellation, so caller task completion is sufficient to exclude active HEAD, listing, read or presigning workers from subsequent client disposal. A byte read owns GET, body consumption and body closure in one worker; cancellation cannot discard a returned body before cleanup. Failed initialization publishes no cached client and releases the lock, allowing a later explicit operation to try again.

PostgreSQL resource locks use a task-owned connection for nested acquisitions. An inherited child Task cannot share an active lease. Cancellation balances unlocks and releases or invalidates the connection. The injecting composition must own a separate bounded lock-only pool using session-pinned PostgreSQL connections; business transaction capacity cannot be consumed by lock waiters. S3 resource locking fails explicitly without a configured provider.

## Alternatives considered

One connection per nested lock was rejected because bounded pools could deadlock before publication. A global local-mutation lock blocked unrelated paths. Repeated whole-file hashing and unbounded listings were rejected because their cost grows with unrelated stored content.

## Consequences

Storage owns bytes, revisions and mechanical locks, not Workspace authorization or package state. Adapter operation bounds are not file quotas. File and prefix operations do not imply atomic multi-file transactions. Session advisory locks do not work through transaction-pooling proxies.

The application drains admitted operations before calling `aclose`. Local storage has no persistent client handles. S3 closes its cached synchronous SDK client once, off the event loop, and rejects new clients once closure starts. Concurrent/repeated close calls observe the same completion or failure; cancellation waits for actual cleanup before propagating. Each asynchronous S3 operation still owns its own client context, so this contract does not claim a shared long-lived asynchronous connection pool.

## Verification

Storage tests exercise versioned reads, pagination, native conditional writes, cancellation, cross-process local exclusion, nested PostgreSQL leases, sibling progress, parent deletion and empty-directory cleanup. Disposal tests observe native SDK pool entries being released, concurrent first-read initialization, event-loop progress during initialization, cancellation waiting for cleanup, initialization and close failures, repeated close behavior and retained Local files. Controlled S3 responses and local PostgreSQL tests do not establish live S3 behavior, application pool composition or 50-Agent performance.

Attachment wrapper tests use real Local storage and native synchronous/asynchronous S3 SDK clients against a controlled HTTP server, with PostgreSQL advisory locks. They verify guarded CAS without nested-lock deadlock, identical retries, conflict rejection, stale revisions, conditional deletion, canonical keys, whole-object bounds and progress for unrelated publication keys.
