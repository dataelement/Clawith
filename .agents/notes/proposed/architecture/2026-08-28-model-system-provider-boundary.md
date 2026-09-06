# Agent Note: Model System and Provider Boundary

Status: proposed — the fixed-Model, Context-profile, Provider-adapter, and normalized-output boundaries are agreed but not implemented

## Problem

Agent Loop and Context need one model contract without branching on OpenAI, Anthropic, Gemini, or another Provider. Model selection, credentials, context limits, request encoding, cache controls, streaming, usage, errors, and Provider continuation features have different wire formats and must not leak into product, Tool, or Run semantics.

## Proposal

### Fixed Model Policy per Run

Run creation resolves and fixes one Model Policy containing the selected Model, Provider, model capability profile, and base request settings. Every Model Step in that Run, including after Waiting and resume, uses the same policy. Agent or platform configuration changes affect only new Runs.

Any transient retry policy may call only the same fixed Model and Provider. Whether a request is retried, which errors qualify, attempt count, delay, timeout, and user-visible error remain implementation decisions. The target has no fallback Model, ordered fallback list, cross-Model retry, cross-Provider failover, Tenant-default fallback during execution, or capability-driven automatic Model switch. A future need for fallback requires a new architecture decision and schema change rather than dormant fields or hidden routing in the first release.

Model Policy contains no `max_model_steps`, model-turn limit, renamed `max_tool_rounds`, total Run duration, idle timeout, or hidden equivalent. Agent Loop does not terminate merely because it has completed a configured number of Model Steps or occupied a configured Run duration. Admission, cancellation, Provider hard limits, and implementation-time per-request I/O timeouts remain separate owner policies.

The target also has no configurable per-Run, per-Agent, daily, monthly, or other Token usage limit or Token quota. Model System records normalized usage for observability, but usage does not stop a Run. Context still fits each request within the selected Model's real context window and maximum output capability; that technical request assembly is not a product Token allowance and never silently discards required source facts.

### Minimal Model persistence and capability resolution

The first release persists Tenant-scoped `llm_models`, one non-null `Agent.model_id`, and required per-Run `provider_continuation_states`. It has no `agent_model_policies` table. Tenant default Model is used only to initialize a new Agent's explicit `model_id`; later Tenant-default changes do not alter existing Agents. Run creation resolves the selected Model into the immutable Model Policy stored in Run Snapshot.

An LLM Model stores its Tenant, Tenant-owned Credential reference, Provider, model identifier, label, base URL, hard context/input/output capabilities, optional image, streaming, cache, and continuation capabilities, versioned non-Secret request settings, enabled state, archive timestamp, and timestamps. The binding rejects Membership- or Agent-owned Credential even when it belongs to the same Tenant. Disabling or archiving Model affects selection for new Runs without cancelling existing Runs; historical Run Snapshot remains unchanged and actual Provider/resource failure remains an execution error.

Hard context, input, and output capabilities resolve once when Model configuration is accepted. Resolution precedence is authoritative Provider model metadata when the Provider exposes it, then the maintained Builtin Model Catalog, then explicit administrator input for a custom or unknown model. The stored capability source is `provider_api`, `builtin_catalog`, or `manual`. Missing required hard limits prevents Model enablement; Runtime never invents a default or probes limits by sending oversized requests. A Run does not refresh these facts from Provider API.

Every enabled Agent Model must support the target's Tool Calling contract. Registration performs the Provider-specific validation required to establish that invariant; a Model that cannot produce supported Tool Calls cannot be enabled as an Agent Model. `supports_tool_calling` is therefore not a nullable capability field and Agent Loop has no branch for a Tool-less Agent Model. Embedding, image, speech, transcription, and other specialized models remain capability-specific Tool providers rather than Agent Models.

`provider_continuation_states` is a narrow Model System table keyed by Run and contains Tenant, Model, Provider, payload and encryption schema versions, encrypted opaque payload, and update time. It stores only exact continuation data required for another call in the same Run. Required state has no independent expiry or TTL: it remains available while the Run may need it, including throughout Waiting, and is removed only after it is no longer required or the Run becomes terminal. It contains no Run Status, History, Context, Tool state, or recoverable execution point.

### Model Context Profile

Model System supplies Context with an immutable Model Context Profile containing the model facts required for budgeting and assembly:

```text
Model identity and Provider
context window
maximum output capability
resolved secret-free User, Agent, or Tenant behavior settings
reasoning and output settings
token counting or estimation behavior
Tool, image, file, and request-overhead rules
supported caching, compaction, streaming, and continuation capabilities
```

