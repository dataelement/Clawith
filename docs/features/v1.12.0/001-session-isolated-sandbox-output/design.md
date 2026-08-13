# Session-Isolated Sandbox Output Design

## 1. Status

- Feature: Session-isolated local code execution output
- Spec: [`spec.md`](spec.md)
- Status: Draft for user confirmation
- Constitution: [`docs/constitution.md`](../../../constitution.md)

## 2. Design Summary

This design intentionally does not route a Session to one Runtime Worker. Every Runtime Worker continues to claim durable commands through the existing PostgreSQL Command Inbox.

Local Session code execution adds one narrow distributed coordination primitive:

```text
Redis execution lease
    covers materialize -> bwrap execute -> conditional Workspace publish
```

One bubblewrap child and its writable working copy remain active for the duration
of one Agent loop. The process is closed when that loop settles. Durable
cross-loop continuity still comes only from files under:

```text
workspace/output/{session_id}
```

Guest paths follow the same logical names exposed by Workspace tools. In
particular, `workspace/<path>` maps to `/workspace/<path>`; the Sandbox does not
add a second `workspace` segment. The persistent output path is therefore:

```text
/workspace/output/{session_id}
```

## 3. Current-State Snapshot

### 3.1 Runtime execution

- `RuntimeCommandDaemon` concurrently calls `RuntimeCommandWorker.run_once()`.
- Commands are claimed from PostgreSQL with `FOR UPDATE SKIP LOCKED`.
- The existing PostgreSQL advisory Thread lock serializes one LangGraph Thread.
- `RuntimeToolStepService` executes a model-proposed Tool batch sequentially.
- Different Threads/Runs and legacy/direct Tool entry points can still execute concurrently.

This means Redis is not a new Agent scheduler. It is a defense-in-depth mutex around one shared Session output prefix.

### 3.2 Code execution

The typed Runtime path is currently:

```text
RuntimeToolStepService
  -> execute_builtin_tool_outcome
  -> _run_with_temp_workspace_outcome
  -> _execute_code_outcome
  -> SandboxBackend.execute
```

The legacy path calls the same backend through `execute_tool`. Approved legacy actions can call `_execute_tool_direct`.

### 3.3 Workspace publication

`_prepare_temp_workspace` materializes durable Storage into a temporary Agent root. `flush_temp_workspace` uses Storage version tokens and conditional writes to publish changes. Redis Workspace path locks are an additional short-lived guard, while Storage conditional operations provide the conflict decision.

The local subprocess backend currently creates another staging tree, runs bubblewrap over that staging tree, validates generated files, and copies accepted changes back into the temporary Agent root. The outer temporary Workspace adapter then publishes to durable Storage.

### 3.4 Dirty-worktree integration constraint

At design time, `subprocess_backend.py` and its tests already contain user-owned staged and unstaged changes for staging, pip proxying, output validation, and process reaping. Implementation MUST preserve those changes and layer this feature onto their resulting behavior. It MUST NOT reset or replace the files wholesale.

## 4. Component Design

```text
agent_tools code-execution orchestrator
  ├─ SandboxExecutionLeaseStore       Redis acquire/renew/release
  ├─ SandboxExecutionLease            one held lease + heartbeat
  ├─ SandboxWorkspacePolicy           mode/session/path validation
  ├─ TempWorkspace                    materialize roots + publish roots
  ├─ SubprocessBackend                Agent-loop bwrap + publish enforcement
  └─ flush_temp_workspace             durable conditional publication
```

### 4.1 `SandboxExecutionLeaseStore`

New module:

```text
backend/app/services/sandbox/execution_lease.py
```

Public responsibilities:

```python
class SandboxExecutionLeaseStore:
    async def acquire(scope, *, ttl_seconds) -> SandboxExecutionLease | None: ...

class SandboxExecutionLease:
    async def start_heartbeat() -> None: ...
    async def ensure_publication_window(seconds: int) -> bool: ...
    async def release() -> None: ...
```

