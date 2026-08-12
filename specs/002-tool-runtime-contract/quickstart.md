# Quickstart: Tool Runtime Contract Implementation

## Checkout

```bash
cd /Users/zhou/Code/clawith-worktrees/tool-runtime-contract-repair
git branch --show-current
git log -1 --oneline
```

Expected branch: `002-tool-runtime-contract`; base contains `upstream/main@251aeba8` or a later explicitly rebased upstream main.

## Baseline Evidence (2026-08-10)

- Branch: `002-tool-runtime-contract`
- Base: `251aeba8c36513bcab11b1538ecfd758bdf2cbe4` (`upstream/main`)
- Pre-implementation changes: only SpecKit artifacts and its generated `AGENTS.md` technology context; original checkout changes remain isolated.
- Alembic: one head, `f061_enterprise_info_tenant_id`.
- Architecture guard: passed all P0 checks; repository-wide legacy warnings were present before implementation (direct service selects, physical FKs and oversized files).
- Existing directed coverage includes model/tool step, tool outcome, checkpoint side effects, cancel source, async poll, A2A, command worker and `test_tool_execution.py`.

## Implementation Order

1. Add contract and identity tests before production edits.
2. Add checkpoint `StepToolContext` and stable Call Instance creation.
3. Remove ToolProvider access from new-format Tool Step; add legacy batch resolver.
4. Add DB columns/migration and projection metadata.
5. Add shared validation/authorization/failure envelope.
6. Add repair episode state and uniform Tool repair/retry limit 10 gates.
7. Harden operation deadlines/cancel/lease tests.
8. Add RegisteredTool boundary and migrate representative tools only.

## Scoped Verification

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_agent_runtime_model_step_service.py \
  tests/test_agent_runtime_tool_step_service.py \
  tests/test_agent_runtime_tool_execution.py \
  tests/test_agent_runtime_tool_contracts.py \
  tests/test_agent_runtime_tool_repair_budget.py
.venv/bin/ruff check \
  app/models/agent_tool_execution.py \
  app/services/agent_runtime \
  tests/test_agent_runtime_tool_contracts.py \
  tests/test_agent_runtime_tool_repair_budget.py
.venv/bin/alembic heads
```

Before completion:

```bash
cd /Users/zhou/Code/clawith-worktrees/tool-runtime-contract-repair
bash scripts/arch-guard.sh
cd backend
.venv/bin/python -m pytest tests/test_agent_runtime_*.py
.venv/bin/alembic downgrade -1
.venv/bin/alembic upgrade head
```

## Proof Scenarios

- accepted call survives assignment/enabled/readiness change;
- current actor/resource/credential revocation still blocks before side effect;
- checkpoint restart on another Worker uses the same binding and execution row;
- repeated Provider-local ID in another Assistant Turn does not collide;
- schema failure returns exactly one sanitized Tool Result;
- the 10th repair failure pauses before the next model invocation;
- provider retry, safe replay, pending, cancel and unknown do not increment repair budget;
- lease loss blocks stale settlement; uncertain write is never auto-replayed;
- legacy checkpoint resolves once per pending batch, new checkpoint never uses legacy fallback.

## Completion Evidence (2026-08-11)

### Runtime and Tool regression

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/test_agent_runtime_*.py \
  backend/tests/test_tool_execution.py \
  backend/tests/test_builtin_tool_contracts.py \
  backend/tests/test_agent_tools_legacy_contract_compatibility.py \
  backend/tests/test_agent_tools_remaining_typed_outcomes.py \
  backend/tests/test_agent_tools_typed_content_outcomes.py \
  backend/tests/test_agent_tools_deadlines.py \
  backend/tests/test_llm_single_step.py
```

Result: `834 passed, 3 warnings`. The warnings are existing Pydantic/Lark
deprecations and no test failed.

### Static and architecture checks

```bash
backend/.venv/bin/ruff check --select E9,F63,F7,F82 <all changed Python scopes>
backend/.venv/bin/ruff check \
  backend/app/services/agent_runtime/tool_contracts.py \
  backend/app/services/agent_runtime/tool_registry.py \
  backend/app/services/agent_runtime/tool_repair_budget.py \
  backend/app/services/agent_runtime/tool_validation.py \
  backend/tests/test_agent_runtime_tool_contracts.py \
  backend/tests/test_agent_runtime_tool_execution_migration.py \
  backend/tests/test_agent_runtime_tool_repair_budget.py \
  backend/tests/test_agent_runtime_tool_validation.py \
  backend/alembic/versions/v1_11_3_f062_tool_execution_identity.py
bash scripts/arch-guard.sh
git diff --check
```

Results:

- fatal Ruff checks passed across every changed Python scope;
- full Ruff passed for the new contract/registry/repair/validation modules,
  their focused tests, and migration;
- Architecture Guard passed all P0 checks;
- `git diff --check` passed;
- repository-existing broad Ruff/style debt and Architecture Guard warnings
  remain (import/style findings in legacy large files, direct selects, physical
  foreign keys, and oversized files). They are not introduced as part of this
  contract repair and were not mass-formatted in this focused branch.

### Migration verification

```bash
cd backend
.venv/bin/alembic heads
.venv/bin/python -m pytest -q \
  tests/test_agent_runtime_tool_execution_migration.py \
  tests/test_agent_runtime_tool_contracts.py
.venv/bin/alembic upgrade \
  f061_enterprise_info_tenant_id:f062_tool_execution_identity --sql
.venv/bin/alembic downgrade \
  f062_tool_execution_identity:f061_enterprise_info_tenant_id --sql
```

Results:

- exactly one Alembic head: `f062_tool_execution_identity`;
- migration/contract tests: `9 passed`;
- forward SQL adds nullable `provider_call_id` and `contract_version`;
- reverse SQL drops the two fields in reverse order;
- the local PostgreSQL role cannot create an isolated verification database,
  while the existing `clawith` database is behind current main. Therefore no
  destructive online upgrade/downgrade was run against user data. Both online
  schema-introspection behavior and old-row compatibility are covered by the
  migration tests; both directions also pass Alembic's offline migration path.

### Final consistency checks

- branch: `002-tool-runtime-contract`;
- base: `upstream/main@251aeba8c36513bcab11b1538ecfd758bdf2cbe4`;
- all SpecKit files under `specs/002-tool-runtime-contract/` exist;
- accepted calls persist `contract_version`; legacy calls use an explicit
  `legacy:<tool>:<digest>` contract version and emit
  `legacy_tool_context_resolved` compatibility telemetry;
- legacy deletion remains gated by zero observed legacy batches, one complete
  supported-release interval, and a closed rollback window;
- the original dirty checkout remains separate from this worktree;
- no commit or push was performed.

## Remaining Risks

- Production/provider validation is not part of this local run. Deadline,
  cancellation, unknown-write, credential revocation, and Provider Tool payload
  behavior are covered by deterministic unit/integration doubles, not live
  provider credentials.
- The current Runtime still settles accepted Tool Calls sequentially. The new
  provider `parallel_tool_calls` capability only controls whether a Provider may
  emit more than one call in a response; it does not authorize concurrent
  business execution. `parallel_safe` remains a separate execution-policy fact.
- RegisteredTool migration is intentionally incremental. One builtin read, one
  AgentBay read, and exact-name dynamic MCP contracts use the completeness gate;
  remaining legacy adapters stay observable and hidden when incomplete until
  their contracts are migrated and the deletion gate is satisfied.
