# Backend AGENTS.md — Clawith Backend Guidelines

---

## 1. Subsystem Overview

**Stack**: Python 3.11+, FastAPI, SQLModel (SQLAlchemy 2.0+), Alembic, LangGraph, Celery / Worker processes, Pytest.
**Root Spec**: Extended from root [`AGENTS.md`](file:///Users/alex/Documents/Code/dataelem/Clawith/AGENTS.md).

---

## 2. Common Commands

From `backend/` directory:

| Action | Command |
|---|---|
| Run Dev Server | `uv run uvicorn app.main:app --reload --port 8000` |
| Run Unit Tests | `uv run pytest` |
| Run Specific Test File | `uv run pytest tests/test_agent_runtime.py` |
| Run Linter / Format Check | `uv run ruff check .` |
| Run Auto-Fix Linter | `uv run ruff check --fix .` |
| Generate DB Migration | `uv run alembic revision --autogenerate -m "description"` |
| Apply DB Migrations | `uv run alembic upgrade head` |

---

## 3. Python Coding Standards

### 3.1 Import Placement
- **File Header Placement**: All Python imports MUST be placed at the top of the file (file header).
- **No Inline Imports**: Avoid inline/local imports within functions or methods unless strictly necessary (e.g., to break circular import dependencies).

### 3.2 Multi-Tenant Scope (P0 - C2)
- **Mandatory Tenant Filter**: Every database query (`select(...)`), update, or delete MUST explicitly include `tenant_id` scoping to guarantee data isolation.
- **Worker & Context Var**: Ensure background tasks propagate tenant context correctly.

### 3.3 Code Formatting & Type Safety
- **Ruff Compliance**: Code must adhere to Ruff rules (max line length: 120, target-version: `py311`).
- **Type Annotations**: All public functions and endpoint handlers must include explicit type hints for parameters and return values.

### 3.4 Code Splitting Guidelines (C6)
- **Function Length Recommendation**: Recommended ~**100 lines** per function. Treat functions exceeding this size as candidates for refactoring into sub-functions or helper modules (flexible guideline).
- **File Length Recommendation**: Backend Python files recommended ~**1000 lines**. Split oversized files into modular sub-files when reasonable.

### 3.5 Anti-Reinvention & Helper Layer (C6)
- **Search Before Coding**: Check `app/core/`, `app/utils/`, and `app/helpers/` before writing custom helper/utility functions.
- **Extract Common Logic**: Promote reusable operations (formatting, ID generation, string manipulation) into shared `utils/helpers` modules.

### 3.6 Database & Query Performance (C5)
- **No Physical Foreign Keys**: Do not define physical `FOREIGN KEY` constraints at the DB layer. Keep relationship checks at the SQLModel / application layer.
- **Minimize DB JOINs & N+1 Prevention**: Avoid multi-table complex JOINs. Use batch query interfaces (`where(Model.id.in_(ids))` / batch APIs) and `selectinload` to prevent N+1 loop queries.

---

## 4. Subsystem Layout & Architectural Invariants

- `app/api/`: FastAPI endpoints & HTTP/WS adapters.
  - **Rule**: Must NOT invoke LangGraph node executors directly. Must submit commands through `RuntimeCommandIntake`. Must NOT write raw ORM queries; delegate to `app/dao/`.
- `app/dao/`: Data Access Objects (Detailed guidelines → [`app/dao/AGENTS.md`](file:///Users/alex/Documents/Code/dataelem/Clawith/backend/app/dao/AGENTS.md)).
  - **Rule**: Exclusive owner of database queries and persistence. Must enforce `tenant_id` scope.
- `app/services/agent_runtime/`: Core execution boundary.
  - `command_worker.py`: Claims durable commands and executes graph turns.
  - `graph.py`: LangGraph graph topology definition.
- `app/models/`: SQLModel data models.
- `app/services/`: Product domain logic services.

---

## 5. Testing Conventions

- Place unit and integration tests under `tests/`.
- Name test files with `test_` prefix (e.g., `tests/test_runtime_intake.py`).
- Use `@pytest.mark.asyncio` for async test functions.

