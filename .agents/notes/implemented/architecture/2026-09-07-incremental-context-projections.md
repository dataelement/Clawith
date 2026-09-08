# Agent Note: Incremental Context views and disposable projections

Status: implemented — sourced assembly, compaction and projection mechanics are available; complete Runtime acceptance is tracked by G005.

## Problem

Rebuilding instructions and historical messages on every call repeats source reads and destabilizes cache prefixes. Persisting only a generated summary in a disposable projection would also make the actual model input unrecoverable after projection deletion.

## Decision

Context receives labelled fixed sources, a Model-owned profile and operation limits, and newly committed complete interaction units. Platform and Soul labels share one leading system message. Reference sources remain user data; History cannot introduce system messages. The assembler reuses its fixed prefix and appends only advancing History units. Tool calls and their results remain complete units.

Budgeting uses a conservative UTF-8 text estimate plus framing, Model message and Tool cardinality limits, and complete request bytes. Physical request limits do not limit a Run's lifetime, total steps or cumulative Tokens. Images require Model-owned image accounting and are explicitly rejected by this text assembly path rather than assigned a guessed cost.

When the request no longer fits, Context first replaces older large Tool outputs in its view. If needed, it asks an injected summarizer for objective, constraints, progress, decisions, unresolved work, next actions and exact references, preserving a recent complete interaction. It returns the actual replacement messages and both summary coverage and the complete represented History cutoff. Run must record these observations before they influence a Model call. `restore_base` reconstructs those exact messages without calling the summarizer. Todo and optional timezone-qualified minute data enter the tail; private Model settings do not.

Projection persistence uses the caller's transaction and bounded reads/writes. A stale valid save cannot replace a later valid cursor. Unknown versions and invalid projections are cache misses; invalid observed values can be discarded without deleting a concurrent replacement, and a bad high cursor cannot block a rebuilt view. Projection serialization checks total fields, messages, UTF-8 expansion and summary bytes before allocation. Projection state never decides whether an input was consumed.

Saving a projection returns its content hash for the corresponding Run-owned ModelInput record. Runtime may reuse it only when that recorded hash and the base/cursor relationships agree. Structurally valid but altered content is a miss just like an invalid version. An older ModelInput without a hash reconstructs from History rather than trusting the cache.

## Alternatives considered

Re-reading all sources for each step wastes work and changes fixed observations. Re-generating a lost summary would not recover the text actually used. A separate durable Context history would duplicate Run's authority. Silently estimating image Tokens from transport bytes would misrepresent the physical Model budget.

## Consequences

Caller-owned Run History and startup Snapshot remain necessary for reconstruction and isolation. Assembly telemetry records duration, source reuse, estimated input Tokens, compaction duration/count, cleared Tool Tokens and summary coverage. Provider usage remains a separate normalized observation. Failure to fit after safe compaction is explicit, not truncation of authoritative sources.

## Verification

Context and Run Tool adapter checks passed 44 tests, including real PostgreSQL projection reads, rollback, stale/invalid replacement, exact base reconstruction, Model request preflight, instruction-boundary rejection and actual executor role denial. Scoped Ruff and Pyright passed; independent code and architecture review approved these mechanisms. This does not establish hosted Provider behavior, multimodal Context or the G005 load target.