The module owns Redis scripts and does not know about LangGraph, Tool receipts, or Workspace files.

### 4.2 `SandboxWorkspacePolicy`

New module:

```text
backend/app/services/sandbox/workspace_policy.py
```

Responsibilities:

- parse `workspace_mode`;
- validate canonical Session UUID;
- derive the durable relative prefix `workspace/output/{session_id}`;
- derive the guest prefix `/workspace/output/{session_id}`;
- declare materialization and publication roots;
- reject unsupported backend/mode combinations.

The model never provides these values. They come from Tool configuration and trusted Runtime/session context.

### 4.3 Code-execution orchestrator

The common orchestration boundary remains in `agent_tools` initially, but the new logic is extracted into small helpers rather than enlarging backend-specific code.

The orchestrator resolves one immutable execution plan before any side effect:

```python
@dataclass(frozen=True)
class ExecuteCodePlan:
    sandbox_config: SandboxConfig
    workspace_mode: Literal["merge", "isolated_output"]
    publication_owner: Literal["gateway", "workspace_cas"]
    tenant_id: UUID | None
    agent_id: UUID
    session_id: UUID | None
    effective_timeout_seconds: int
```

`publication_owner` is a trusted Executor configuration value, not a Tool argument. One Executor resolves exactly one publication owner for the whole invocation:

- `gateway`: the Sandbox gateway performs the durable Workspace mutation and revision recording; the outer temporary Workspace adapter does not publish those files;
- `workspace_cas`: the gateway only validates and copies into `TempWorkspace`; the outer adapter performs version-token conditional publication and any post-commit revision recording.

The two branches are mutually exclusive. Startup/configuration validation rejects an Executor definition that enables both or neither publication paths. The same resolved plan is used for lease TTL, Workspace materialization, backend selection, mount policy, and publication ownership. Configuration is not fetched independently at multiple stages.

For an invocation carrying a Session, the orchestrator also resolves a validated scope:

```python
@dataclass(frozen=True)
class SandboxExecutionScope:
    tenant_id: UUID
    agent_id: UUID
    session_id: UUID
```

The scope resolver parses canonical UUIDs and verifies that the Chat Session belongs to the exact Agent and tenant using an explicitly tenant-filtered DAO query. A missing tenant, missing Session, or ownership mismatch fails closed before lease acquisition, materialization, or code execution. `tenant_id=None` is permitted only for legacy non-Session `merge` invocations that do not create a lease key.

## 5. Redis Lease Contract

### 5.1 Key

```text
tenant:{tenant_id}:sandbox-execution:{agent_id}:{session_id}
```

This is explicit tenant scoping under Constitution C2. All IDs are canonical UUID text.

Only a validated `SandboxExecutionScope` may construct this key. The implementation must never serialize a missing tenant as `tenant:None` and must not accept an arbitrary well-formed Session UUID without verifying tenant and Agent ownership.

### 5.2 Value

The value is an opaque exact-match string:

```text
v1|{executor_instance_id}|{random_lease_token}
```

`executor_instance_id` is process-unique and generated once at process startup from hostname, PID, and a random UUID. The full value is never logged. Logs may include a short SHA-256 correlation hash.

### 5.3 Acquire

Acquire uses one Redis command:

```text
SET key value NX PX 60000
```

The steady-state lease TTL is 60 seconds. An invocation that cannot acquire returns:

```text
error_code = sandbox_session_busy
retryable = true
```

The caller does not block or poll while occupying a Runtime command slot.

### 5.4 Heartbeat

While materialization, execution, or pre-publication processing is active, a background task renews every 20 seconds with an atomic Lua script:

```lua
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
```

Renewal returning `0` means ownership was lost. Redis exceptions mean ownership is unverifiable. Both states are latched on the lease handle.

### 5.5 Publication window

Immediately before durable Workspace publication, the owner runs the same compare-and-renew script with a 120-second TTL and then publishes under a 60-second application timeout.

