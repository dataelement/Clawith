# Trigger owner

Trigger owns explicit cron, once, interval, poll, on-message and webhook configurations, bounded due discovery, occurrence acceptance and Run/result association. It does not schedule Heartbeats or own Run lifecycle. Cross-owner code imports `public.py` only.

Configuration JSON is a closed versioned contract. Validate it on persistence reads; never reinterpret unknown versions. Removal disables and hides configuration while preserving occurrence history. Human configuration uses captured Agent access. Native configuration uses trusted Main scope for that Agent only; selected personal connections require explicit scope authorization and Tool-owned validation.

Version 2 adds explicit poll/webhook Credential references and source Membership filtering while retaining version 1 reads. Credentials must belong to the Tenant or this Agent; source Membership must belong to the Tenant. Message intake receives an already-authorized target-specific event, never permission to scan private history. Application adapters retain event Workspace scope and forbid shared-memory publication.

Configuration version 3 freezes an explicitly authorized Session or Group/topic destination; native callers can reuse only their actual Main origin. Occurrence version 4 freezes destination and result-visibility origin independently of Workspace rights. Filter history by that Membership or Group before loading payload bodies. Recover legacy visibility from immutable Snapshot/account ownership or report a required backfill; never make unverifiable private results Agent-public. Scheduled Main cannot wait for a human; Child-to-Parent questions remain allowed.

Due scans page enabled configurations by ID, with the next cursor derived from scanned rows even when no row is due. The caller supplies the current process start as `not_before`. Accept only the latest current occurrence, never replay missed intervals or restart pending occurrences automatically. Poll I/O and webhook authentication occur before owner intake. Poll observations include the exact configuration used for I/O, so updates cannot misattribute a response.

Occurrence admission serializes on the Trigger row and deduplicates stable source keys. Run callbacks lock Run before occurrence and share the caller's transaction. No callback performs external I/O or creates another transaction. See the [scheduled product occurrence Note](../../../../.agents/notes/implemented/architecture/2026-09-09-scheduled-product-occurrences.md).

Calendar-invalid cron fails at configuration intake. Stored configuration failures appear in due-page errors while valid neighbors continue. Results contain only a Run index and bounded preview; complete outputs remain in Run History. History pages enforce aggregate byte and row bounds before loading JSON and expose a continuation cursor for either cutoff.
