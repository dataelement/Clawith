# Agent Note: Frontend Authoritative API Failures Remain Visible

Status: implemented — authoritative reads, writes, and multi-step imports preserve transport and contract failures instead of publishing successful local state.

## Problem

Frontend configuration surfaces must distinguish absent or empty Backend state from a failed or malformed response. Mapping either failure to empty data can make platform settings, tenant quotas, company introductions, channel configuration, or Agent tools appear editable or successfully loaded before their authoritative state is known. Tool mutations and multi-step MCP imports also need one failure contract so local success cannot outlive a rejected write or missing credential.

## Decision

Platform administration forms that write a batch of related settings remain disabled until every required read in that batch succeeds. Each platform configuration response enters as `unknown` and is parsed before the batch becomes ready. An existing optional system-setting object with an empty value resolves to the documented form defaults; missing required fields or malformed values reject the batch. Read failures are visible and retryable, and save handlers independently reject writes while the authoritative batch is unavailable.

Platform metric transport and response parsing belong to the service layer. The service rejects HTTP and schema failures. The dashboard reports the failure while retaining already loaded metric data.

`src/services/api.ts` passes successful JSON through named response parsers before returning typed values for selected auth, tenant, administration, Agent, file, browser-control, and upload contracts. Parsers receive `unknown` and report malformed payloads as `invalid_api_response` with the failing path and expected shape. JSON endpoints reject `204`; endpoints whose contract is no content use `requestVoid` and require `204`. Other generic `request` and `fetchJson` consumers remain explicit runtime-validation debt rather than inheriting safety from a TypeScript type argument.

External JSON used by channel and Tool configuration is `unknown` until the owning service or feature boundary validates the complete response. Only an explicit channel-read `404` means that the optional resource is not configured; authorization failures, server failures, and malformed successful responses remain errors. Stored channel credentials are not copied into editable drafts, so secret fields begin blank.

`ToolsManager` reads the canonical `/api/tools/agents/{agentId}/with-config` contract and rejects the complete list when any required Tool field is invalid. A failed initial load shows an error and retry action; a failed refresh may retain already loaded Tools while making the failure visible. Tool writes reject non-success HTTP responses. Optimistic enabled-state changes roll back on failure, and configuration dialogs close only after their write succeeds.

The Frontend MCP import helper owns the create-tools, save-shared-credential, and compensation sequence for both single-Tool and bulk imports. A credential-save failure deletes every Tool created by that operation before rejecting, preserves any Tool IDs whose rollback failed, and reloads the authoritative Tool list. The import dialog stays open and cannot publish success after this secondary failure. Individual Tool creation failures remain an explicit partial result only after required credential persistence succeeds.

## Alternatives considered

**Keep silent defaults and rely on save errors.** Rejected because a valid save can overwrite authoritative values that the user never loaded.

**Clear dashboard data on every failed refresh.** Rejected because it makes a transient failure indistinguishable from a real zero-data result.

**Keep metric parsing in the page.** Rejected because authentication, transport, and external response validation belong to the service boundary.

**Treat `request<T>` as runtime validation.** Rejected because a TypeScript type argument does not inspect external JSON and can publish malformed data as a trusted application value.

**Accept `204` from JSON endpoints as `undefined`.** Rejected because it hides a response-contract mismatch and moves the failure into an unrelated consumer.

**Drop malformed items from an otherwise successful Tool list.** Rejected because partial parsing invents a successful collection the Backend did not return and can hide configuration state.

**Treat every channel read failure as an unconfigured channel.** Rejected because missing optional state is represented only by `404`; authentication, authorization, server, and schema failures require user-visible recovery.

**Keep optimistic Tool state and close configuration dialogs after failed writes.** Rejected because local success must follow the Backend's committed mutation result.

**Keep Tools created before MCP credential persistence fails.** Rejected because those Tools cannot execute with the intended server credential and would publish an incomplete import as usable configuration.

## Consequences

Configuration pages may be temporarily read-only and show a retry action when Backend state is unavailable. Already loaded platform metrics or Tools can remain visible during a failed refresh together with an explicit error. API consumers with named parsers fail at the response boundary instead of rendering incomplete payloads; remaining generic consumers still require contract-by-contract conversion. Tool toggles may update immediately but revert when persistence fails. MCP compensation is best-effort; rollback failures remain explicit for reconciliation rather than being reported as full success.

## Verification

Verification includes positive and negative malformed-success response-parser tests, Tool mutation rollback tests, MCP credential-failure and compensation tests, source-contract guards for save gating and failure retention, the complete Frontend test suite, ESLint, TypeScript, Prettier, and the production build. Browser interaction is a separate required gap unless it is exercised for the outgoing change.