This ordering closes the check-then-expire race:

```text
atomic owner check + extend to 120 s
  -> publish with 60 s deadline
  -> release
```

Even if the normal heartbeat fails after the extension, another Worker cannot acquire the lease during the bounded publication window.

If the publication-window extension fails, publication does not start and the Tool settles `unknown` if code may already have run.

The 60-second publication timeout covers path-lock acquisition, candidate validation, conditional writes/deletes, revision recording owned by the selected publication path, and result collection. Publication is a multi-file operation and is not transactionally atomic. If timeout, cancellation, Redis uncertainty, or an exception occurs after the first durable mutation may have started, the Tool settles `unknown`, never automatically retries code, and records the known committed, deleted, conflicted, and unverified paths/counts in outcome metadata for reconciliation. Cancellation of the caller does not convert a possibly partial publication into a normal failure.

Workspace path locks used inside publication are updated to accept `tenant_id` and use keys beginning with:

```text
tenant:{tenant_id}:workspace-lock:{agent_id}:{normalized_path}
```

Session-scoped execution must not call the existing unscoped key builder. Owner-only release and Storage CAS remain required; the path lock is not a substitute for either.

### 5.6 Release

Release uses compare-and-delete:

```lua
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
```

Release is shielded during cancellation cleanup. Failure to release after a fully settled publication is logged; TTL provides recovery and does not change the committed Tool outcome.

### 5.7 Why no fencing counter

A monotonic fencing counter is unnecessary in V1 because:

- the lease is renewed immediately before a bounded publication window;
- a second execution cannot acquire during that window;
- whichever publication owner is selected must use conditional version-token writes/deletes and reject stale candidates;
- no long-lived Sandbox resource accepts commands after the invocation.

If publication cannot be bounded or future backends maintain long-lived mutable state, the contract must add durable fencing before supporting that behavior.

## 6. Workspace Mode Configuration

### 6.1 Configuration schema

Add to the `execute_code` built-in definition:

```json
{
  "key": "workspace_mode",
  "label": "Workspace Write Mode",
  "type": "select",
  "default": "merge",
  "options": [
    {"label": "Merge workspace changes", "value": "merge"},
    {"label": "Session output only", "value": "isolated_output"}
  ]
}
```

Add `workspace_mode` to `SandboxConfig` with validation restricted to the two values. `SandboxConfig.from_dict` preserves the existing field-level fallback behavior.

Add the internal trusted `publication_owner` setting to the Executor definition. It is resolved from stored Executor configuration and is not exposed as a model-call argument. The local Executor definition must select exactly one of `gateway` or `workspace_cas`; validation rejects ambiguous values. Seeder/default handling must choose the owner matching the deployed Executor implementation so upgrades never activate two publication paths.

Existing Tools receive `merge` when the field is absent, so no data migration is required. Seeder schema/default merging exposes the option to enterprise and per-Agent configuration UIs, which already render `select` fields generically.

### 6.2 Supported backends

| Backend | `merge` | `isolated_output` |
|---|---:|---:|
| Local subprocess + bubblewrap | Supported | Supported |
| Unsafe local fallback without bubblewrap | Existing development behavior | Rejected |
| Docker | Existing behavior | Rejected in V1 |
| E2B / remote API backends | Existing behavior | Rejected in V1 |

Stable rejection:

```text
error_code = sandbox_workspace_mode_unsupported
retryable = false
```

`execute_code_e2b` does not gain a `workspace_mode` control in V1.

For `isolated_output`, rejection of an unavailable bubblewrap backend takes precedence over `allow_unsafe_fallback_when_bwrap_missing`. Enabling the development fallback cannot weaken the isolated mount contract.

## 7. Temporary Workspace and Publication Roots

### 7.1 Split materialization from publication

`TempWorkspace` currently uses one `selected_paths` collection for both materialization and sync-back. `isolated_output` needs all standard Agent paths readable but only one prefix publishable.

Refactor the object to hold:

