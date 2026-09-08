# Agent Note: Incremental Context views and disposable projections

Status: implemented — sourced assembly, compaction and projection mechanics are available; complete Runtime acceptance is tracked by G005.

## Problem

Rebuilding instructions and historical messages on every call repeats source reads and destabilizes cache prefixes. Revalidating and serializing unchanged historical units also repeats CPU work even when database reads are incremental. Persisting only a generated summary in a disposable projection would make the actual model input unrecoverable after projection deletion. Image transport size cannot supply the Model's image Token cost.

## Decision

Context receives labelled fixed sources, a Model-owned profile and operation limits, and newly committed complete interaction units. Platform and Soul labels share one leading system message. Run supplies the original initial or delegated request as a fixed user-data source rather than a compressible duplicate in the execution tail. Reference sources remain user data; History cannot introduce system messages. The assembler reuses its fixed prefix and appends only advancing History units. Tool calls and their results remain complete units.

Each assembler owns one fixed prefix cost, the latest exposed Tool tuple and cost, and one prepared view with per-unit costs and canonical JSON fragments. Reuse requires the exact immutable state object; a reconstructed or replaced state is validated again, even if its sequences match. Nested message, content and call collections must be immutable. Changed Tool definitions invalidate the Tool cost; clearing or compaction replaces the cached base. The cache holds one current view and its encoded fragments, each bounded by the 16 MiB limit and 100,000 logical items. It never becomes a cross-Run cache or an archive of prior views. Releasing the assembler releases its caches.

On a cache hit, only new units are validated and serialized. Full request assembly still copies ordered message references; projection preparation still joins encoded fragments and hashes the complete result. These unavoidable complete-result operations remain bounded and measured. Model remains responsible for validating its final request.

Text-only budgeting uses a conservative UTF-8 estimate plus framing without network counting. Image-bearing views require the fixed profile's image capability and a Model-owned exact whole-request counter. Image bytes still count toward transport and storage limits but are never treated as image Tokens. Message and Tool cardinality limits apply in both paths. Physical request limits do not limit a Run's lifetime, total steps or cumulative Tokens.

The counter receives all logical messages and exposed Tools. Only its latest successful result is cached, keyed by the complete immutable request rather than an image identifier; changes to text, images or Tool definitions require a new count. Counting is bounded metadata I/O, not a generated Model Step. The finite compaction pipeline may count its original, cleared, retained-tail and summarized candidates. Each count has a deadline of at most ten seconds and no longer than the Model operation limit. `ModelPreparationFailure` preserves normalized failures for Run-owned retry policy; Context neither retries network operations itself nor adds another execution lifecycle.

When the request no longer fits, Context first replaces older large Tool outputs in its view. If needed, it asks an injected summarizer for objective, constraints, progress, decisions, unresolved work, next actions and exact references, preserving a recent complete interaction. It returns the actual replacement messages and both summary coverage and the complete represented History cutoff. Run must record these observations before they influence a Model call. `restore_base` reconstructs those exact messages without calling the summarizer. Todo and optional timezone-qualified minute data enter the tail; private Model settings do not.

Older Tool images can be replaced by an explicit omitted-output marker, but the latest complete interaction, including its images, is retained. Original History is unchanged. A text-only summary adapter uses an explicit image-omission marker and asks to retain source references; it does not present base64 as inspected image content. One successful summary may be retained for the exact previous-summary, older-unit and Token-target inputs so a subsequent counting failure does not repeat successful generation. The assembler's sources are fixed, and advancing the source state clears that single retry entry. Different inputs cannot reuse it.

Projection persistence uses the caller's transaction and bounded reads/writes. A stale valid save cannot replace a later valid cursor. Unknown versions and invalid projections are cache misses; invalid observed values can be discarded without deleting a concurrent replacement, and a bad high cursor cannot block a rebuilt view. Projection serialization checks total fields, messages, UTF-8 expansion and summary bytes before allocation. Projection state never decides whether an input was consumed.

Saving a projection returns its content hash for the corresponding Run-owned ModelInput record. Runtime may reuse it only when that recorded hash and the base/cursor relationships agree. Structurally valid but altered content is a miss just like an invalid version. An older ModelInput without a hash reconstructs from History rather than trusting the cache.

`save_prepared` consumes Context's private prepared encoding bound to its state object. It preserves the same v1 bytes and hash as ordinary state serialization without revalidating or serializing old units again. PostgreSQL receives bounded encoded text cast to JSONB rather than a Python decode/re-encode round trip. The caller's transaction and the History-bound hash protocol are unchanged; there is no public validation-bypass flag. Persisted reads still validate and compare the expected hash.

## Alternatives considered

Re-reading all sources for each step wastes work and changes fixed observations. Re-generating a lost summary would not recover the text actually used. A separate durable Context history would duplicate Run's authority. Keeping full-state validation and JSON serialization on every projection write would negate much of the incremental assembly benefit; accepting a generic skip-validation flag would weaken the owner boundary. Context-produced prepared bytes avoid both problems.

Estimating image Tokens from transport bytes misrepresents the Model budget. Caching by image identity alone misses changes to the surrounding request. A separate preparation-yield state machine for token metadata calls adds execution machinery without a generated Model Step; bounded counting remains inside preparation, while Run owns generation scheduling and retries.

## Consequences

Caller-owned Run History and startup Snapshot remain necessary for reconstruction and isolation. Assembly telemetry records local duration including preparation/hash work, source reuse, input Tokens, compaction duration/count, summary coverage, and validated, serialized and reused unit/message counts. Remote counting calls/duration and summarizer waiting are measured separately and excluded from local assembly duration. Cleared-Token differences are unknown when the original view cannot be counted or when the comparison crosses exact image counting and conservative text estimation; an unknown value is not reported as zero.

Runtime supplies Run identity to a synchronous non-blocking observation sink and isolates sink failures from execution. Provider usage remains a separate normalized Model observation, not another Context authority or a condition for lifecycle decisions. Failure to fit after safe compaction is explicit, not silent truncation of authoritative sources.

## Verification

The recorded 44-test Context/Run Tool adapter verification covered real PostgreSQL projection reads, rollback, stale/invalid replacement, exact base reconstruction, Model request preflight, instruction-boundary rejection and actual executor role denial. Its independent code and architecture approvals apply to those mechanisms, not automatically to later incremental or media additions.

A subsequent focused run of `tests/modules/context` and `tests/execution_dependencies/test_runtime_summary.py` passed 56 checks, with scoped Ruff and Pyright passing. Incremental spies verify that old units are not revalidated or serialized, changed state/Tools invalidate reuse, and compaction resets the base. Prepared encoding matches v1 bytes/hash for Unicode, escaped content, Tool exchanges and summaries; real PostgreSQL tests verify `save_prepared` round trips without traversing old state. Controlled counters verify whole-request invalidation, no network counting for text, transport bounds before counting, timeout cancellation, preserved latest images, explicit older-image clearing and successful-summary reuse after a later count failure. These are not hosted Provider, full-product multimodal E2E or complete G005 load-acceptance claims.

An isolated CPU comparison against `20fb33814bb0032d705d621590f49266ea860436`, using 200 existing units containing 400 KB of text and 20 small additions, measured preparation plus complete projection encoding/hash. P95 changed from 5.88 ms to 0.192 ms; unit validation calls changed from 8,420 to 20. This supports the local cache decision but excludes SQL, Model network latency and platform-wide throughput.
