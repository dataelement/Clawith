# Clawith Project/项目管理功能 — 方案设计 v1

> **Status**: Draft for review
> **Scope**: 在 clawith 现有 agent + chat 基础上，引入"项目"作为一等公民，解决"任务零散、跨 agent 协作没有抓手"的问题。

---

## 1. 背景与动机

### 1.1 现状问题

clawith 目前的工作单位是 **agent + chat session**：
- 每个 chat session 挂在单一 agent 下（`chat_sessions.agent_id`）。
- 一件复杂的事（比如"clawith 出海"）往往需要和多个 agent 分别开多个 chat，任务容易零散、难以追溯。
- 没有"共同目标"的概念，agent 之间的协作只能靠 A2A 白名单（`agent_agent_relationships`）硬连。
- 现有 `tasks` 表是 **per-agent todo**（供 supervision 面板用），没有跨 agent 的 deliverable 概念。

### 1.2 目标

引入 **Project** 作为工作容器，让用户能：

1. 围绕一个**具体目标**（如"clawith 出海 - material 制作"）组织工作。
2. 把**多个 agent** 拉进同一个 project，各自承担角色。
3. 把 project 拆成多个**可验收的 deliverable**（目标 / 截止时间 / 验收标准）。
4. 有**状态机**记录 project 的生命周期。
5. 有**扁平 tag + folder** 的轻量分组能力（例如 A 客户下的一组子项目可以共用一个 folder 或 tag）。
6. 让 agent 在为 project 工作时，**自动感知 project 的上下文**（brief、pending tasks、验收标准）。

### 1.3 非目标（Out of Scope）

| 不做 | 为什么 |
|---|---|
| Project 嵌套（parent/child project）| 用户选择扁平 + tag/folder，避免无限层级的复杂度 |
| 把现有 chat session 挂到 project 下 | MVP 阶段 chat 和 project **互斥**，远期 P4 再做 |
| 改造现有 `tasks` 表 | 保持 supervision 系统不动，新建 `project_tasks` 承载 deliverable 语义 |
| Project 级群聊 | P4，MVP 协作模式默认 `isolated`（各干各的） |
| 真正的 ACL / 分角色权限矩阵 | MVP 走 tenant 级共享 + creator/admin 可写，够用 |

---

## 2. 核心设计决策

| # | 决策 | 备选 | 选定理由 |
|---|---|---|---|
| D1 | **Project task 新建 `project_tasks` 表**，不复用 `tasks` | 扩展 `tasks` 加 `project_id` | `tasks` 是 agent_id NOT NULL 的 supervision todo；project_task 是多 agent 可分派的 deliverable，字段和 UI 完全不同 |
| D2 | **Task 的 assignee 用 `ARRAY(UUID)` 存**，不建 M2M | `project_task_assignees` M2M 表 | 查询场景就是"列出这个 task 的 assignees"，ARRAY 足够；如果未来要"按 assignee 跨 project 筛选"，再迁移 |
| D3 | **MVP chat 和 project 互斥**，通过"上下文注入"让 agent 感知 project | session 立刻挂 project_id | 降低 MVP 复杂度；P3 用 brief 注入拿到主要收益；P4 再真正把 session 挂进来 |
| D4 | **Agent 加入 project 不自动建 A2A 关系**，MVP 提供"A2A 矩阵面板"让用户一键授权 | 自动双向建立 | 用户明确要手动授权；矩阵 UI 可以让手动授权不痛 |
| D5 | **Focus trigger 复用 `agent_triggers`**，扩展 `focus_ref` 语义为 `project:{uuid}` / `project_task:{uuid}` | 为 project 新建 trigger 表 | 复用 Pulse Engine，成本最低；project 级 trigger UI 放 P4 |
| D6 | **Folder 就是 `projects.folder` 字符串字段**，不建 folder 表 | folders 表 + FK | 扁平、无层级，字符串足矣；前端做成可输入的下拉框（Notion 风格）|
| D7 | **Project 可见性：tenant 级共享**。创建者 + tenant admin 可写，其他同租户用户只读 | 个人私有 / 细粒度 ACL | 用户明确选 tenant 级；细粒度留给未来 |
| D8 | **Collab mode 做成字段，MVP 只实现 `isolated`**（枚举里预留 `group_chat` / `lead_helper`）| 只做 isolated | 字段先占位，避免未来 breaking change |

---

## 3. 数据模型

### 3.1 新增表