```python
materialized_paths: list[str]
publish_paths: list[str]
```

For `merge`:

```text
materialized_paths = existing defaults
publish_paths      = existing defaults
```

For `isolated_output`:

```text
materialized_paths = existing defaults
publish_paths      = ["workspace/output/{session_id}"]
```

The materialization manifest may include all readable files. `flush_temp_workspace` filters both local-file collection and manifest deletion checks to `publish_paths`. A file outside those roots is never written or deleted.

### 7.2 Session output preparation

The exact output directory is created inside the temporary Agent root even when it does not yet exist in Storage. Existing files under the prefix are materialized with Storage version tokens.

Path construction goes through `normalize_workspace_path` plus an exact UUID-derived suffix. No model-provided relative path participates in the writable mount root.

### 7.3 Durable artifact references

Successful conditional writes under the Session output prefix produce the existing form:

```text
workspace://{agent_id}/workspace/output/{session_id}/{relative_file}
```

Deleted files do not produce artifact references. Conflict and unknown outcomes preserve existing Tool ledger semantics.

## 8. Bubblewrap Mount Design

### 8.1 Merge mode

Preserve the current staging behavior:

```text
bind staging-root /workspace
```

The existing output validation/gateway remains responsible for selecting acceptable staged changes before the outer Storage publication.

### 8.2 Isolated output mode

Prepare three host paths:

```text
staging_root/                                  full materialized Agent root
staging_root/workspace/.tmp/                   runtime scripts and pip proxy files
staging_root/workspace/output/{session_id}/    Session output staging
```

Build bubblewrap mounts in this order:

```text
--bind staging_root /clawith
--bind staging_root/workspace /workspace
--bind staging_root/memory /memory
--bind staging_root/skills /skills
--bind staging_root/skills /workspace/skills   # legacy Skill compatibility
--ro-bind persistent_venv /opt/clawith/venv
```

Destination directories are created before bubblewrap starts. The staging tree
is a writable loop-local copy; publication filtering, not a nested read-only
mount, defines persistence.

The execution script moves to:

```text
/workspace/.tmp/_exec_tmp.{py|sh|js}
```

The working directory is `/`, so relative logical Workspace-tool paths map
directly: `workspace/...` is `/workspace/...`, `skills/...` is `/skills/...`,
and `memory/...` is `/memory/...`. Writes outside Session output are allowed in
the loop copy but are discarded at loop settlement.

`/workspace/skills` is a compatibility mount for Skills authored against the
legacy whole-Agent-root-at-`/workspace` layout. The model-facing contract keeps
`/skills` canonical, and new Skills MUST use relative `skills/...` paths or the
canonical absolute `/skills/...` path.

### 8.3 Symlink policy

Materialization and staging MUST not allow a symlink under the writable prefix to target outside that prefix. Before mounting and before publication:

- resolve the host output directory beneath the staging root;
- reject symlink components in the output root;
- skip or reject staged symlink files;
- verify every publication candidate remains beneath the resolved output root.

The working-copy tree may retain safe readable symlinks only where current
Workspace path rules already permit them; no symlink may turn into a
publication escape.

## 9. Sandbox Output Gateway and Publication Ownership

The backend staging gateway is refactored into preparation and publication phases. Preparation accepts explicit allowed publication roots and produces an immutable validated candidate set without durable mutation:

```python
candidates = await gateway.prepare(
    staging_root,
    temp_workspace_root,
    publish_paths=policy.publish_paths,
)
```

For `isolated_output`, all materialized directories in the loop working copy are
readable and writable, but publication scans only
`workspace/output/{session_id}` for creates, modifications, and deletions. It
preserves existing quotas and HTML/SVG sanitization. Other writes remain
ephemeral and are discarded when the Agent loop closes.

Output quotas apply only to the publication delta: newly created or modified
candidate files count toward the 100-file, 50-MB total, and 10-MB single-file
limits. Unchanged materialized files do not consume output quota. Deletions use
a separate 100-file limit so a loop cannot bypass protection with a mass delete.

