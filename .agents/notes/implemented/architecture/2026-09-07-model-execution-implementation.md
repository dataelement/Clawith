# Agent Note: Model execution and validated configuration

Status: implemented — Model execution, configuration acceptance and Runner/Context integration have controlled service and application tests.

## Problem

Declared limits and capability booleans do not establish that a configured Model can execute the required protocol. Provider requests also need exact continuation ownership without leaking opaque state into Context or holding database transactions during network I/O.

## Decision

Model owns four explicit protocol adapters, normalized text/Tool/usage/streaming outcomes, request bounds and encrypted continuation. Configuration intake resolves hard limits from supported Provider metadata, an exact Provider/endpoint/model Catalog entry, or explicit administrator values in that order. Missing hard limits fail; network or malformed metadata errors do not silently select another source. A small Tool Calling probe observes a call to a non-executed test Tool. It performs no external Tool effect and does not probe hard limits with an oversized request.

The probe retains the resolved output allowance and reasoning/thinking settings. A separate 256-token cap was rejected because it could contradict a valid configured thinking budget and falsely reject Tool Calling support. The prompt remains minimal; allowing the configured output is not a requirement to generate that many tokens. Validation can incur the configured model's inference cost and remains bounded by the Model operation deadline and response-byte limit.

`validate_configuration` returns an owner-produced acceptance bound to the Tenant, Credential and exact configuration values, including the explicit `settings.protocol`. Enabled creation, enabled changes and explicit enablement require a matching acceptance. Resolution rejects a requested protocol that differs from the stored protocol; adapters consume this setting without forwarding it as a Provider option. Disabled drafts remain writable without a Provider call. Credential and draft creation commit before validation; configuration activation occurs in a subsequent short transaction. Tests use actual public configuration and Credential services with only the remote HTTP peer replaced.

Model persists encrypted required continuation before returning the complete step result. Waiting retains it; a committed terminal fact supplied by Runner authorizes cleanup. Missing, malformed or unreadable required state fails explicitly. Model imports no Run-private persistence. Provider requests use the stateless HTTP contract and release database connections before network work.

Captured policies are validated without consulting current configuration. The public validator checks bounded JSON before parsing, protocol agreement, required Tool Calling and Context profile capability/limit agreement. Model exposes its immutable operation limits to Context rather than requiring Context to invent request cardinalities.

One-shot summary requests share the fixed Model, Credential, request validation and error normalization, but never read or modify the Run's encrypted continuation. Their input is non-streaming text without Tool execution or continuation references; only a complete text result is accepted. Returned summary results do not promise continuation. This prevents a summary call from pruning opaque state still needed by a recent execution interaction.

`count_input_tokens(policy, request)` performs a metadata request, not generation or an execution Model Step. It returns a Provider token estimate or normalized Model failure; Context still checks the resulting input against the fixed window and output allowance. It reads the same owner-bound Credential and required continuation, releases database sessions before HTTP, and never saves continuation. The complete count request and response are bounded; its deadline is the smaller of ten seconds and the configured Model operation timeout. Counting neither grants tools nor creates a Run, and Model does not add retries around the operation.

Anthropic uses `/messages/count_tokens`, Gemini uses `models/...:countTokens`, and Responses uses `/responses/input_tokens`. Chat may use the Responses counter at the same configured endpoint only when captured capabilities explicitly select `image_token_counting="openai_responses"`; this does not switch the generation protocol. Unconfigured counters, counter HTTP 404/405/501 and Chat continuation that the selected counter cannot represent fail explicitly as unavailable. No universal image Token cost, inferred endpoint, substitute account or fallback counter is used.

Logical image Tool Results retain their Tool Call identity and original History source. Anthropic and Responses encode native image results. Chat and Gemini retain textual Tool Results, then attach images in a call-labelled Provider user message after every result in the exchange; this is physical encoding, not a new human input. The adapter does not fetch image URLs or Workspace files. Count requests use the corresponding media encoding, and cache directives do not mutate replay values.

HTTP status and structured Provider error fields determine failure classification. Explicit `rate_limit_error` or `rate_limit_exceeded` codes and status 429 produce `rate_limited`; `overloaded_error`, `server_error`, `internal_server_error` and explicit 5xx statuses produce `provider_unavailable`, except the unsupported-counter statuses above. HTTP-200 error envelopes and streaming errors, including nested Responses failures, follow the same classification. Unknown errors are not made retryable by their message text. Diagnostics contain fixed messages rather than private Provider payloads. The caller owns any approved retry sequence; Model performs one attempt per invocation.

## Alternatives considered

An optional capability helper without enforcement at configuration activation was rejected because ordinary `create`, `update` and `set_enabled` callers could bypass validation. Guessing limits from model names, switching Model accounts, reconstructing missing signatures and silently trimming Context were excluded by the approved architecture.

Using ordinary continuation-aware execution for summary generation would replace the active Run's retained state. Allocating a fictitious Run for this utility would invent an execution identity without its owning lifecycle. The explicit one-shot summary port avoids both.

Charging every image a guessed constant or implicitly querying another protocol would misrepresent the configured Model's budget. Converting image data to base64 text would not make it visible as an image. Model-owned counters and protocol-specific media encoding preserve the original source without adding a media owner.

## Consequences

Enabled configuration requires external validation before the write transaction. Consumers cannot switch execution protocol after validation. This changes the internal G003 service call requirements without adding compatibility paths or a second configuration lifecycle.

## Verification and remaining gaps

Controlled tests exercise the four adapters, streaming failure, credential isolation, continuation persistence/replay/cleanup and metadata/Catalog/administrator precedence. Dependent module fixtures provision disabled drafts, validate through a controlled peer and enable through the real public service instead of forging acceptance or setting database flags directly.

Independent review and controlled tests cover continuation, configuration acceptance and exact probe output/reasoning fields across all four adapters. [Application resource composition](2026-09-07-application-execution-resources.md), Workspace Builtins and explicit provisioning are implemented; G005 supplies the actual Runner/Context consumer. Hosted Provider behavior, target migrations, deployment and 50-Agent performance are not established by local tests. The [G004 contract](../../../../specs/backend-execution-dependencies.md) remains the approved authority.

Thirteen summary tests exercise actual PostgreSQL continuation and controlled HTTP: success, transport failure and cancellation leave the complete existing continuation row unchanged, and a subsequent ordinary step replays the original signature. Text-only restrictions and incomplete/Tool-producing summaries fail explicitly. Captured-policy tests cover agreement and pre-parse limits. Independent review approved the Model-side behavior; Context separately validates whether the resulting summary fits its view.

The Model suite passed 174 tests after the media/counting and structured-error changes; scoped Ruff and Pyright passed. `test_media_budget.py` exercises the four media encodings, actual count-request bodies with controlled HTTP, real Credential access, unchanged encrypted replay, explicit Chat counter selection, token/byte limits, cancellation and response closure, deadline selection and error classification. [Run application composition](2026-09-08-run-tool-and-application-composition.md) separately records the MCP-image integration fixture through actual Runtime, Context and Model services. Neither evidence establishes hosted counter availability, exact billing, G006 user attachment APIs or formal platform performance.

Counter wire references: [OpenAI token counting](https://developers.openai.com/api/docs/guides/token-counting), [Anthropic token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting), [Gemini countTokens](https://ai.google.dev/api/tokens). Provider counts remain estimates, not a universal image-cost formula.