```python
# backend/app/models/project.py

class ProjectStatus(str, Enum):
    draft = "draft"          # 已创建，未启动
    active = "active"        # 进行中
    on_hold = "on_hold"      # 暂停
    completed = "completed"  # 已完成
    archived = "archived"    # 归档，只读

class ProjectCollabMode(str, Enum):
    isolated = "isolated"        # MVP 默认：各干各的
    group_chat = "group_chat"    # P4
    lead_helper = "lead_helper"  # P4

class Project(Base):
    __tablename__ = "projects"

    id                   : UUID        # PK
    tenant_id            : UUID        # FK tenants.id, NOT NULL, INDEX
    created_by           : UUID        # FK users.id, NOT NULL
    name                 : String(200) # NOT NULL
    description          : Text        # 简介 / 背景
    brief                : Text        # 会被注入到 agent 上下文（markdown）
    folder               : String(100) # NULL, INDEX — 扁平 folder 分组
    status               : Enum(ProjectStatus)        # DEFAULT 'draft'
    collab_mode          : Enum(ProjectCollabMode)    # DEFAULT 'isolated'
    target_completion_at : DateTime    # NULL — 整体预期完成时间（软，用于排序/提醒）
    started_at           : DateTime    # NULL
    completed_at         : DateTime    # NULL
    created_at           : DateTime
    updated_at           : DateTime

    __table_args__ = (UniqueConstraint("tenant_id", "name"),)


class ProjectAgent(Base):
    """M2M: project ↔ agent。一个 agent 可同时属于多个 project。"""
    __tablename__ = "project_agents"

    project_id : UUID      # FK projects.id ON DELETE CASCADE, PK
    agent_id   : UUID      # FK agents.id ON DELETE CASCADE, PK
    role       : String(20)  # 'lead' / 'member' / 'observer', DEFAULT 'member'
    added_by   : UUID      # FK users.id
    added_at   : DateTime


class ProjectTag(Base):
    __tablename__ = "project_tags"

    id         : UUID        # PK
    tenant_id  : UUID        # FK tenants.id, NOT NULL, INDEX
    name       : String(50)  # NOT NULL
    color      : String(20)  # NULL — tabler color name 或 hex

    __table_args__ = (UniqueConstraint("tenant_id", "name"),)


class ProjectTagLink(Base):
    __tablename__ = "project_tag_links"

    project_id : UUID  # FK projects.id ON DELETE CASCADE, PK
    tag_id     : UUID  # FK project_tags.id ON DELETE CASCADE, PK


class ProjectTask(Base):
    """
    Project 下的可分派 deliverable。承载"目标 / 截止时间 / 验收标准"。
    与现有 tasks 表（per-agent supervision todo）语义区分，独立表。
    """
    __tablename__ = "project_tasks"

    id                  : UUID        # PK
    project_id          : UUID        # FK projects.id ON DELETE CASCADE, NOT NULL, INDEX
    title               : String(200) # NOT NULL
    goal                : Text        # 目标
    acceptance_criteria : Text        # 验收标准（markdown）
    due_at              : DateTime    # NULL — 截止时间
    priority            : Enum('low','normal','high','urgent')  # DEFAULT 'normal'
    status              : Enum('todo','doing','review','done','cancelled')  # DEFAULT 'todo'
    assignee_agent_ids  : ARRAY(UUID) # 多 agent 分派（见 D2）
    created_by          : UUID        # FK users.id
    sort_order          : Int         # DEFAULT 0 — 手工排序
    created_at          : DateTime
    updated_at          : DateTime
    completed_at        : DateTime    # NULL


class ProjectActivity(Base):
    """活动流 / 审计日志，供 Overview 展示。P2 引入。"""
    __tablename__ = "project_activities"

    id          : UUID      # PK
    project_id  : UUID      # FK, INDEX
    actor_type  : String    # 'user' / 'agent' / 'system'
    actor_id    : UUID      # NULL — user 或 agent id
    event       : String(50)  # 'project.created' / 'task.completed' / 'agent.added' / ...
    payload     : JSONB     # 事件详情
    created_at  : DateTime
```

### 3.2 对既有表的改动

| 表 | 改动 | 阶段 | 备注 |
|---|---|---|---|
| `agent_triggers` | `focus_ref` 规范化：约定格式 `project:{uuid}` / `project_task:{uuid}` | P3 | 字段已存在，只是规范语义 |
| `chat_sessions` | 加 nullable `project_id` FK | P4 | 预留，MVP 不填不用 |
| `tasks`（supervision） | **不动** | — | 保持独立 |

### 3.3 状态机

