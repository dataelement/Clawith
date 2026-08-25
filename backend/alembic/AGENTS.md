# Alembic AGENTS.md — Clawith Database Migration Guidelines

> Auto-loads when editing anything under `backend/alembic/`.
> Read this **before** creating or editing a migration. Complements [`backend/AGENTS.md`](file:///Users/alex/Documents/Code/dataelem/Clawith/backend/AGENTS.md) and [`docs/constitution.md`](file:///Users/alex/Documents/Code/dataelem/Clawith/docs/constitution.md).

---

## 0. The Single Head Rule (最高拓扑不变量)

> **A new migration's `down_revision` MUST be the current single head — never an older revision, and never guessed from the filename.**

Mounting a `down_revision` on an already-applied revision forks the migration graph into **multiple heads**. Multiple heads cause application startup failure (`alembic upgrade head` aborts with "Multiple head revisions present").

The migration graph MUST always have **exactly one head**:

```bash
cd backend
uv run alembic heads      # MUST print exactly ONE revision
```

---

## 1. Creating Migrations Safely

### 1.1 Preferred Method (Auto-fill `down_revision`)
Let Alembic query the database and automatically determine the correct `down_revision`:

```bash
cd backend
uv run alembic revision --autogenerate -m "add_agent_credentials_table"
```

### 1.2 Verification Step
After creating or hand-editing a migration, verify head integrity:

```bash
cd backend
uv run alembic heads      # Check that exactly ONE line is output
```

### 1.3 Handling Multiple Heads (Branch Merge)
If parallel git feature branches legitimately produce two heads, resolve it with an explicit **merge revision**:

```bash
uv run alembic merge heads -m "merge_feature_branches"
```

> **CRITICAL**: Do NOT "fix" a fork by editing an already-released migration's `down_revision` — that rewrites history in production environments that have already applied it.

---

## 2. DDL-Only Rule (纯 DDL 变更规范)

**Migrations are DDL-only — no inline data migration or cleaning.**

- **Permitted**: Schema DDL (`create_table`, `add_column`, `drop_table`, `alter_column`, `create_index`, `create_foreign_key`).
- **Permitted Default Fill**: Declarative `server_default` on an added column.
- **FORBIDDEN (Data Ops)**:
  - Reading rows then writing based on them (`SELECT` → `UPDATE` / `INSERT`).
  - Data dedup / cleanup / backfill / purge loops.
  - Operations conditional on existing business data state.

> **Why**: Inline data operations are non-resumable and can stall or timeout during startup on production databases with large datasets. Data migrations must be placed in a separate one-off script under `scripts/` or `backend/scripts/` to be run out-of-band.

---

## 3. Idempotency & Safety Guards

- **Idempotence**: Guard new column/table additions against cases where the table already exists.
- **Rollback Symmetry**: Every `upgrade()` migration MUST have a corresponding, functional `downgrade()` implementation for rollback capability.
- **No Unindexed Large Table Locks**: Avoid adding unindexed foreign keys or columns blocking concurrent runtime queries on large product tables.

---

## 4. Pre-Merge Checklist

- [ ] `uv run alembic heads` prints **exactly one** revision.
- [ ] `down_revision` equals the head that existed *before* this change.
- [ ] `upgrade()` and `downgrade()` are DDL-only (no inline `SELECT`→`UPDATE`/`INSERT` data loops).
- [ ] Migration filename follows `v{Major}_{Minor}_{Patch}_f{Feature_Num}_{description}.py` convention (e.g., `v1_0_0_f060_tenant_id_backfill.py`).
- [ ] Revision ID follows `f{Feature_Num}_{description}` convention (e.g., `f060_tenant_id_backfill`, <=32 chars).
- [ ] Tested rollbacks locally: `uv run alembic downgrade -1` followed by `uv run alembic upgrade head`.

---

## 5. Migration & Revision Naming Standard (Bisheng Specification)

To ensure version traceability and strict alphabetical sorting, file names and revision IDs must follow the Bisheng convention:

### 5.1 File Naming Format
```text
v{Major}_{Minor}_{Patch}_f{Feature_Num}_{description}.py
```
- **Version Prefix (`v1_0_0`)**: Indicates the product release milestone. Keeps migrations sorted chronologically.
- **Feature Number (`f060`)**: Sequential feature/PR ID (3-digit minimum) preventing git branch merge collisions.
- **Brief Description**: Concise snake_case description of the change.

### 5.2 Revision ID Format
Use meaningful, feature-bound revision IDs instead of random hashes:
```python
revision: str = "f060_add_tenant_id_missing_tables"
down_revision: str | None = "allow_checkpoint_deliveries"
```

### 5.3 Structured Docstrings
Include `Background`, `Scope`, and `Idempotent` sections in every migration docstring to document technical intent and rollback safety.
