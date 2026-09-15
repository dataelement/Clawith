# Heartbeat owner

Heartbeat owns one enabled/disabled interval configuration per Agent, timezone/active hours, durable occurrences and Run/result association. It never creates or queries Trigger records. Cross-owner code imports `public.py` only.

Human configuration uses captured Agent access; native configuration uses trusted Main scope. Explicit personal Tool connections are validated by Tool and retained as references, never Secret payloads. Default execution uses Agent accounts. Closed JSON versions and payload shapes are checked on reads.

Configuration version 2 adds optional explicit Session or Group/topic delivery. Occurrence version 3 freezes that destination and a visibility origin; personal-account results remain private to their original Membership even after disabling the connection. Origin metadata never grants a User Workspace. Preserve legacy readers and require provenance backfill where ownership cannot be recovered. Human-question Waiting is disabled for the Main; publication failure does not remove its execution result.

Due scans return a scanned-row cursor independently of the filtered due items. The caller supplies the current process-start lower bound; only the latest current interval can be accepted. Missed intervals and previously pending occurrences are not replayed on restart. Active windows may cross midnight; equal endpoints mean all day.

Occurrence acceptance serializes on the Heartbeat row, preserving one occurrence per source. Run startup and terminal callbacks update the occurrence inside the existing Run transaction, without network I/O, nested transactions or a new scheduler. See the [scheduled product occurrence Note](../../../../.agents/notes/implemented/architecture/2026-09-09-scheduled-product-occurrences.md).

Invalid stored configuration is an explicit due-page error, not a failure of neighboring configurations. Results retain a bounded preview and Run identity; complete output stays in Run History. Read byte metadata before loading occurrence pages or details, and paginate by both row count and aggregate bytes.

Complete-result HTTP and native Tool reads authorize original visibility metadata before reading the Run's terminal JSON fragment. Native callers cannot derive private history access from A2A provenance or a personal Credential; only the actual Main's captured output subject or its own public Agent scope qualifies. Verify the exact occurrence/Run source and preserve 8000-character continuation pages without copying terminal output into Heartbeat storage.
