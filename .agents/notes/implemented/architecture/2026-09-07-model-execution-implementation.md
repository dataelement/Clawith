# Agent Note: Model execution and validated configuration

Status: implemented — Model execution, configuration acceptance and application resource assembly have controlled tests; Runner integration remains G005 work.

## Problem

Declared limits and capability booleans do not establish that a configured Model can execute the required protocol. Provider requests also need exact continuation ownership without leaking opaque state into Context or holding database transactions during network I/O.

## Decision

Model owns four explicit protocol adapters, normalized text/Tool/usage/streaming outcomes, request bounds and encrypted continuation. Configuration intake resolves hard limits from supported Provider metadata, an exact Provider/endpoint/model Catalog entry, or explicit administrator values in that order. Missing hard limits fail; network or malformed metadata errors do not silently select another source. A small Tool Calling probe observes a call to a non-executed test Tool. It performs no external Tool effect and does not probe hard limits with an oversized request.

The probe retains the resolved output allowance and reasoning/thinking settings. A separate 256-token cap was rejected because it could contradict a valid configured thinking budget and falsely reject Tool Calling support. The prompt remains minimal; allowing the configured output is not a requirement to generate that many tokens. Validation can incur the configured model's inference cost and remains bounded by the Model operation deadline and response-byte limit.

`validate_configuration` returns an owner-produced acceptance bound to the Tenant, Credential and exact configuration values, including the explicit `settings.protocol`. Enabled creation, enabled changes and explicit enablement require a matching acceptance. Resolution rejects a requested protocol that differs from the stored protocol; adapters consume this setting without forwarding it as a Provider option. Disabled drafts remain writable without a Provider call. Credential and draft creation commit before validation; configuration activation occurs in a subsequent short transaction. Tests use actual public configuration and Credential services with only the remote HTTP peer replaced.

Model persists encrypted required continuation before returning the complete step result. Waiting retains it; a committed terminal fact supplied by Runner authorizes cleanup. Missing, malformed or unreadable required state fails explicitly. Model imports no Run-private persistence. Provider requests use the stateless HTTP contract and release database connections before network work.

## Alternatives considered

An optional capability helper without enforcement at configuration activation was rejected because ordinary `create`, `update` and `set_enabled` callers could bypass validation. Guessing limits from model names, switching Model accounts, reconstructing missing signatures and silently trimming Context were excluded by the approved architecture.

## Consequences

Enabled configuration requires external validation before the write transaction. Consumers cannot switch execution protocol after validation. This changes the internal G003 service call requirements without adding compatibility paths or a second configuration lifecycle.

## Verification and remaining gaps

Controlled tests exercise the four adapters, streaming failure, credential isolation, continuation persistence/replay/cleanup and metadata/Catalog/administrator precedence. Dependent module fixtures provision disabled drafts, validate through a controlled peer and enable through the real public service instead of forging acceptance or setting database flags directly.

Independent review and controlled tests cover continuation, configuration acceptance and exact probe output/reasoning fields across all four adapters. [Application resource composition](2026-09-07-application-execution-resources.md), Workspace Builtins and explicit provisioning are implemented; G005 supplies the actual Runner/Context consumer. Hosted Provider behavior, target migrations, deployment and 50-Agent performance are not established by local tests. The [G004 contract](../../../../specs/backend-execution-dependencies.md) remains the approved authority.
