# AGENTS.md — Clawith Agent Governance & Architecture Guidelines

---

## 1. Project Identity

**Clawith** — Multi-tenant Enterprise Agent Application Platform.
Repository architecture and invariants defined in [`ARCHITECTURE_SPEC_EN.md`](file:///Users/alex/Documents/Code/dataelem/Clawith/ARCHITECTURE_SPEC_EN.md).

### Core Stack & Layout
| Path | Component | Stack | Responsibilities |
|---|---|---|---|
| `backend/` | Product API & Runtime | Python 3.11+, FastAPI, SQLModel (PostgreSQL), LangGraph, Celery/Worker | API adapters, tenant isolation, durable execution state, message delivery |
| `frontend/` | Web Interface | React 18, TypeScript, Vite, Tailwind CSS, shadcn/ui | End-user agent interaction, workspace, chat, session management |

### Separation of Four Kinds of Facts (Separation Principle)
1. **Product Records**: Owner = Clawith product tables (Tenant, User, Agent, Session, Group, Permissions).
2. **Accepted Command Inbox**: Owner = `agent_run_commands` table (Accepted start, resume, cancel inputs).
3. **Execution Lifecycle**: Owner = LangGraph Checkpoint (PostgreSQL durable checkpoint).
4. **User Delivery**: Owner = Product-side idempotent reconciliation and delivery.

> **CRITICAL INVARIANT (C1)**: Product projections must **NEVER** become a second Agent execution state machine. API endpoints and product services must not mutate checkpoint lifecycle fields directly or implement private execution control loops.

---

## 2. P0 Architectural Constitution Rules

The single source of truth for architectural laws is [`docs/constitution.md`](file:///Users/alex/Documents/Code/dataelem/Clawith/docs/constitution.md) (enforced by `scripts/arch-guard.sh`). Do not copy these laws here — link to them:

- **C1: Runtime Boundary Isolation** → [`docs/constitution.md#C1`](file:///Users/alex/Documents/Code/dataelem/Clawith/docs/constitution.md#c1-runtime-boundary-isolation-fact-separation)
- **C2: Strict Multi-Tenant Data Scope** → [`docs/constitution.md#C2`](file:///Users/alex/Documents/Code/dataelem/Clawith/docs/constitution.md#c2-strict-multi-tenant-data-scope--auto-injected--explicit-filters)
- **C3: Idempotent Side Effects & Reconciliation** → [`docs/constitution.md#C3`](file:///Users/alex/Documents/Code/dataelem/Clawith/docs/constitution.md#c3-idempotent-side-effects--reconciliation)
- **C4: Client & Gateway Wrapper Enforcement** → [`docs/constitution.md#C4`](file:///Users/alex/Documents/Code/dataelem/Clawith/docs/constitution.md#c4-client--gateway-wrapper-enforcement)
- **C5: Database & Performance Standards** → [`docs/constitution.md#C5`](file:///Users/alex/Documents/Code/dataelem/Clawith/docs/constitution.md#c5-database--performance-standards-no-foreign-keys--n1-prevention)
- **C6: Code Modularity & Reusability** → [`docs/constitution.md#C6`](file:///Users/alex/Documents/Code/dataelem/Clawith/docs/constitution.md#c6-code-modularity--reusability-recommended-size-thresholds--helper-layer)

---

## 3. Quick Command Reference

Dev and test commands live in sub-project instruction files:
- Backend: `backend/AGENTS.md` (Server start, Alembic migrations, Pytest, Ruff)
- Frontend: `frontend/AGENTS.md` (Vite dev server, type-check, lint, build)

---

## 4. SDD Workflow (Specification-Driven Development)

For non-trivial features or architecture refactoring, follow this workflow:

```text
1. Spec Discovery                          → ★ User Confirms
2. spec.md   → /sdd-review <dir> spec       → ★ User Confirms
3. design.md → /sdd-review <dir> design     → ★ User Confirms (Constitution Check)
4. tasks.md  → /sdd-review <dir> tasks
5. Branch feat/{NNN}-{name}
6. Implement Wave-by-Wave & Run unit tests → /task-review
7. Run scripts/arch-guard.sh & test suite
8. /code-review --base main
```
*Note: ★ indicates mandatory user confirmation gates.*

---

## 5. Instruction File Mapping (AGENTS.md Hierarchy)

- **Root `AGENTS.md`** (This file): Single source of truth for global constitution, architecture topology, SDD workflow, and P0 rules.
- **[`backend/AGENTS.md`](file:///Users/alex/Documents/Code/dataelem/Clawith/backend/AGENTS.md)**: Backend-specific coding standards, Python import rules, database access guidelines.
- **[`backend/alembic/AGENTS.md`](file:///Users/alex/Documents/Code/dataelem/Clawith/backend/alembic/AGENTS.md)**: Database migration standards, timestamp conventions, lock safety.
- **[`frontend/AGENTS.md`](file:///Users/alex/Documents/Code/dataelem/Clawith/frontend/AGENTS.md)**: Frontend-specific coding standards, React/TS guidelines, HTTP wrapper usage.

> **RULE**: Sub-directory `AGENTS.md` files extend root guidelines. Never duplicate root rules in sub-files. If a rule spans multiple components, put it here.
