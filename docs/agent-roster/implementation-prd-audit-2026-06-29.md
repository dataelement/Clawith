# Agent 通讯录实现与 PRD 对照

日期：2026-06-29

## 当前结论

本期实际交付已经从原 PRD 的“公司级组织花名册”收敛为“Agent 通讯录”：

- 已完成 roster-first 的可见性、查询、ID 化发送和 Agent 详情页通讯录入口。
- 旧 Relationships 数据仍保留为 legacy/OKR/gateway 兼容，不再作为新通讯录主语义。
- 公司级人和数字员工同框组织花名册、第三方组织架构映射、部门拖拽管理不属于本期已实现范围。

## 已实现

### 权限与可见性

功能描述：

用户创建或设置 Agent 时，可以选择 Company-wide、Only Me、Custom 三种访问方式。三者现在分别表达“全公司可用并进入 Plaza”“仅创建者私人使用”“全公司可用但只指定管理者”。这套权限同时决定人能否使用 Agent、Agent 之间能否互相发现和联系，以及 Agent 是否能出现在 Plaza。

实现说明：

- `company/custom/private` 已拆分为使用权和管理权语义。
- `company`：同租户平台用户可使用；非私人 Agent 之间可互通；Plaza 开启。
- `custom`：使用权按非私人处理，所有同租户用户和非私人 Agent 可使用；显式用户授权只表达管理权；Plaza 关闭。
- `private`：仅创建者可使用和管理；私人 Agent 只可看到同创建者的私人 Agent 和创建者本人。
- 关键实现：
  - `backend/app/core/permissions.py`
  - `backend/app/models/agent.py`

### Roster 查询与 API

功能描述：

Agent 可以实时查询自己当前能看到和能联系的同事清单。这个清单同时包含人类成员和数字员工，并返回稳定 ID、展示信息、联系方式和不可联系原因。前端通讯录和模型工具看到的是同一套结果，避免 UI 显示一个对象但 Agent 实际联系不到。

实现说明：

- `query_roster` 和 HTTP API 共用 `agent_roster` service。
- 已有只读 API：

```text
GET /api/agents/{agent_id}/roster
```

- API 支持：
  - `member_type=all|agent|human`
  - `query`
  - `include_uncontactable`
  - `limit/offset`
- 返回稳定发送 ID：
  - Agent：`target_agent_id`
  - Human：`target_member_id`
  - Platform user：`platform_user_id`
- 关键实现：
  - `backend/app/services/agent_roster.py`
  - `backend/app/api/roster.py`
  - `backend/app/services/agent_tools.py`

### ID 化发送

功能描述：

Agent 不能再靠名字猜测要联系谁。联系数字员工时必须使用通讯录返回的 `target_agent_id`，联系人类成员时必须先查 `query_roster`，再使用返回的 `target_member_id` 或 `platform_user_id` 发送。这样可以解决重名、旧记忆、旧 prompt 中名字不稳定的问题。

实现说明：

- A2A 主路径要求 `target_agent_id`。
- Human 主路径要求先 `query_roster(member_type="human")`，再使用：
  - `send_platform_message(target_member_id=...)`
  - `send_channel_message(target_member_id=...)`
- 旧 name 参数仍有兼容分支，但会把新调用引导回 query_roster。
- `send_channel_message` 不再默认暴露给没有渠道配置的 Agent。

### UI

功能描述：

Agent 详情页里原来的 Relationships 主入口已经产品化为 Directory / 通讯录。用户看到的是“这个 Agent 当前可以联系谁”，而不是旧的手动关系编辑器。权限设置页和创建页的文案也同步改成新语义，避免用户误以为 Custom 是限制使用人的白名单。

实现说明：