The Model Context Profile never contains Provider Credential references, access tokens, authorization headers, Provider endpoints, secret-store locations, or raw Provider configuration. The private Model Policy in the existing Run Snapshot retains the resolved non-Secret Provider endpoint, Credential reference, and request settings required to continue the same execution after Waiting. Model System consumes those private fields; Context receives only the explicitly selected Model Context Profile. Product-managed Secret bytes remain in Credential and are obtained only for the external call, never stored in Run Snapshot.

Context calculates effective input budget, source allocation, retained history, Compaction thresholds, and final model view. Model System does not choose which Session, Run, Memory, Skill, File, or Tool Result content to omit because those semantics belong to Context and their source owners.

Before sending, Model System validates the assembled request against hard model and Provider limits. It returns a normalized budget or capability error rather than silently truncating or changing Context.

### Provider Adapter

Context supplies Provider-neutral logical segments. Model System Adapter maps them to each Provider's physical request order, message roles, Tool schema format, cache hierarchy, and continuation mechanism.

```text
Context logical segments -----+
                               +----> Model System Adapter
Model System secret resolution-+        - Provider request encoding
                                        - credentials
                                        - cache controls
                                        - continuation metadata
                                        - streaming transport
```

Provider data is not one durability class. Prompt caches, KV caches, and replaceable conversation references are optional optimizations when the Adapter can reconstruct a valid request from Clawith Context and Run History. Opaque reasoning, thinking, response, signature, or continuation items are required execution state when the fixed Model cannot accept the next request without exact replay. Neither class becomes product truth or replaces Run History.

### Normalized Model Step Result

Agent Loop consumes one Provider-neutral Model Step Result:

```text
Assistant Content
Tool Calls
Finish Reason
normalized usage reference
normalized error or success
```

Tool Calls retain stable call identity and normalized input. Agent Loop forwards them to Tool System. Provider raw JSON, SDK objects, internal error text, credentials, and Provider-specific continuation items do not cross this boundary.

Other consumers receive separate narrow contracts:

```text
Agent Loop ----------> Model Step Result
Streaming observer --> Model Stream Events
Usage and quota -----> Model Usage Record
Failure handling ----> Model Error
Provider Adapter ----> internal Provider Execution Metadata store
```

No universal response object serves all consumers.

### Streaming and finality

Streaming Delta is a transport and presentation projection. Model System normalizes visible stream events for observers, but Agent Loop settles only from the complete normalized Model Step Result. Partial deltas do not become an alternate Model Output or independent Run fact.

### Provider execution metadata

Each Provider Adapter classifies returned Provider Execution Metadata as required continuation state or optional optimization according to the fixed Model's actual next-request contract. Unknown data is not assumed disposable when the Provider requires it for a supported continuation path.

Required continuation state is correlated to its Run and Model Step, stored by Model System before the normalized Model Step Result is released to Agent Loop, and replayed exactly on the next applicable Provider request. It survives ordinary Waiting and resume for that Run without an independent expiry or TTL. Model System treats it as opaque and confidential: it does not enter model-visible Context, ordinary Run History content, Tool Results, Workspace, Frontend state, or ordinary logs.

The Model Step does not settle and its Tool Calls do not execute until required metadata has been committed. A persistence failure, missing or corrupt required item, unknown payload schema, or unavailable decryption key returns a structured unrecoverable Model Error and the Run becomes Failed. Required-state encryption-key rotation must retain every key still referenced by a non-terminal Run, or re-encrypt all affected Waiting state before retiring that key. If execution disappears before the Model Step and required metadata are committed, Agent Runner records Interrupted under its normal execution-loss rule. None of these failures permits reconstructed replay or revival of that Run; later work starts a new Run from committed product and Workspace facts.

Optional Provider metadata may use bounded retention or TTL and may be discarded whenever the Adapter can rebuild a correct request from the fixed Model Policy, Context, and Run History. Losing it may reduce cache reuse or performance but cannot change the semantic outcome or erase Clawith facts.

Model System removes required Provider Execution Metadata when it is no longer required for a later Model Step or after the Run becomes terminal. Terminal cleanup is retryable housekeeping; cleanup failure is observable but does not change the already committed terminal Run outcome. This narrow per-Run storage does not create a generic Checkpoint, Provider-owned Run History, cross-Worker takeover, Tool replay, or side-effect recovery protocol.

### Ownership boundaries

