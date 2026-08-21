# Data Model: Vercel Async Deployment Wait Recovery

No schema migration or new persistent entity is required. The feature uses the existing Tool execution
receipt and Runtime checkpoint.

## Deployment Operation Receipt

Represents the original Vercel deployment and every subsequent status observation.

| Field | Meaning | Validation |
| --- | --- | --- |
| `operation_id` | Vercel deployment ID | Non-empty, stable across polls |
| `operation_key` | Runtime settlement correlation | Non-empty, stable for one deployment |
| `state` | Latest normalized Vercel `readyState` | Known non-terminal or terminal state |
| `poll.tool` | Internal Tool continuation | `vercel_deploy` |
| `poll.arguments.operation` | Execution mode | `poll` |
| `poll.arguments.deployment_id` | Exact deployment to read | Equals `operation_id` |
| `poll.interval_ms` | Next scheduled observation | `2000` |
| `runtime_async_pending` | Whether more observations are required | `true` for non-terminal, `false` for terminal |

## State Transitions

```text
INITIALIZING ─┐
QUEUED       ─┼─> pending ─> exact poll ─> pending or terminal
BUILDING     ─┘

READY     -> succeeded
ERROR     -> failed
CANCELED  -> failed
unknown or invalid observation -> unknown, never succeeded
```

## Relationships

- One Agent Run contains the original deployment Tool execution.
- Each scheduled poll creates or consumes a Tool execution associated with the same Run.
- All receipts for one deployment share `operation_key`.
- A terminal poll atomically settles the current poll and prior pending receipts with that key.
