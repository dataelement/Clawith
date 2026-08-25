# Clawith 文档导航 (Documentation Navigation Hub)

> 找文档从这里开始。原则：**规范看根目录与 `constitution.md`、架构看 `architecture/`、需求交付看 `features/`、重构计划看 `technical-plans/`**。

---

## 1. 规范与流程 (开发前必读)

| 文档 | 内容 |
|---|---|
| [`constitution.md`](constitution.md) | 架构宪法铁律 C1–C4（运行时隔离 / 多租户 / 副作用幂等 / HTTP 客户端包装） |
| [`SDD-Guide.md`](SDD-Guide.md) | 开发流程与文档归档指南：流程分级 (Hotfix vs Full SDD)、★ 暂停点、已知坑记录机制 |
| [`../AGENTS.md`](../AGENTS.md) | 全局 AI Agent 约束与指令总入口 |

---

## 2. 系统架构 (子系统深度)

[`architecture/`](architecture/) — 核心架构基线（最新状态快照）：

- [`01-architecture-overview.md`](architecture/01-architecture-overview.md)：系统整体拓扑与四类事实隔离原则
- [`02-backend-runtime-boundary.md`](architecture/02-backend-runtime-boundary.md)：FastAPI、RuntimeCommandIntake 与 Command Worker 运行时隔离
- [`03-multi-tenant-data-model.md`](architecture/03-multi-tenant-data-model.md)：多租户数据隔离模型与 SQLModel 表结构

---

## 3. 功能交付归档 (SDD 产出)

`docs/features/` — 按 `v{X.Y.Z}/{NNN}-{name}/` 组织。每个需求包含 `spec.md` (需求与验收标准)、`design.md` (架构设计与已知坑)、`tasks.md` (任务日志)。

---

## 4. 重大技术方案与迁移计划

[`technical-plans/`](technical-plans/) — 重大技术重构与迁移方案归档：

- [`20260728-private-chat-finish-migration-plan.md`](technical-plans/20260728-private-chat-finish-migration-plan.md)：私有会话结束逻辑迁移方案
- [`20260728-dao-migration-plan.md`](technical-plans/20260728-dao-migration-plan.md)：DAO 重构与数据库迁移方案