Model System owns Model resolution, Provider adapters, credentials, capability profiles, request parameters, physical request encoding, Provider caching, required Provider Execution Metadata persistence and cleanup, streaming transport, normalized usage, and Provider error mapping.

It does not own Platform Instruction content, Soul, Product Input, Run History, Context source selection, Tool registration or execution, Task or Goal judgment, Workspace, Session or Group output, or Channel delivery.

## Alternatives considered

### Let Agent Loop call Provider SDKs directly

This would duplicate Provider conditions throughout the execution loop and make every new Provider a Runtime refactor.

### Let Model System choose Context content

Model System knows token and request constraints but not the ownership or semantic importance of Session, Run, Memory, Skill, File, and Tool facts. It supplies the profile; Context chooses the view.

### Re-resolve Model on every step

Mid-Run configuration changes would alter context budget, Tool behavior, caching, and output semantics. Model Policy remains fixed for one Run.

### Configure a fallback Model

Automatic fallback changes Context capacity, Tool support, continuation requirements, caching, request semantics, cost, and output behavior inside one Run. The target fixes one Model and fails explicitly. Fallback may be reconsidered later only as a new Model Policy and failure-contract decision.

### Use Provider state as Run History

Provider state is opaque and incomplete as an execution audit record. Clawith keeps its own Run History. Required Provider Execution Metadata is stored only to continue the fixed Model correctly, while optional cache state remains disposable; neither becomes Run History authority.

### Return raw Provider responses to every consumer

Agent Loop, streaming, usage, and failure handling need different narrow contracts. Raw responses stay inside the Adapter.

## Acceptance criteria

- Every Run fixes one Model Policy and uses it through Waiting and resume.
- Model Policy and Agent contain no Model Step or model-turn limit; reaching an arbitrary loop count is not a Run failure condition.
- Model Policy contains no total Run duration or idle timeout; Provider request timeout and its exact failure behavior remain implementation decisions rather than Run policy.
- Model Policy, Agent, and Run contain no configurable Token usage limit or quota; usage remains observable while Context respects only the fixed Model and Provider hard request capacities.
- Model configuration changes affect only new Runs.
- The first release stores Tenant Model configuration, one explicit `Agent.model_id`, and required per-Run Provider continuation state; resolved Model Policy is a Run Snapshot value rather than another table or lifecycle object.
- Required Model hard limits resolve from Provider API, then Builtin Catalog, then explicit manual configuration; missing values block enablement and no Runtime default or oversized-request probe is allowed.
- Every enabled Agent Model supports Tool Calling by contract, so no nullable `supports_tool_calling` field or Tool-less Agent Loop branch exists.
- Context receives an immutable Model Context Profile and owns budgeting, source selection, and Compaction.
- Model Context Profile is secret-free; credentials and secret-bearing Provider configuration remain inside Model System and are applied only by Provider Adapter during request transport.
- Model System validates hard request limits and never silently truncates Context.
- Provider Adapter maps logical Context segments to Provider-specific request format and caching.
- Provider Adapter distinguishes required continuation state from optional optimization metadata; neither replaces Run History.
- Required Provider Execution Metadata is committed before Model Step settlement, has no independent expiry or TTL, survives Waiting and resume, replays exactly, and is removed when no longer needed or after the Run becomes terminal.
- Missing, corrupt, unreadable, or unknown-schema required metadata fails the current Run without reconstructed replay; key rotation retains usable old keys or re-encrypts every affected Waiting state before retirement.
- Optional Provider metadata may use TTL because its loss may reduce performance but cannot affect correctness; failed terminal cleanup is observable and retryable without changing the terminal Run outcome.
- Agent Loop consumes only a normalized Model Step Result.
- Streaming, Usage, Error, and Provider Execution Metadata use separate narrow contracts.
- Tool Calls retain normalized stable call identity before entering Tool System.
- Partial Streaming Deltas do not settle Model or Run outcome.
- Raw Provider responses, Credential material, SDK objects, and internal errors do not enter model-visible results. Non-Secret Provider endpoints and Credential references may persist in the private Run Snapshot Model Policy but never enter Model Context Profile or model-visible Context.
- Any later retry policy may call only the same fixed Model and Provider; exact retry and error behavior remains an implementation decision, and the target contains no fallback Model field, list, resolution, or execution path.

## Risks and open questions

Exact Model Policy fields, token estimators, request parameters, Provider capability matrices, optional-cache controls and retention, continuation storage representation, same-Model retry classification, usage schema, and error taxonomy remain implementation decisions.
