# Clawith Architecture Constitution

> **The single source of truth for Clawith's architectural laws — invariant across all features, never to be violated.**
>
> - `AGENTS.md` and every feature's `design.md` **reference this file; they never copy it.** Changing an implementation never requires editing this file (they point here).
> - `scripts/arch-guard.sh` is the **machine-enforcement arm** of this document: each RULE maps to a clause below.
> - Violations are reported as **BLOCKER** during design/code reviews.

---

## Anchor Table (Clause ↔ arch-guard RULE)

| Clause | Law | arch-guard RULE | Severity |
|---|---|---|---|
| **C1** | Runtime Boundary Isolation (Fact Separation) | `C1-RuntimeIsolation` | VIOLATION |
| **C2** | Strict Multi-Tenant Data Scope | `C2-MultiTenantScope` | VIOLATION |
| **C3** | Idempotent Side Effects & Reconciliation | `C3-IdempotentSideEffects` | VIOLATION |
| **C4** | Client & Gateway Wrapper Enforcement | `C4-NoDirectAxios` | VIOLATION |

---

## C1. Runtime Boundary Isolation (Fact Separation)

Clawith separates four distinct kinds of facts:

1. **Product Records**: Clawith product SQLModel tables (`Tenant`, `User`, `Agent`, `Session`, `Group`, `Permissions`).
2. **Accepted Command Inbox**: `agent_run_commands` table (Accepted `start`, `resume`, `cancel` inputs).
3. **Execution Lifecycle**: LangGraph Checkpoint (PostgreSQL durable checkpoint).
4. **User Delivery**: Product-side idempotent reconciliation and delivery.

### Invariants:
- `backend/app/api/` and channel adapters must only create durable commands via `RuntimeCommandIntake`.
- API endpoints and product services **MUST NOT** invoke graph nodes directly, advance node execution status, or modify checkpoint tables.
- Product projections must **NEVER** become a second Agent execution state machine.

---

## C2. Strict Multi-Tenant Data Scope — Auto-Injected & Explicit Filters

Every database query, Redis cache key, and background worker task MUST explicitly enforce `tenant_id` scoping to prevent cross-tenant data leaks.

- **SQLModel / SQLAlchemy**: Always include `.where(Model.tenant_id == tenant_id)` or ensure tenant context injection via ContextVar.
- **Cache Keys**: Redis keys must be prefixed with `tenant:{tenant_id}:`.
- **Worker Tasks**: Celery/Command Worker tasks must validate `tenant_id` before processing commands.

---

## C3. Idempotent Side Effects & Reconciliation

LangGraph checkpoint commitment is authoritative.

- Command application and product synchronization are distinct facts.
- A committed checkpoint remains authoritative even if product synchronization temporarily fails.
- Product-side projections, notifications, and message delivery MUST be distinct, retryable, and idempotent.

---

## C4. Client & Gateway Wrapper Enforcement

- **Frontend**: Components and pages MUST NEVER `import axios` directly. All HTTP requests must go through the central request wrapper (`src/api/request.ts`).
- **Backend**: Backend code must access external LLM/tools through unified proxy & sandboxed execution environments.

---

## C5. Database & Performance Standards (No Foreign Keys & N+1 Prevention)

- **No Physical Foreign Keys**: Database tables MUST NOT create physical `FOREIGN KEY` constraints at the DB layer. Maintain relationship integrity at the application/SQLModel layer to prevent lock contention and migration deadlocks.
- **Minimize DB JOINs**: Avoid multi-table complex JOINs. Prefer application-level batch querying or indexed lookup tables.
- **N+1 Prevention via Batching**: Eliminate N+1 loop queries. Use batch query APIs (`in_()` clauses, batch load interfaces) or `selectinload` for batch fetching.

---

## C6. Code Modularity & Reusability (Recommended Size Thresholds & Helper Layer)

- **Recommended Size Thresholds (Flexible Guidelines)**:
  - Functions: Recommended ~100 lines. Treat exceeding lines as a signal for refactoring into sub-functions.
  - Backend files: Recommended ~1000 lines (Frontend ~600 lines). Allow flexibility based on context, treating large files as candidates for module splitting.
- **No Wheel Reinvention**: Search existing `app/core/`, `app/utils/`, and `app/helpers/` utilities before writing custom helper code. Extract common logic into reusable `utils/helpers` modules.