```
draft ──start──▶ active ──pause──▶ on_hold
                   │                 │
                   │◀────resume──────┘
                   │
                   ├──complete──▶ completed ──archive──▶ archived
                   │
                   └──archive────────────────────────────▶ archived
```

- **complete 校验（软）**：所有 `project_tasks.status ∈ {done, cancelled}` 才允许 complete。否则返回 warning，允许传 `force=true` 绕过。
- **archived**：只读，list 默认过滤。
- **到期**：`target_completion_at` 过了自动写一条 ProjectActivity + 发站内 notification（P2）。
- **删除**：MVP 只允许删除 `archived` 状态的 project。

---

## 4. API 设计

所有路径前缀 `/api/projects`，风格参考现有 `backend/app/api/chat_sessions.py`。

### 4.1 Project CRUD

```
GET    /api/projects?status=&folder=&tag=&q=        # 列表 + 筛选
POST   /api/projects                                 # 创建
GET    /api/projects/{id}                            # 详情（聚合 agents/tasks/tags）
PATCH  /api/projects/{id}                            # 更新
DELETE /api/projects/{id}                            # 仅 archived 可删
POST   /api/projects/{id}/transition                 # { action, force? }
                                                     # action: start|pause|resume|complete|archive
```

### 4.2 Agents

```
GET    /api/projects/{id}/agents
POST   /api/projects/{id}/agents                     # { agent_id, role }
PATCH  /api/projects/{id}/agents/{agent_id}          # 改 role
DELETE /api/projects/{id}/agents/{agent_id}
GET    /api/projects/{id}/a2a-matrix                 # 项目内 agent 两两授权状态
POST   /api/projects/{id}/a2a-grant                  # { source_agent_id, target_agent_id }
                                                     # 底层写 agent_agent_relationships
```

### 4.3 Tasks

```
GET    /api/projects/{id}/tasks?status=
POST   /api/projects/{id}/tasks
PATCH  /api/projects/{id}/tasks/{task_id}
DELETE /api/projects/{id}/tasks/{task_id}
POST   /api/projects/{id}/tasks/reorder              # { ordered_ids: [...] }
```

### 4.4 Tags

```
GET    /api/project-tags                             # tenant 级
POST   /api/project-tags
DELETE /api/project-tags/{id}
POST   /api/projects/{id}/tags                       # { tag_ids: [...] } 关联/解除
```

Folder 不单独开接口，就是 `projects.folder` 字段；前端从 list 响应里聚合出 distinct folder。

### 4.5 Context 注入（P3）

```
GET /api/projects/{id}/brief-prompt
→ 返回可直接拼进 system prompt 的字符串，包含：
  - name / description / brief
  - status / target_completion_at
  - pending tasks 列表（title + goal + due + acceptance 摘要）
```

WebSocket 层在 `call_llm` 前根据前端传来的 `active_project_id` 调用并 prepend 到 system message。

---

## 5. 前端结构

### 5.1 Sidebar 改动

在 `frontend/src/pages/Layout.tsx` 的主 sidebar 增加 Projects 入口：

```
┌─────────────────┐
│ [Logo / Toggle] │
├─────────────────┤
│  Plaza          │
│  Dashboard      │
│  Projects  ← 新 │
├─────────────────┤
│  Agent 1 📌     │
│  Agent 2        │
│  ...            │
└─────────────────┘
```

复用现有 `.sidebar-item` 样式。

### 5.2 路由

```
/projects           → ProjectsList.tsx
/projects/new       → ProjectCreate.tsx（或用 list 里的 modal）
/projects/:id       → ProjectDetail.tsx
```

### 5.3 ProjectsList 页面

- **顶部**：Search + 状态 filter（All / Active / On Hold / Completed / Archived）+ Tag filter
- **左侧栏**：Folder 列表（从 `projects.folder distinct` 得到，点击过滤）
- **主视图**：卡片网格（可切列表）
  - 卡片内容：name、status badge、folder、tags、agent avatar 堆叠、task 进度环、due date
- **右上**：`+ New Project`

### 5.4 ProjectDetail 页面

Tab 结构参考现有 `AgentDetail.tsx`：

| Tab | 内容 | MVP |
|---|---|---|
| **Overview** | name / description / brief (markdown) / status badge / target_completion_at / folder / tags / task 进度环 / 最近 activity（P2） | ✅ |
| **Tasks** | 可拖拽排序 task 列表 + inline 展开 goal/acceptance/assignees + status 分组 | P2 |
| **Agents** | 参与的 agents + role + `+ Add Agent` picker + **A2A 矩阵面板**（见 D4） | ✅ |
| **Settings** | 基本字段 / folder / tags / collab_mode / 状态机操作 / delete | ✅ |
| **Chat** | 聚合 project 下所有 session | P4 |
| **Triggers** | 项目级触发器 | P4 |

