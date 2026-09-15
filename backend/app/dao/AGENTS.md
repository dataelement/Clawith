# Empty target DAO namespace

The clean-break target retains a zero-byte `app.dao` package initializer only as a static namespace boundary. It has no shared DAO implementation, generic `BaseDAO`, query bridge, implicit session ContextVar, process-global Tenant ContextVar, or package export. Do not add Python modules or exports under this directory.

Each target owner keeps its ORM models and repositories private under `app/modules/<owner>/`. Cross-owner consumers call typed public services. Cross-owner atomic work uses `app.infrastructure.database.TransactionContext`; it does not share private repositories or recreate a global DAO facade.

This file remains only as a path-specific guard for the empty namespace. Update `backend/AGENTS.md` and the owning architecture Note if an approved top-level boundary later replaces this rule.