- Agent 详情页的旧 `relationships` route/tab key 保留用于路由兼容。
- 用户可见 tab 文案已经是 `Directory / 通讯录`。
- 页面渲染 `RosterDirectory`，数据来自 roster API，不再用旧 relationships API 冒充通讯录。
- 权限入口文案已改为：
  - Company-wide：全平台用户和 Agent 可使用，Plaza 开启。
  - Only Me：仅创建者可使用和管理，Plaza 关闭。
  - Custom：所有人可使用，只显式指定管理者，Plaza 关闭。
- 关键实现：
  - `frontend/src/pages/agent-detail/AgentDetailPage.tsx`
  - `frontend/src/i18n/en.json`
  - `frontend/src/i18n/zh.json`
  - `frontend/src/pages/AgentCreate.tsx`
  - `frontend/src/pages/OpenClawSettings.tsx`

## 本轮修复

- 修正 Access Permissions 文案，避免继续把 `custom` 解释为“指定可使用成员”。
- 修正 `Agent` 模型注释，明确 `custom` 是管理授权，不是 A2A relationship 配置。
- 修正 legacy relationship 兼容逻辑：`custom` 不再物化全公司用户到旧 relationship 表；只为 `private` 保留创建者兼容关系。
- 补充测试覆盖 custom 不应生成公司级 legacy relationships。

## 未实现

这些属于原 PRD 的公司级组织花名册能力，本期没有完成：

- 侧边栏顶级“组织花名册”入口。
- 公司花名册 tab：人和数字员工同框、按部门分组展示。
- 部门管理：新建、重命名、删除、拖拽成员移动部门。
- 人类成员卡片 UI：头像、职位、激活状态。
- Agent 卡片 UI：能力标签、创建者、可见性徽标。
- 第三方组织架构 tab。
- 第三方员工到平台账号的映射和解除映射。
- 花名册部门和第三方部门的并列展示。

## 仍保留的 legacy 范围

- `backend/app/api/relationships.py` 仍保留。
- `AgentRelationship` / `AgentAgentRelationship` 表仍保留。
- OKR 关系同步和部分 gateway/OpenClaw 旧能力仍可能读取 legacy relationships。
- 旧 relationships i18n 文案仍存在于 legacy 编辑器/兼容路径，不代表新通讯录主入口。

## 下一阶段建议

本轮已处理：

- 清理 gateway/OpenClaw onboarding 指令中的旧 relationships 主路径描述，改为 gateway directory payload，并补充中文/英文两版指令。
- 检查 OKR 依赖：当前 OKR 仍使用 legacy relationship rows 作为“OKR 跟踪名单”，不是通讯录来源；已在代码注释中标明，后续如迁移应作为独立 OKR 产品变更。
- 拆出 `RosterDirectory` 到独立组件文件。
- 给 legacy relationships API 标记 legacy，并更新 docstring / OpenAPI tag / 错误信息。
- 标注 `company_access_level` 为 legacy/default UI 字段，不参与 custom 使用权限制。

仍待具备运行环境后处理：

- 浏览器端端到端测试：需要可访问的前后端服务和登录态。本地当前没有 3010/5173 服务在监听，项目依赖中也没有 Playwright。

1. 修 bug：
   - 继续清理 gateway/OpenClaw onboarding 文案里的 “relationships” 主路径描述。
   - 检查 OKR 仍依赖旧 relationships 的范围，决定是否迁移到 roster。
   - 对浏览器端跑 Agent 详情页 Directory、创建/设置页权限文案、query_roster 到发送链路的端到端测试。
2. 整理代码：
   - 把 `RosterDirectory` 从过大的 `AgentDetailPage.tsx` 中拆到独立组件文件。
   - 给 legacy relationships API/docstring 标记 legacy，避免误用。
   - 观察 `company_access_level` 和 `AgentPermission(scope_type="company")` 是否还能进一步收口。
3. PRD 更新：
   - 保留原组织花名册 PRD 作为后续大阶段。
   - 为当前已交付版本补一份“Agent 通讯录 PRD”，避免继续用公司级花名册验收本期。