Publication behavior is exclusive:

- after the publication lease is extended, `publication_owner=gateway` passes the prepared candidate set to the gateway's conditional durable publisher and disables outer sync-back for those roots;
- after the publication lease is extended, `publication_owner=workspace_cas` copies the prepared candidates into `TempWorkspace` without database revision or durable Storage mutation, then the outer adapter owns Storage CAS and records revisions only after the corresponding durable mutation is confirmed.

Both branches preserve path filtering, conditional conflict handling, artifact reporting, and `unknown` settlement. No invocation may execute both branches. Tests must spy on both publication interfaces and prove exactly one receives durable mutation calls for each configuration.

## 10. End-to-End Execution Flow

```text
1. AgentToolExecution receipt is reserved by the existing Runtime Tool service.
2. Resolve ExecuteCodePlan once.
3. Validate backend + workspace_mode + publication_owner and resolve the exact tenant/Agent/Session ownership.
4. For every local Session-scoped `execute_code`, in either `merge` or `isolated_output`, acquire the shared Redis execution lease.
5. Start lease heartbeat.
6. Materialize readable Agent paths and Session output manifest.
7. Create or reuse the Run-scoped backend and bwrap process.
8. Run code and capture exit/output.
9. Gateway validates only policy-allowed staged changes and freezes the publication candidate set without durable mutation.
10. Atomically extend the lease for the bounded publication window.
11. Invoke exactly one selected publication branch using the prepared candidates: gateway-owned conditional publication or outer conditional publication of only TempWorkspace.publish_paths.
12. Compose code status, publication status, and artifact references.
13. Stop heartbeat and compare-delete the lease.
14. Existing Runtime Tool service settles the durable receipt/checkpoint.
```

The lease covers steps 4 through 13. It intentionally includes materialization so the second execution cannot read a snapshot taken before the first execution publishes. A local `merge` call and a local `isolated_output` call with the same validated scope therefore contend on the same key. Only a non-Session `merge` compatibility call omits the lease.

## 11. Entry-Point Coverage

### 11.1 Typed Runtime

`execute_builtin_tool_outcome` uses the new common code-execution orchestrator. Its Runtime `session_id` is resolved through the tenant-filtered scope resolver rather than being trusted solely because it is present. This is the primary supported path.

### 11.2 Legacy caller

The legacy `execute_tool` branch calls the same orchestrator and passes its contextual `session_id` to the same scope resolver. It receives a text rendering only after typed execution semantics are decided.

### 11.3 Approved action

`_execute_tool_direct` gains an optional contextual `session_id`. `AutonomyService` passes the Session from stored `runtime_scope` where available, but the common scope resolver still verifies tenant and Agent ownership before treating it as trusted.

An approved action configured for `isolated_output` but lacking an exact Session fails with `sandbox_session_required`; it never writes to a shared Agent output directory.

### 11.4 Non-Session sources

`merge` remains available without a Session and does not invent an execution lease scope. `isolated_output` requires a Session and fails closed.

## 12. Outcome and Error Contract

| Situation | Status | Error code | Retryable |
|---|---|---|---:|
| Execution lease occupied | failed | `sandbox_session_busy` | true |
| Redis unavailable before code | failed | `sandbox_coordination_unavailable` | true |
| Missing tenant for Session execution | failed | `sandbox_execution_scope_invalid` | false |
| Session does not belong to tenant/Agent | failed | `sandbox_execution_scope_invalid` | false |
| Isolated mode without Session | failed | `sandbox_session_required` | false |
| Unsupported backend/mode | failed | `sandbox_workspace_mode_unsupported` | false |
| Lease lost before code starts | failed | `sandbox_execution_lease_lost` | true |
| Lease lost/unverifiable after code may run | unknown | `sandbox_execution_lease_lost` | false |
| Code exits non-zero, publication succeeds | failed | `sandbox_execution_failed` | existing policy |
| Code ran, publication conflicts/unprovable | unknown | existing `workspace_sync_conflict` / `workspace_sync_outcome_unknown` | false |
| Publication timed out or may be partial | unknown | `workspace_sync_outcome_unknown` | false |

