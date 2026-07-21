# Agent 通讯录 Phase 4 - 通讯录 UI 产品化

> 2026-06-29 状态更新：4.1/4.2 的核心实现已经落地，详情页 `relationships` route/tab key 仍用于路由兼容，但用户可见文案和页面内容已经是 `Directory / 通讯录`，并渲染 `RosterDirectory`。当前实现与原组织花名册 PRD 的对照见：[Agent 通讯录实现与 PRD 对照](./implementation-prd-audit-2026-06-29.md)。

## 背景

Phase 1/2 已经把 Agent-to-Agent 和人类发送链路改成 roster-first：

```text
query_roster(...)
-> stable ID
-> send_message_to_agent / send_platform_message / send_channel_message
```

Phase 3 已经把旧工具入口和旧 prompt 语义从模型主路径下线。Phase 4 开始处理用户界面，把旧的 `Relationships` 页面产品化为“通讯录”。

这里的“通讯录”不是备注页，也不是旧关系表的改名。

**通讯录 = 当前 Agent 实际可以联系的人类成员和数字员工列表。**

## 产品定义

通讯录展示的是“这个 Agent 现在能联系谁”，而不是“管理员手工配置过什么关系”。

每个通讯录条目至少要表达：

- 对象类型：人类成员 / 数字员工。
- 稳定 ID：人类 `target_member_id`，数字员工 `target_agent_id`。
- 展示信息：姓名、角色、部门、来源 provider。
- 可见状态：能不能出现在当前 Agent 的通讯录里。
- 可联系状态：现在能不能实际发送消息。
- 可用发送方式：
  - 数字员工：`send_message_to_agent`
  - 平台用户：`send_platform_message`
  - 第三方渠道用户：`send_channel_message`
- 不可联系原因：例如 `agent_stopped`、`agent_expired`、`member_inactive`、`missing_contact_target`。

## 关键原则

- 通讯录的数据源必须来自 roster visibility / contactability 规则，而不是旧 `AgentRelationship` 表。
- 旧 Relationships 可以作为兼容或历史数据存在，但不能决定“是否可以联系”。
- UI 文案不能再暗示“添加关系后才可以联系”。
- 第一阶段不做大组织架构产品，不做复杂权限编辑，不做旧表物理删除。
- 每一步保持小提交：先数据源，再页面替换，再增强筛选。

## 当前实现调研

### 前端

当前 Agent 详情页仍使用 relationship 语义：

- `frontend/src/pages/agent-detail/agentDetailTabs.ts`
  - tab key 仍是 `relationships`。
- `frontend/src/pages/agent-detail/AgentDetailPage.tsx`
  - 组件名为 `RelationshipEditor`。
  - 页面标题使用 `humanRelationships` / `agentRelationships`。
  - 按钮使用 `addRelationship`。
  - 数据请求仍走：
    - `/agents/{agent_id}/relationships/`
    - `/agents/{agent_id}/relationships/agents`
    - `/agents/{agent_id}/relationships/member-candidates`
    - `/agents/{agent_id}/relationships/agent-candidates`
- `frontend/src/i18n/zh.json` / `frontend/src/i18n/en.json`
  - tab 和页面文案仍是“关系 / Relationships”。

这些接口和文案表达的是“关系编辑器”，不是“实际可联系通讯录”。

### 后端

旧 relationships API 仍在：

- `backend/app/api/relationships.py`
  - `GET /agents/{agent_id}/relationships/`
  - `PUT /agents/{agent_id}/relationships/`
  - `GET /agents/{agent_id}/relationships/agents`
  - `PUT /agents/{agent_id}/relationships/agents`
  - candidate 接口带有部分可见性过滤，但目标仍是“创建关系”。

真正的 roster 规则目前主要在工具实现里：

- `backend/app/services/agent_tools.py`
  - `_query_roster()`
  - `_format_roster_agent()`
  - `_format_roster_human()`
- `backend/app/core/permissions.py`
  - `evaluate_roster_agent_visibility()`
  - `evaluate_roster_human_visibility()`

现状问题：

- 前端没有直接可用的 roster HTTP API。
- roster 查询逻辑在 tool service 内，UI 不能直接复用 HTTP endpoint。
- relationships candidate API 和 roster 查询规则相似，但语义不一致，且返回字段不等于 tool 的通讯录结果。

## 拆分文档

- [Phase 4.1 - Roster Service 与只读通讯录 API](./phase4-1-roster-service-api.md)
- [Phase 4.2 - Agent 详情页通讯录 UI](./phase4-2-agent-directory-ui.md)
- [Phase 4.3 - 旧 Relationships 入口处理](./phase4-3-legacy-relationships-entry.md)

## Phase 4.1 - Roster Service 与只读通讯录 API

目标：先给前端一个只读通讯录 API，让 UI 不再依赖旧 relationships 表表达“可联系对象”。

第一步建议新增：

```text
GET /api/agents/{agent_id}/roster
```

同时把 `_query_roster()` 内部的查询、格式化、排序逻辑抽到 service，让 LLM tool 和 HTTP API 共用同一套规则。

详细设计见：[Phase 4.1 - Roster Service 与只读通讯录 API](./phase4-1-roster-service-api.md)

## Phase 4.2 - Agent 详情页通讯录 UI

目标：Agent 详情页从“关系编辑器”变成“实际可联系通讯录”。

第一步建议保留旧 tab key `relationships` 作为路由兼容，但显示文案改为“通讯录 / Directory”，页面组件改成只读 `RosterDirectory`。

详细设计见：[Phase 4.2 - Agent 详情页通讯录 UI](./phase4-2-agent-directory-ui.md)

## Phase 4.3 - 旧 Relationships 入口处理

目标：如果仍需要维护旧关系数据，把它从主通讯录里移走。

旧 `AgentRelationship` / `AgentAgentRelationship` 表继续作为 OKR 或历史兼容数据存在，但不再作为 Agent 详情页主通讯录入口。

详细设计见：[Phase 4.3 - 旧 Relationships 入口处理](./phase4-3-legacy-relationships-entry.md)

## Phase 4.4 - 通讯录增强

在 4.1 / 4.2 稳定后再做：

- 部门过滤。
- provider 过滤。
- 可联系 / 不可联系过滤。
- 重名消歧：部门、职位、provider、平台用户身份。
- 多 provider 身份合并策略。
- DingTalk / WeCom / Teams 等渠道的状态展示。

## 非目标

- 不物理删除 `AgentRelationship` / `AgentAgentRelationship` 表。
- 不在第一步重做组织架构 UI。
- 不把管理权 UI、使用权限 UI 和通讯录 UI 混在一个提交里。
- 不让前端直接调用 LLM tool；前端应使用 HTTP API。
- 不用旧 relationships API 冒充 roster API。

## 总体验收标准

- Agent 详情页显示“通讯录”，不再显示“关系 / Relationships”作为主产品入口。
- 通讯录展示的成员来自 roster API，而不是旧 relationships 表。
- 可联系对象包含稳定发送 ID。
- 不可联系对象在 `include_uncontactable=true` 时能显示原因。
- 搜索结果和 `query_roster` 工具语义一致。
- 旧 human / A2A 发送链路测试继续通过。
- 前端构建通过。

## 建议实施顺序

1. 抽出 roster 查询 service，并让 `query_roster` 继续使用它。
2. 新增只读 roster HTTP API 和后端测试。
3. 前端新增 `RosterDirectory`，先挂到现有 `relationships` tab。
4. i18n 改“关系”为“通讯录”，清理误导文案。
5. 再决定旧 relationships 编辑入口是否隐藏或迁移到高级设置。
