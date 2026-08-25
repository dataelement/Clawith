# Research: Vercel Async Deployment Wait Recovery

## Decision 1: Reuse the declared asynchronous Tool contract

**Decision**: Emit the existing `runtime_async_pending + async_operation` metadata from
`vercel_deploy` for non-terminal provider states.

**Rationale**: The Runtime already persists due times, schedules idempotent timer resumes, reconstructs
poll calls, and atomically settles all same-Run receipts sharing an operation key.

**Alternatives considered**:

- Restore a blocking `while + sleep` loop: rejected because it occupies a Tool worker and loses the
  durable restart behavior introduced by the Runtime.
- Add a Vercel-specific scheduler: rejected because it duplicates an existing generic mechanism.
- Let the Model call `wait(external)`: rejected because that wait has no guaranteed pending operation
  or resume producer.

## Decision 2: Use the exact deployment identifier

**Decision**: Poll `GET /v13/deployments/{deployment_id}` and keep one stable operation key derived
from that deployment identity.

**Rationale**: The create response already supplies the identity. Project deployment lists cannot
prove which deployment belongs to the original Tool operation.

**Alternatives considered**:

- `vercel_list_deployments`: rejected because list ordering and concurrent deployments make the
  correlation ambiguous.

## Decision 3: Keep polling internal and fixed-interval

**Decision**: The Runtime-generated poll invokes an internal `vercel_deploy` mode with the existing
two-second interval. Known provider-pending states may continue polling, while consecutive status-read
failures are capped at the Runtime safe-read limit of 10 and reset after a successful observation. The
public Model-facing deployment request remains unchanged.

**Rationale**: This is the smallest compatible change and prevents Model turns between polls.

**Alternatives considered**:

- Publicly expose a new status Tool or operation discriminator: rejected as unnecessary API expansion.
- Add adaptive backoff or an overall provider-pending deadline: deferred because the approved scope is
  production stopgap, not Runtime redesign. Exhausted status-read failures enter reconciliation rather
  than declaring the external deployment failed without provider proof.

## Decision 4: Preserve provider truth at terminal settlement

**Decision**: READY succeeds; ERROR and CANCELED fail; known non-terminal states remain pending.
Missing, unknown, or mismatched state never succeeds.

**Rationale**: Acceptance of a deployment request is not proof that the deployment completed.

**Alternatives considered**:

- Treat accepted or BUILDING as success: rejected because it caused the reported stuck Run.