### 5.5 Agent 反向入口

在 `AgentDetail.tsx` 的 TABS 里新增 **Projects** tab：列出该 agent 参与的所有 project，点击跳转。

---

## 6. 协作与上下文共享模型

用户给的优先级：**A > D > B > C**。

| P | 能力 | 实现方式 | 阶段 |
|---|---|---|---|
| **P0 (A)** | Project brief 注入 agent | `GET /api/projects/{id}/brief-prompt` + WebSocket 层按 `active_project_id` prepend system message | P3 |
| **P1 (D)** | 结构化状态共享 | brief-prompt 自动带 "Pending tasks (N)" + 新表 `project_decisions` 记录决策 | P3 |
| **P2 (B)** | 共享 workspace | 新增 `project_workspaces/{project_id}/` 目录 + 扩展 `autonomy_policy`（新增 `read_project_workspace` / `write_project_workspace`） | P4 |
| **P3 (C)** | 对话历史共享 | 启用 `chat_sessions.project_id` + 提供 `get_project_conversation_history` tool 给 agent 按需查询 | P4 |

### 6.1 Agent 怎么"进入 project 模式"（P3）

因为 MVP 不把 chat session 挂到 project，agent 感知 project 的机制是：

1. 用户在 project 详情页点"Work with @Agent X" → 跳到该 agent 的 chat，URL 带 `?active_project={id}`。
2. Chat 页面在 header 顶部显示横幅：`🎯 Working in: clawith 出海 material 制作 [× 清除]`。
3. 用户发消息时，前端通过 WebSocket 带上 `active_project_id`。
4. 后端在 `call_llm` 前调用 `brief-prompt` 并 prepend 到 system message。

横幅可以手动清除，清除后变回普通 chat。

### 6.2 Focus Trigger（P3）

- `agent_triggers.focus_ref` 支持格式：`project:{uuid}` / `project_task:{uuid}`。
- Pulse Engine 触发时，如果 focus_ref 指向 project/task，就把 brief + task 详情拼到系统 message 里。
- 使用场景："每周一 9 点让 @designer 看看 '出海 material' 里有没有新的 review 任务"。

---

## 7. 权限与可见性

| 操作 | 谁能做 |
|---|---|
| 看 project 列表 / 详情 | 同 tenant 所有用户 |
| 创建 project | 同 tenant 所有用户 |
| 编辑 project（含加/移 agent、改 task、推状态机） | `created_by` + tenant admin |
| 删除 project | `created_by` + tenant admin，且必须先 archive |
| A2A 授权 | 对 source 和 target agent 都有权限的用户 |

Agent 分派受现有 `agent_agent_relationships` 约束 — 加 agent 到 project **不会**自动建 A2A，需要在 A2A 矩阵面板里手动点"授权"。

---

## 8. 分阶段实施计划

### Phase 1 — MVP：组织层 + Agents + A2A 矩阵

**目标**：能创建 project、分组、塞 agent、跑状态机、手动授权 A2A。

**不涉及**：task、context 注入、chat 集成。

- [ ] Alembic migration: `projects` / `project_agents` / `project_tags` / `project_tag_links`
- [ ] Backend: Project / Agent / Tag CRUD + state transition + A2A grant API
- [ ] 多租户隔离 + 写权限校验
- [ ] Frontend: sidebar 新增 Projects 入口
- [ ] `ProjectsList.tsx`（卡片 + filter + folder 栏）
- [ ] `ProjectDetail.tsx`: Overview + Agents (含 A2A 矩阵) + Settings tabs
- [ ] `AgentDetail.tsx` 新增 Projects tab（反向入口）
- [ ] i18n 中英文

**验收标准**：
- 能创建"clawith 出海"，打 tag "海外"、folder "2026H1"
- 能加 2 个 agent 并手动授权它们互相通信
- 能跑完整个状态机 draft → active → completed → archived

---

### Phase 2 — Tasks & 验收标准

**目标**：project 里能建 deliverable，带 goal/截止时间/验收标准，能分派给多 agent，能追踪进度。

- [ ] Alembic migration: `project_tasks` + `project_activities`
- [ ] Backend: Task CRUD + reorder + activity 写入
- [ ] Frontend: Tasks tab（拖拽 + inline 展开 + markdown 渲染）
- [ ] Overview 加进度环 + 最近 task + 活动流
- [ ] `complete` transition 时校验 pending tasks
- [ ] `target_completion_at` 到期自动写 activity + 站内通知

