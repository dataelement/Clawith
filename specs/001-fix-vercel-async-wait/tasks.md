# Tasks: Vercel Async Deployment Wait Recovery

**Input**: Design documents from `/specs/001-fix-vercel-async-wait/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Regression tests are required by the feature specification and Constitution.

## Phase 1: Baseline

**Purpose**: Confirm the existing Vercel and generic Runtime contracts before editing production code.

- [x] T001 Run the current scoped baseline in `backend/tests/test_agent_tools_typed_vercel_deploy.py`, `backend/tests/test_agent_runtime_async_tool_poll.py`, and `backend/tests/test_agent_runtime_tool_step_service.py`

---

## Phase 2: User Story 1 - Receive the Final Deployment Result (Priority: P1) 🎯 MVP

**Goal**: Keep non-terminal Vercel deployments pending and settle the original operation when the
exact deployment reaches READY, ERROR, or CANCELED.

**Independent Test**: A scripted deployment progresses BUILDING → READY; the initial result is
pending, the internal poll reads the exact deployment, and the terminal result carries the same
operation key for existing Runtime settlement.

### Tests for User Story 1

- [x] T002 [US1] Replace the accepted-BUILDING success expectation with pending-contract and internal-poll terminal cases in `backend/tests/test_agent_tools_typed_vercel_deploy.py`

### Implementation for User Story 1

- [x] T003 [US1] Add the minimal Vercel provider-state helper, internal poll branch, pending outcome, and terminal operation metadata in `backend/app/services/agent_tools.py`
- [x] T004 [US1] Prove the existing Runtime consumes the Vercel pending and terminal contracts using scoped coverage in `backend/tests/test_agent_runtime_tool_step_service.py` or an existing equivalent test

**Checkpoint**: BUILDING remains pending, READY succeeds, ERROR/CANCELED fail, and the original Run
can continue through the existing Runtime.

---

## Phase 3: User Story 2 - Avoid Duplicate Deployments (Priority: P2)

**Goal**: Ensure every continuation performs only an exact status read for the original deployment.

**Independent Test**: Multiple internal polls issue zero project creates, uploads, repository links,
or deployment POSTs and always use the original deployment ID.

### Tests for User Story 2

- [x] T005 [US2] Add assertions that internal polls perform only exact deployment GET requests and never repeat external writes in `backend/tests/test_agent_tools_typed_vercel_deploy.py`

### Implementation for User Story 2

- [x] T006 [US2] Verify the internal poll discriminator branches before launch validation and all external write stages in `backend/app/services/agent_tools.py`

**Checkpoint**: One user request produces exactly one Vercel deployment POST regardless of poll count.

---

## Phase 4: Validation

**Purpose**: Prove the stopgap and enforce the approved diff boundary.

- [x] T007 Run scoped pytest for `backend/tests/test_agent_tools_typed_vercel_deploy.py`, `backend/tests/test_agent_runtime_async_tool_poll.py`, and `backend/tests/test_agent_runtime_tool_step_service.py`
- [x] T008 Run scoped Ruff on `backend/app/services/agent_tools.py` and modified test files, then verify generic Runtime production files are unchanged

---

## Dependencies & Execution Order

- T001 establishes the baseline.
- T002 must precede T003 so the regression is observable before implementation.
- T003 enables T004 and T005.
- T005 validates T006; both use the same source and test files, so they run sequentially.
- T007 and T008 run after all implementation tasks.

## Implementation Strategy

Implement only User Story 1 and User Story 2 as one minimal stopgap. Do not add a new scheduler,
deadline, backoff policy, cancellation path, public Tool, or generic wait rule. Stop if the existing
Runtime contract cannot consume the declared async outcome without production Runtime changes and
report that evidence before expanding scope.