For a failed code invocation whose isolated artifacts publish successfully, the returned `ToolExecutionOutcome` remains failed but includes those artifact references and publication counts in metadata.

## 13. Cancellation and Cleanup

- bubblewrap remains `--die-with-parent` and is launched in a new process session;
- timeout/cancellation terminates and reaps the process group using the existing in-progress process-reaping changes;
- heartbeat shutdown and owner-only lease release run in `finally`;
- temporary Workspace cleanup occurs only after publication has settled or been abandoned;
- no WebSocket disconnect handler writes Workspace files;
- no Worker-local Session timer owns durable output.

## 14. Seven-Day Retention Decision

V1 establishes the durable path but does not implement physical seven-day deletion.

A correct physical TTL requires at least:

- a durable `sandbox_output_expires_at` product fact scoped to the Chat Session;
- a tenant-aware cleanup scanner;
- acquisition of the same Session execution lease before deletion;
- per-file conditional deletion and conflict recovery;
- audit/version behavior for automated deletion.

Using Redis expiry or a Worker-local timer would make deletion non-durable and inconsistent across restarts, violating the requested architecture boundary. Therefore V1 leaves output durable until the retention feature is separately implemented. No UI will claim that automatic deletion is active.

## 15. Rollout

1. Add `workspace_mode` with default `merge`.
2. Add a validated internal `publication_owner` default matching the deployed local Executor's single durable publication implementation.
3. Keep all existing Agents on `merge` after seeding.
4. Enable `isolated_output` per Executor Code configuration.
5. Reject unsupported backends and ambiguous publication-owner configuration explicitly.
6. No Runtime graph version or checkpoint migration is required.
7. Rollback consists of selecting/defaulting to `merge` while retaining one valid publication owner; no durable command or file migration is needed.

## 16. Constitution Check

### C1 — Runtime Boundary Isolation

Pass.

- PostgreSQL `agent_run_commands` remains the accepted command authority.
- LangGraph checkpoint remains the execution lifecycle authority.
- Redis lease is only a volatile Tool concurrency guard.
- No product projection or Redis state advances Agent lifecycle.

### C2 — Strict Multi-Tenant Scope

Pass with required implementation checks.

- Redis lease keys explicitly begin with `tenant:{tenant_id}:`.
- Tenant, Agent, and Session identities are resolved into a non-null scope before Session execution.
- The Session lookup must match tenant, Agent, and Session together and include an explicit `tenant_id` filter.
- Session-scoped Workspace lock keys also begin with `tenant:{tenant_id}:`; the legacy unscoped helper is not used by this flow.
- Workspace storage remains Agent-prefixed and output is further Session-prefixed.

### C3 — Idempotent Side Effects and Reconciliation

Pass.

- `AgentToolExecution` remains the durable side-effect receipt.
- Redis does not decide whether a Tool succeeded.
- Trusted Executor configuration selects exactly one durable publication owner for an invocation; gateway and outer Workspace CAS publication cannot both run.
- The selected owner preserves conditional conflict handling and never silently overwrites a newer durable file.
- Partial or unprovable multi-file publication settles `unknown` with reconciliation metadata.
- Post-dispatch uncertainty settles as `unknown` rather than automatic replay.

### C4 — Gateway Wrapper Enforcement

Pass.

- Code continues through the unified Sandbox backend.
- `isolated_output` strengthens the local filesystem gateway.
- No new direct external provider client is introduced.

### C5 — Database and Performance

Pass for V1.

- No database schema or query is added for the deferred retention feature.
- Materialization remains bounded by existing file/total-size limits.
- No physical foreign key is introduced.

### C6 — Modularity and Reuse

Pass with implementation constraint.

