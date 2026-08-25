# 03 - Multi-Tenant Data Model & Isolation

> Status: Current implementation baseline.
> Scope: Tenant scoping, SQLModel data models, and cache key rules.

---

## 1. Multi-Tenant Principle

Clawith is a strictly multi-tenant enterprise system. No operation or query may access data outside the authorized `tenant_id` scope.

---

## 2. Enforcement Rules

1. **Database Queries**: Every SQLModel / SQLAlchemy query MUST explicitly include `.where(Model.tenant_id == tenant_id)` or use auto-injected ContextVar filters.
2. **Redis Cache Keys**: Cache keys must follow the format `tenant:{tenant_id}:{key_name}`.
3. **Background Worker Tasks**: Worker tasks must validate the tenant scope of the target `AgentRun` before executing commands.
