# Run owner

Run uniquely owns execution identity, lifecycle, immutable startup Snapshot and append-only History. Models and repositories stay private. `contracts.py` owns versioned History encoding; cross-owner consumers use `public.py` when its service facade is implemented, not private codecs or ORM.

History preserves initial/related input, complete normalized Model Steps, Tool Results, Waiting and outcomes. Model-Step `read_through_sequence` is a durable input-consumption fact; Context summary coverage is not. Unknown authoritative kinds, versions, extra fields and malformed payloads fail explicitly without dropping data or exposing input values in diagnostics.

The [Core Runtime contract](../../../../specs/backend-core-runtime.md) owns lifecycle rules. Service-wide interruption ends Running and Waiting Main/Subagent Runs without waking a Parent; ordinary per-Run cancellation remains distinct. No lifecycle, Snapshot or database writer is implied by the codec alone.
