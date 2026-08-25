# DAO Layer AGENTS.md — Clawith Data Access Object Guidelines

> Auto-loads when editing files under `backend/app/dao/`.
> Read this **before** creating or refactoring DAO classes.
> Complements [`backend/AGENTS.md`](file:///Users/alex/Documents/Code/dataelem/Clawith/backend/AGENTS.md) and [`docs/constitution.md`](file:///Users/alex/Documents/Code/dataelem/Clawith/docs/constitution.md).

---

## 1. Subsystem Purpose & Layering Rules

The DAO layer (`backend/app/dao/`) is the sole owner of database persistence, query building, and ORM operations in Clawith.

```text
API Endpoints / Services  ───>  DAO Layer (app/dao/)  ───>  PostgreSQL (SQLModel / SQLAlchemy)
```

### Mandatory Layering Rules:
- **No Direct ORM Queries in API/Service**: API Endpoints (`app/api/`) and Services (`app/services/`) MUST NOT construct raw `select(...)` or execute direct ORM queries. All database operations MUST pass through an explicit DAO class method.
- **No Business Logic in DAO**: DAO classes must restrict their scope to DB reads, writes, filtering, sorting, and joins. Business validation and domain workflows belong in the Service layer.

---

## 2. Multi-Tenant Scoping (P0 - Constitution C2)

- **Mandatory `tenant_id` Filter**: Every DAO query for a tenant-scoped model MUST explicitly enforce `tenant_id` filtering:
  ```python
  stmt = select(self.model).where(
      self.model.id == record_id,
      self.model.tenant_id == tenant_id
  )
  ```
- **No Unscoped Batch Operations**: Operations like `get_all()`, `bulk_update()`, or `delete()` on tenant-scoped models MUST require a valid `tenant_id`.

---

## 3. Session & Transaction Management

DAO methods inherit session management from `BaseDAO` (`app/dao/base.py`):

### 3.1 Read-Only vs Read-Write Sessions
- **Read Operations**: Always pass `readonly=True` to `self.session()` to avoid unnecessary transaction commit overhead.
  ```python
  async with self.session(readonly=True) as db:
      result = await db.execute(stmt)
      return result.scalars().all()
  ```
- **Write Operations**: Use `readonly=False` (default). In multi-step DAO operations within a Service, use `await db.flush()` rather than immediate `commit()`, allowing the parent Service context to manage transaction commit/rollback atomically.

### 3.2 Session Context Inheritance
`BaseDAO` utilizes `_session_ctx` to reuse an active AsyncSession created by an upstream Service transaction, preventing nested transaction conflicts.

---

## 4. Query Performance & Anti-Patterns

### 4.1 N+1 Query Prevention & Batch Interfaces
- For models with relationships, explicitly specify loading strategies (`selectinload` or `joinedload`) instead of relying on lazy loading during async execution.
- **Batch Interfaces**: In N+1 scenes, provide explicit batch query methods (e.g., `get_by_ids(ids: Sequence[str], tenant_id: str)`) that query with `where(Model.id.in_(ids))` in a single query rather than making loop queries.

### 4.2 Minimize DB JOINs & Avoid Physical Foreign Keys (C5)
- **No Physical DB Foreign Keys**: Do NOT create physical `FOREIGN KEY` constraints at the DB level. Use logical `Relationship` mapping in SQLModel without DB DDL FK constraints to prevent migration locks and deadlocks.
- **Minimize DB JOINs**: Avoid multi-table complex JOINs. Prefer indexed batch queries or application-level aggregation.

### 4.3 Pagination & Size Recommendations (C6)
- Methods returning lists MUST support offset/limit or cursor pagination. Hardcoded unlimited queries on large tables are forbidden.
- DAO methods are recommended to stay around ~**100 lines**. Refactor complex SQL builders or multi-step logic into helper methods when reasonable.

---

## 5. Exception & Return Value Standards

- **Single Record Return**: Return `Model | None` when querying by ID or unique keys. Do NOT raise HTTP 404 inside DAO methods; let the API layer handle HTTP status codes.
- **List Return**: Return `Sequence[Model]` (or an empty list `[]` when no records match).
- **No Silent Exception Swallowing**: Exceptions during DB execution MUST NOT be swallowed with `except: pass`. Allow SQLAlchemy errors to propagate or log with `logger.exception()` before re-raising.

---

## 6. Cross-DAO Calls — Prohibition & Allowed Patterns

### 6.1 Prohibition
DAO methods **MUST NOT** call another DAO instance. This prevents session nesting, circular dependencies, and obscures who owns the transaction.

```python
# ❌ FORBIDDEN — GroupDAO calling AgentDAO
class GroupDAO(TenantScopedBaseDAO[Group]):
    async def get_group_with_agents(self, group_id):
        group = await self.get_active(group_id)
        agents = await agent_dao.list_by_ids(...)  # ← VIOLATION
```

### 6.2 Allowed: SQL JOIN Within Same DAO
Multi-table SQL JOINs inside the **same DAO** file are allowed and preferred over cross-DAO calls for read-heavy queries.

```python
# ✅ OK — join within AgentDAO
stmt = (
    select(Agent)
    .join(AgentPermission, Agent.id == AgentPermission.agent_id)
    .where(...)
)
```

### 6.3 Allowed: Service-Layer Coordination
Cross-entity workflows belong in the Service layer, which coordinates multiple DAOs:

```python
# ✅ OK — Service orchestrates two DAOs
class GroupChatService:
    async def create_group_with_session(self, ...):
        group = await group_dao.create(...)          # DAO 1
        session = await chat_session_dao.create(...) # DAO 2
```

### 6.4 Allowed: Helper submodels in same DAO file
A DAO file may contain methods for closely related sub-models (e.g. `AgentDAO` handles `AgentPermission`) as long as they share the same domain boundary.

---

## 7. Transaction Management

### 7.1 Default: Autonomous Flush (Non-transactional)
Most single-step write operations use the default behavior: DAO flushes, BaseDAO commits automatically on exit.

```python
async def create_agent(self, ...) -> Agent:
    async with self.session() as db:  # auto-commit on clean exit
        obj = Agent(...)
        db.add(obj)
        await db.flush()
        return obj
```

### 7.2 Multi-step Atomic Writes: Session Context Inheritance
For cross-DAO atomic operations, the Service layer creates a session and passes it via `_session_ctx` ContextVar. All DAO calls within the `async with` block reuse the same session.

```python
# Service layer — use database.transaction() for atomicity
from app.database import transaction

async def create_group_with_agents(self, ...):
    async with transaction() as db:           # one outer session
        group = await group_dao.create(...)   # reuses session via _session_ctx
        session = await chat_session_dao.create(...)  # same session
        # commit happens only here on clean exit
```

### 7.3 Rule: flush() in DAO, commit() in database.transaction()
- DAO methods always `flush()` — never `commit()` directly.
- Only `BaseDAO.session()` (when it creates a new outer session) and `database.transaction()` issue `commit()`.
- This ensures Service-layer atomicity without leaking transaction responsibility into DAOs.

---

## 8. Tenant Isolation — TenantScopedBaseDAO Contract

All DAOs for models with a `tenant_id` column **MUST** inherit `TenantScopedBaseDAO` instead of `BaseDAO`.

### 8.1 Mandatory Methods
| Method | Description |
|---|---|
| `get_scoped(id)` | Fetch by PK, auto tenant filter |
| `list_scoped(skip, limit, extra_filters)` | List with auto tenant filter |
| `delete_scoped(id)` | Delete by PK, auto tenant filter |

### 8.2 Prohibited Unscoped Patterns
```python
# ❌ FORBIDDEN on tenant-scoped models
await self.get_all()     # No tenant_id filter
await self.delete(id=x)  # Can delete across tenants

# ✅ REQUIRED
await self.list_scoped()
await self.delete_scoped(id=x)
```

### 8.3 Platform-Admin Exceptions
Cross-tenant reads for platform-admin operations are allowed via the parent `BaseDAO` methods, but **MUST** be annotated:

```python
agents = await agent_dao.get_all()  # arch-guard: allow (platform_admin cross-tenant)
```

### 8.4 Background Worker / Daemon
Code not running in an HTTP request (Celery tasks, trigger daemons) MUST wrap DAO calls with `tenant_context()`:

```python
from app.dao.base import tenant_context

with tenant_context(tenant_id):
    agents = await agent_dao.list_scoped()
```

### 8.5 Models Without tenant_id (Transitional)
Models without a `tenant_id` column (`ChatMessage`, `Notification`, `AuditLog`, `Task`) use `BaseDAO` with mandatory scope parameters until migration adds the column. Their DAO methods MUST document the isolation mechanism used.