**验收标准**：给 "clawith 出海" 加 5 个 task，设置 due + 验收标准，分派给不同 agent，跑完进度至 100% 后能 complete project。

---

### Phase 3 — Context 注入 + Focus Trigger

**目标**：agent 真正"感知"自己在为 project 工作。

- [ ] `GET /api/projects/{id}/brief-prompt`
- [ ] WebSocket 层改造：支持 `active_project_id`，prepend system message
- [ ] Agent chat header 加"🎯 Working in: X"横幅
- [ ] `focus_ref` 规范化 + Pulse Engine 支持 `project:` / `project_task:` 前缀
- [ ] `project_decisions` 表 + ProjectDetail 的 Decisions tab

**验收标准**：用户在 project 点 "Work with Designer Agent" → 跳到 chat → agent 第一条回复就能准确引用 project brief 和 pending tasks。

---

### Phase 4 — 深度集成（远期）

- [ ] 启用 `chat_sessions.project_id`，支持新建 session 时选 project，详情页 Chat tab 聚合展示
- [ ] `group_chat` / `lead_helper` 协作模式
- [ ] 共享 workspace（`project_workspaces/`） + autonomy_policy 扩展
- [ ] Project 级 Triggers tab（UI 直接管理 project-scoped trigger）
- [ ] Agent 可用的 `get_project_conversation_history` tool

---

## 9. 风险与注意事项

| 风险 | 缓解 |
|---|---|
| `project_tasks` 和现有 `tasks` 语义相近，用户可能混淆 | UI 上明确叫 "Deliverables" 而不是 "Tasks"；文档里说清楚 |
| A2A 手动授权 UX 繁琐 | MVP 提供矩阵面板 + 一键授权，让手动授权不痛 |
| Tenant 级共享下，多人同时编辑 project/task 会有冲突 | MVP 不做乐观锁，`updated_at` + 冲突时最后写赢；后续观察是否要加 |
| Context 注入如果 brief 很长会挤占 context window | 提供 brief 长度上限（e.g. 2000 token），超过截断 + warning |
| `ARRAY(UUID)` 对"跨 project 按 assignee 筛选"支持差 | 真要做时迁移到 M2M 表，migration 成本可控 |
| Phase 1 无 task 无 context 注入，用户可能觉得"project 就是个空壳" | 文档/ release note 明确说 MVP 定位是"组织层"，P2/P3 才完整 |

---

## 10. 待 Reviewer 评估的 Open Questions

请同事 review 时特别关注下面几点：

1. **D1（新建 `project_tasks` vs 扩展 `tasks`）** 这个判断是否合理？有没有我没考虑到的 supervision 场景会因此割裂？
2. **D3（MVP chat 和 project 互斥）** 是否接受？还是应该 Phase 1 就直接把 `chat_sessions.project_id` 加上（哪怕 UI 还不用）？
3. **D4（A2A 手动授权 + 矩阵面板）** UX 上大家能接受吗？还是应该提供一个"一键互相授权本 project 内所有 agent"的快捷按钮？
4. **D7（Tenant 级共享 + creator/admin 可写）** 够不够？会不会很快就要细粒度 ACL？
5. **Phase 拆分** 是否合理？是否应该把 P3（context 注入）提前到 P2 一起做，否则 MVP 完整度不足？
6. **命名**：UI 上用 `Projects / Deliverables` 还是 `项目 / 交付物`？中英文统一怎么定？
7. **Folder 是字符串字段还是独立表？** 当前选字符串（D6），如果 reviewer 觉得需要支持 folder 重命名、合并，就得改成独立表。

---

## Appendix A — 关键代码位置（实施时的锚点）

| 概念 | 文件 | 行 |
|---|---|---|
| ChatSession 模型 | `backend/app/models/chat_session.py` | 1–45 |
| Agent 模型 | `backend/app/models/agent.py` | 19–174 |
| AgentTrigger 模型（focus_ref） | `backend/app/models/trigger.py` | 1–46 |
| A2A 关系 | `backend/app/models/org.py` | 79–92 |
| Chat Session API（参考风格） | `backend/app/api/chat_sessions.py` | 1–200 |
| 主 Sidebar | `frontend/src/pages/Layout.tsx` | 234–656 |
| Agent 详情 tabs | `frontend/src/pages/AgentDetail.tsx` | 23 (TABS 数组) |
| Alembic 迁移目录 | `backend/alembic/versions/` | — |
