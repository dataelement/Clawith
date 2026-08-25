# Contract: Tool Result and Failure Feedback

## Model-visible Envelope

```json
{
  "role": "tool",
  "tool_call_id": "call_instance_id",
  "name": "tool_name",
  "execution_status": "succeeded|failed|pending|unknown",
  "error_code": "stable_optional_code",
  "content": "bounded sanitized summary",
  "model_action": "continue|repair_arguments|choose_other_tool|ask_user|wait|reconcile",
  "side_effect_state": "none|confirmed|possible|unknown",
  "safe_remediation": "optional bounded instruction",
  "result_ref": "optional opaque reference"
}
```

## Exactly-once Feedback

- A valid Call Instance with a repairable deterministic failure receives one Tool Result.
- Checkpoint replay reuses the deterministic result message ID and Receipt result.
- Invalid/missing Call identity is protocol corruption and cannot invent a Tool Result pairing.

## Classification

| Situation | Runtime state | Count repair? | Automatic replay? |
|---|---|---:|---:|
| Schema/argument failure | failed Tool Result | yes | model decides |
| Deterministic business rejection | failed Tool Result | yes when repairable | model decides |
| Permission/confirmation | waiting | no | no |
| Async operation | pending | no | poll only |
| Durable cancel | cancelled terminal | no | no |
| Possible external write | unknown/reconcile | no | no |
| Provider transport retry | internal | no | bounded safe retry only |

## Sanitization

Never include secrets, plaintext credential/config, complete sensitive arguments, stack traces, unbounded provider bodies or raw exception strings. Error codes are stable product vocabulary; summary and remediation have byte limits.
