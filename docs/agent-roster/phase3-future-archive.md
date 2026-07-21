# Agent 通讯录 Phase 3 - 旧路径下线

Phase 3 在 Phase 1/2 已经完成 roster-first 查询和 ID 化发送之后，负责把旧 Relationships / 名字发送 / legacy tool 逐步退出主链路。UI 产品化放到 Phase 4，不在 Phase 3 混做。

核心原则：

- 先让模型看不到旧入口，再清理内部兼容代码。
- 先隐藏或降级旧 UI/API，再考虑删表。
- 不把管理权 UI、通讯录 UI、组织架构 UI 混进同一个提交。
- 每一步必须有测试锁住“模型只能走 roster-first”。

## Phase 3.1 - 模型工具入口下线

详细文档：[Phase 3.1 - 模型工具入口下线](./phase3-1-model-tool-entry-cleanup.md)

这一阶段只改“对应工具”的模型可见入口和 schema，不做 prompt、Relationships、UI、权限或发送实现的大范围清理。

### 目标

让 LLM 主路径只看到 roster-first 的人类发送入口：

```text
query_roster(member_type="human", query="...")
-> target_member_id / platform_user_id
-> send_platform_message / send_channel_message
```

### 改动范围

- `backend/app/services/agent_tools.py`
  - 模型可见 `AGENT_TOOLS` 不再暴露 `send_feishu_message`。
  - `send_platform_message` 的模型 schema 不再暴露 `username`。
  - `send_channel_message` 的模型 schema 不再暴露 `member_name` / `provider_user_id`。
  - `get_agent_tools_for_llm()` 对数据库里的旧 `send_feishu_message` 做过滤，避免老 seed 或老 AgentTool 分配继续进入 LLM 工具列表。
- `backend/app/services/tool_seeder.py`
  - `send_platform_message` seed schema 不再暴露 `username`。
  - `send_channel_message` seed schema 不再暴露 `member_name` / `provider_user_id`。
  - 现有 `send_feishu_message` seed 保持非默认，只作为 legacy 兼容记录；不通过本阶段改动 `Tool.enabled` 语义。

### 保留兼容

本阶段不删除底层函数：

- `_send_feishu_message()`
- `_send_platform_message(... username=...)`
- `_send_channel_message(... member_name/provider_user_id=...)`

原因：旧触发器、旧任务、旧 DB 中已有 tool call 记录可能仍需要兼容执行。Phase 3.1 只保证新模型看不到旧入口。

### 测试点

- `AGENT_TOOLS` 中没有 `send_feishu_message`。
- `send_platform_message` schema 只暴露 `target_member_id` / `platform_user_id` / `message`。
- `send_channel_message` schema 只暴露 `target_member_id` / `channel` / `message`。
- seed schema 与 runtime schema 一致。
- `get_agent_tools_for_llm()` 即使 DB 中存在 `send_feishu_message`，最终返回给 LLM 的工具列表也不包含它。
- 旧内部函数 `_send_feishu_message()` 仍能委托到 `_send_channel_message()`，作为兼容保护。

### 不做

- 不删 `send_feishu_message` 函数。
- 不删旧参数解析分支。
- 不删历史 tool call 兼容。
- 不删 Relationships UI/API。
- 不改 `agent_context.py`、`task_executor.py`、OKR prompt 或其它非工具提示文本。
- 不改 roster resolver、发送函数实现或 DB 表结构。

## Phase 3.2 - Prompt 与人类 Relationships 背景下线

详细文档：[Phase 3.2 - Prompt 与人类 Relationships 背景下线](./phase3-2-prompt-human-relationships-cleanup.md)

### 目标

彻底移除 prompt 中“人类同事背景可以辅助发送”的残余语义。

### 改动范围

- `backend/app/services/agent_context.py`
  - 不再输出 `## 人类同事背景` 作为普通 Agent 上下文。
  - 保留简短 roster-first 规则。
  - 如仍需展示旧关系，只能改成“备注背景”，不能包含发送工具建议。
- 非普通 prompt：
  - `task_executor.py`
  - OKR / heartbeat / scheduler 等包含联系人指引的系统 prompt
  - 统一改成：先 `query_roster`，再 ID 化发送。

### 测试点