- Redis ownership logic is isolated in `execution_lease.py`.
- Workspace policy/path logic is isolated in `workspace_policy.py`.
- Existing Storage CAS and Workspace helpers are reused.
- Backend staging validation is parameterized instead of adding another publication implementation.

## 17. Known Gotchas

1. **Workspace path identity:** Workspace-tool `workspace/<path>` maps directly to guest `/workspace/<path>`.
2. **Two publication implementations:** trusted Executor configuration must select exactly one durable owner; runtime assertions prevent gateway and outer Workspace publication from both running.
3. **Manifest deletion filtering:** filtering only newly collected files is insufficient; deletion checks must also filter the manifest by `publish_paths`.
4. **Loop cleanup:** the command boundary must reap the bwrap process and discard non-published working-copy changes.
5. **Pip proxy:** runtime `.tmp` remains under `/workspace/.tmp` in the loop copy.
6. **Lease timing:** checking a 60-second lease and then starting publication is racy; publication requires atomic extension plus a shorter bounded deadline.
7. **Legacy approvals:** approved execution may lack Session context; isolated mode must fail closed rather than guess.
8. **Remote backends:** their filesystem contract cannot be inferred from the local bubblewrap interface.
9. **Dirty worktree:** process-reaping and staging changes already in progress must be preserved during implementation.
10. **Scope validation:** canonical UUID syntax is insufficient; tenant, Agent, and Session ownership must be verified together before any Session lease or output path is constructed.
11. **Partial publication:** a timeout after the first conditional mutation is an `unknown` outcome with reconciliation metadata, not a retryable failure.
12. **Legacy Skill paths:** existing Skill packages may hard-code `/workspace/skills`; keep the compatibility mount while prompting new code to use canonical `/skills` paths.

## 18. Verification Design

### Lease tests

- acquire succeeds once and contending executor receives busy;
- different tenant/Agent/Session scopes do not collide;
- missing tenant and cross-tenant/cross-Agent Session identities fail before Redis or code execution;
- foreign token cannot renew or release;
- heartbeat latches renewal failure;
- publication extension uses owner comparison;
- Redis exceptions fail closed;
- cancellation performs owner-only release;
- `merge` and `isolated_output` calls for the same Session contend on the same lease key.

### Workspace policy tests

- valid UUID Session derives exact relative and guest paths;
- missing/invalid Session is rejected for isolated mode;
- traversal and separator variants cannot alter the prefix;
- backend/mode compatibility is explicit;
- `isolated_output` rejects missing bubblewrap even when unsafe fallback is enabled.

### Bubblewrap tests

- logical `workspace/<path>` maps to guest `/workspace/<path>`;
- logical `skills/<path>` maps to guest `/skills/<path>`, while legacy `/workspace/skills/<path>` resolves to the same staged Skill tree;
- materialized directories are writable in the loop copy;
- one bwrap process is reused across code calls in one Agent loop;
- loop settlement reaps that process;
- script runs from `.tmp`;
- writes outside output remain available during the loop but are not published;
- writes inside output succeed.

### Publication tests

- materialize all default readable roots but publish only Session output;
- creates, modifications, and deletions under Session output use conditional Storage operations;
- changes outside prefix are ignored and not deleted;
- failed code can publish diagnostic output while remaining failed;
- conflict produces unknown and no silent overwrite;
- candidate preparation performs no durable mutation before the publication-window lease extension succeeds;
- publication timeout after a simulated first-file commit produces unknown with partial/unverified metadata and never re-executes code;
- Session-scoped Workspace locks use tenant-prefixed keys;
- each publication-owner configuration invokes exactly one durable mutation interface and never both;
- Worker A publish followed by logical Worker B materialization reads the same durable file.

### Regression tests

- existing `merge` behavior;
- current Sandbox process timeout/cancellation tests;
- typed E2B outcome tests;
- Runtime Tool ledger and unknown-outcome tests;
- `scripts/arch-guard.sh`;
- targeted Ruff checks for changed modules.
