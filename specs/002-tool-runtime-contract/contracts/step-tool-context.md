# Contract: Step Tool Context

## Producer

`RuntimeModelStepService` produces version 1 context only after the actual primary/fallback Provider response has been accepted. The context must describe the exact Workset sent to that Provider call.

## Consumer

`RuntimeToolStepService` consumes the context before validation, authorization or Receipt reservation.

## Rules

1. `assistant_message_id`, pending calls and accepted call entries must match exactly.
2. Tool name, schema, contract version, effect/retry policy and binding come from accepted context.
3. New-format Tool Step must not call ToolProvider or re-evaluate assignment/enabled/channel/readiness.
4. Current tenant, actor, resource, credential, approval and cancel checks remain mandatory.
5. Binding mismatch/corruption fails before Receipt; it is not guessed or rebuilt.
6. Legacy checkpoint may resolve a batch once and must emit compatibility telemetry.

## Identity

- `provider_call_id`: optional original wire ID;
- `call_instance_id`: required Clawith ID placed in checkpoint `tool_calls[].id` and DB `tool_call_id`;
- `execution_id`: created/resolved by Receipt reservation.

Provider output returned to the Provider must use its expected Provider call identity. Internal projections and idempotency use Call Instance/Execution identity.

## Binding Validity

Ordinary visibility changes affect the next Model Step only. Hard safety invalidators for an accepted Call are:

- tenant/actor mismatch;
- resource authorization revoked;
- credential revoked/unavailable;
- exact registered handler/provider target removed without compatible resolver;
- durable Run cancellation;
- corrupted context or contract version unsupported.
