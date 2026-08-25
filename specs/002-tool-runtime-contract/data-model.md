# Data Model: Tool Runtime Contract

## Identity Model

| Identity | Scope | Authority | Purpose |
|---|---|---|---|
| `provider_call_id` | Provider assistant response | Provider wire protocol | Assistant/tool response pairing and diagnostics only |
| `call_instance_id` | One accepted Assistant Tool Call inside a Run | Clawith Model Step | Checkpoint, message, Activity, Chat and A2A correlation |
| `execution_id` | One durable Receipt row | PostgreSQL `AgentToolExecution.id` | Lease, attempt, result archive, async poll and reconciliation |
| `business_idempotency_key` | Provider/business operation | Tool adapter/provider | External side-effect deduplication when supported |

Compatibility mapping: current DB column `tool_call_id` stores `call_instance_id`. It is not renamed in the first migration.

## Checkpoint Entities

### StepToolContext

```json
{
  "version": 1,
  "assistant_message_id": "...",
  "model_step": 3,
  "workset_version": "sha256:...",
  "accepted_calls": [
    {
      "call_instance_id": "...",
      "provider_call_id": "...",
      "tool_name": "read_document",
      "contract_version": "builtin:read_document:v2",
      "schema": {},
      "binding": {},
      "effect": "read",
      "retry_policy": "safe"
    }
  ]
}
```

Invariants:

- one context belongs to exactly one Assistant message;
- `call_instance_id` is unique inside the Run and stable across replay;
- `provider_call_id` may be null for legacy/provider compatibility;
- schema/binding are JSON serializable, bounded and secret-free;
- new checkpoint pending calls must have a matching accepted call entry.

### ToolWorksetEntry

Fields:

- `tool_name`: model-visible name;
- `contract_version`: immutable schema/behavior version;
- `parameters_schema`: accepted model schema;
- `binding`: stable handler/provider target;
- `effect`: `read | write | external_write`;
- `retry_policy`: `safe | conditional | never`;
- `authorization_policy`: stable policy key;
- `deadline_policy`: stable policy key;
- `recovery_policy`: stable policy key.

### ExecutionBinding

Allowed forms:

- builtin: `{kind: "builtin", handler_key: "read_document"}`;
- MCP: `{kind: "mcp", server_id, mcp_tool_name, credential_ref}`;
- group/A2A/AgentBay: stable adapter key plus resource reference.

Forbidden fields: plaintext credentials, bearer tokens, decrypted config, live client objects, Python callable names that are not registry keys.

### RepairEpisode

```json
{
  "tool_name": "read_document",
  "episode_id": "...",
  "total_failures": 7,
  "last_fingerprint": "schema_validation:missing:path",
  "same_fingerprint_failures": 3,
  "last_call_instance_id": "...",
  "updated_at_model_step": 8
}
```

Transitions:

- count: model-visible and repairable `failed` result;
- reset all for tool: same Tool succeeds or user explicitly corrects the request;
- reset new Run: checkpoint starts empty;
- fingerprint change: reset only `same_fingerprint_failures` to 1;
- exclude: provider retry, safe replay, approval wait, pending, cancel, unknown.

## PostgreSQL Changes

### `agent_tool_executions`

Add nullable columns:

- `provider_call_id VARCHAR(255)`;
- `contract_version VARCHAR(255)`.

Keep:

- primary key `id` as `execution_id`;
- unique `(run_id, tool_call_id)` as Call Instance uniqueness;
- existing attempt, effect, retry, status, result and lease columns.

No physical foreign keys are added. A non-unique tenant/run/provider index is optional only if observed diagnostics require it; first migration omits it to minimize write cost.

## State Ownership

- Workset/context/repair episode: LangGraph checkpoint, because they control execution transition.
- Receipt/result/lease: `AgentToolExecution`, because they are durable side-effect facts.
- Activity/Chat: idempotent projections, never authority for resume or repair counts.

## Compatibility

- Legacy Receipt row with null new fields remains readable.
- Legacy checkpoint without `StepToolContext` enters one-batch resolver and marks telemetry.
- New checkpoint with missing/mismatched context is corruption; it cannot silently fall back.
- Deletion of compatibility code requires zero observed uses across retention and rollback windows plus restore fixtures.