- prompt 测试确认不包含 `## 人类同事背景`。
- prompt 测试确认不包含 `send_feishu_message` 主路径。
- prompt 测试确认不包含 `send_platform_message(username=...)` 或 `send_channel_message(member_name=...)` 示例。
- prompt 测试确认联系人类前必须 `query_roster(member_type="human")`。

### 不做

- 不删除 `AgentRelationship` 表。
- 不删除 relationships API。
- 不改 OKR 数据模型，只改模型提示文本。

## Phase 3.3 - 旧 Relationships 产品入口降级

### 目标

旧 Relationships 不再作为“谁能联系谁”的产品入口。它要么隐藏，要么降级成备注/历史关系。

### 改动范围

- `backend/app/api/relationships.py`
  - A2A 相关接口从主产品入口隐藏或标记 legacy。
  - Human relationship 接口不再被新发送链路读取。
- 前端 Relationships tab
  - 隐藏，或改名为“备注关系 / 历史关系”。
  - 不再承诺配置后影响 A2A 或 human 发送权限。
- 文档
  - 更新产品说明：联系能力来自 roster visibility，不来自手动关系表。

### 测试点

- 新 A2A 发送仍只走 `target_agent_id` + roster visibility。
- human 发送仍只走 resolver + roster visibility。
- 旧 relationship API 的变更不会影响 OKR 等仍在读取旧表的业务，或已明确迁移。

### 不做

- 不物理删表。
- 不删除 OKR 仍依赖的关系同步逻辑。
- 不迁移历史关系数据。

## Phase 3.4 - 管理权语义收口准备

这一阶段独立于旧发送入口下线，不和 3.1/3.2/3.3 混做。

目标：

- `company/custom/private` 的“谁能使用”和“谁能管理”彻底分开。
- `custom` 的显式授权只表示管理权，不再影响使用权。
- 后端语义和旧字段依赖先观察清楚，前端设置页拆分放到 Phase 4。

待清理旧字段：

- `company_access_level`
- `AgentPermission(scope_type="company")`
- 其它只服务旧 custom/use 权限的逻辑

## Phase 4 - UI 产品化

Phase 4 再改 UI。它不阻塞 Phase 3.1/3.2 的旧工具和旧 prompt 下线，也不和 Phase 3 的 runtime 兼容清理混做。

详细文档：[Agent 通讯录 Phase 4 - 通讯录 UI 产品化](./phase4-contact-directory-ui.md)

### Phase 4.1 - Agent 详情页通讯录数据源

范围：

- 新增只读 roster HTTP API。
- 让前端能拿到和 `query_roster` 一致的实际可联系对象。
- 不再用旧 `relationships` API 冒充通讯录数据源。

### Phase 4.2 - 通讯录 UI / roster UI

范围：

- Agent 详情页 `关系 / Relationships` 改成 `通讯录 / Directory`。
- 数字员工通讯录和人类成员通讯录。
- 搜索、过滤、部门、状态。
- 展示可联系 / 不可联系原因。
- 重名时展示部门、职位、provider 身份。

### Phase 4.3 - 旧 Relationships 管理入口处理

范围：

- 隐藏旧 relationships 编辑入口，或迁移到高级设置 / legacy 区域。
- 旧关系表继续作为 OKR 或历史兼容数据存在，但不再作为通讯录主入口。

### Phase 4.4 - 组织架构 UI 增强

范围：

- 部门过滤。
- `department.path`。
- 多 provider 身份合并。
- `unionid` / external identity 去重。
- DingTalk / WeCom / Teams 等 provider 的发送配套。

## 观测和迁移

- 统计旧关系表是否还有读写。
- 统计工具调用失败原因。
- 记录 `query_roster -> send_*` 转化。
- 迁移历史自定义权限数据。
- 最后再决定删除或长期保留旧字段 / 旧表。

## 当前执行结论

Phase 3 已完成 **3.1 模型工具入口下线** 和 **3.2 Prompt 与人类 Relationships 背景下线**。

下一步进入 **Phase 4：通讯录 UI 产品化**。

原因：

- 模型工具 schema 已经看不到旧入口。
- prompt 已经不再引导模型使用旧发送语义。
- 产品侧还需要把 Agent 详情页里的 `Relationships` 改成真正的“通讯录”：它展示当前 Agent 实际可联系的人类成员和数字员工，而不是旧关系表的备注视图。
- 第一刀应先建立 roster HTTP 数据源，再替换前端页面语义。
