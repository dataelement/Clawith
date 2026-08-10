# Contract: Vercel Declared Async Operation

## Pending outcome

```json
{
  "status": "pending",
  "result_ref": null,
  "metadata": {
    "provider": "vercel",
    "deployment_id": "dpl_xxx",
    "deployment_state": "BUILDING",
    "runtime_async_pending": true,
    "async_operation": {
      "version": 1,
      "operation_id": "dpl_xxx",
      "operation_key": "vercel:deployment:dpl_xxx",
      "state": "BUILDING",
      "poll": {
        "tool": "vercel_deploy",
        "arguments": {
          "operation": "poll",
          "deployment_id": "dpl_xxx"
        },
        "interval_ms": 2000
      }
    }
  }
}
```

## Terminal outcome

The terminal result MUST retain the same operation key and set `runtime_async_pending` to `false`.

| Provider state | Tool status |
| --- | --- |
| `READY` | `succeeded` |
| `ERROR` | `failed` |
| `CANCELED` | `failed` |
| missing, unknown, or mismatched observation | `unknown` |

## Prohibited behavior

- A poll MUST NOT call any project-create, file-upload, repository-link, or deployment-create endpoint.
- A poll MUST NOT use project deployment-list results for settlement.
- A non-terminal state or transient status-read timeout MUST NOT produce `succeeded`.
- The contract MUST NOT require a Model-generated wait or polling Tool call.
