# Agent 通讯录 Phase 1.3 - A2A 发送链路 ID 化

## 目标

Phase 1.3 在 Phase 1.2 的 `query_roster` 基础上，把 Agent-to-Agent 发送从“按名字猜目标”改成“按稳定 `target_agent_id` 精确发送”。

完成后，模型的标准流程应是：

1. 先调用 `query_roster` 查询可见数字员工。
2. 从结果中选择 `target_agent_id`。
3. 调用 `send_message_to_agent` 或 `send_file_to_agent`，直接传入 `target_agent_id`。

本阶段只处理数字员工之间的发送链路，不处理 human 发送工具 ID 化。

## 当前问题

现在 A2A 发送链路依赖 `agent_name`：

- system prompt 中拼接 Relationships 列表。
- 模型看到目标 Agent 名字。
- `send_message_to_agent(agent_name=...)` 内部按名字 exact match，再 fuzzy match。
- 命中后再判断 company auto-contact 或旧 `AgentAgentRelationship`。

这个方案的问题是：

- 重名 Agent 会误发或命中不稳定。
- fuzzy match 可能把消息发给不符合用户意图的 Agent。
- prompt 里预先塞完整同事列表，列表变大后影响上下文和缓存。
- 新 PRD 的 roster visibility 语义无法自然落到“按名字猜目标”的发送链路里。

Phase 1.3 要把“找谁”交给 `query_roster`，把“发送给谁”改成稳定 ID。

## 前置依赖

Phase 1.3 依赖：

- Phase 1.1 的权限拆分函数。
- Phase 1.2 的 `query_roster` 返回 `target_agent_id`。

发送工具不应该自己重新实现一套可见性规则。它应该复用 Phase 1.1 的 Agent roster visibility 判断，并在发送前做最终硬校验。

## 工具定义

### send_message_to_agent

```json
{
  "name": "send_message_to_agent",
  "description": "Send a message to a digital employee colleague. Use query_roster first to get target_agent_id.",
  "parameters": {
    "type": "object",
    "properties": {
      "target_agent_id": {
        "type": "string",
        "description": "Target digital employee ID returned by query_roster."
      },
      "message": {
        "type": "string",
        "description": "Message content to send."
      },
      "msg_type": {
        "type": "string",
        "enum": ["notify", "consult", "task_delegate"],
        "description": "notify is one-way FYI, consult is synchronous question, task_delegate is async delegated work with callback."
      }
    },
    "required": ["target_agent_id", "message", "msg_type"]
  }
}
```

### send_file_to_agent

```json
{
  "name": "send_file_to_agent",
  "description": "Send a workspace file to a digital employee colleague. Use query_roster first to get target_agent_id.",
  "parameters": {
    "type": "object",
    "properties": {
      "target_agent_id": {
        "type": "string",
        "description": "Target digital employee ID returned by query_roster."
      },
      "file_path": {
        "type": "string",
        "description": "Workspace-relative path of the source file, e.g. workspace/report.md."
      },
      "message": {
        "type": "string",
        "description": "Optional delivery note."
      }
    },
    "required": ["target_agent_id", "file_path"]
  }
}
```

## 参数规则

- 删除模型可见的 `agent_name` 参数。
- 不保留按名字发送的兼容路径。
- `target_agent_id` 必须是合法 UUID。
- `target_agent_id` 必须对应存在的 Agent。
- 目标不能是 source Agent 自己。
- source 和 target 必须在同一租户。
- source 必须对 target `visible=true`。
- target 必须 `can_contact=true`。
- `message` 不能为空。
- `send_file_to_agent.file_path` 必须通过现有 workspace 文件安全检查。

## 原 Prompt / 工具说明最小改动

Phase 1.3 需要同步修改模型可见的工具描述和 A2A 相关 prompt，让 Agent 能正确调用新链路。

重点不是只改工具参数，而是要让原来的 prompt 不再引导模型走“Relationships 名字列表 + `agent_name` 发送”的旧路径。

### send_message_to_agent 工具描述

工具描述里不要再引导模型参考 system prompt 的 Relationships 名字列表。应改成：

- 联系数字员工前，先调用 `query_roster`。
- 从 `query_roster.members[]` 里选择 `member_type="agent"` 的结果。
- 使用该结果里的 `target_agent_id` 调用 `send_message_to_agent`。
- 不要凭名字调用，不要自己猜 ID。

保留现有 `msg_type` 决策说明：

- 需要对方做事并返回结果：`task_delegate`
- 只是通知对方知道：`notify`
- 需要即时问答：`consult`
- 不确定时优先 `task_delegate`

### Agent 工具调用 prompt

除了单个工具 schema 的 `description`，Agent 的通用工具调用提示也要同步改。凡是现在提示模型“根据 Relationships 选择同事 / 直接调用 `send_message_to_agent(agent_name=...)`”的地方，都要改成 roster-first。

新的工具调用规则应表达为：

```text
When you need to contact a digital employee, do not call send_message_to_agent directly by name.
First call query_roster with member_type="agent".
If you know the target name, role, or capability, pass it as query.
Choose the intended agent from query_roster.members.
Then call send_message_to_agent or send_file_to_agent with target_agent_id.
Never invent target_agent_id. Never use display_name as the send target.
```

对应中文语义：

- 需要联系数字员工时，不要直接按名字发送。
- 先调用 `query_roster(member_type="agent")`。
- 如果知道名字、角色或能力，用 `query` 缩小范围。
- 从返回的 `members` 中选择目标。
- 用 `target_agent_id` 调用 `send_message_to_agent` 或 `send_file_to_agent`。
- 不能自己编 ID，不能把 `display_name` 当发送目标。

这个提示应放在模型能看到的工具使用规则里，优先级要高于旧的 Relationships 说明。Phase 1.3 后，旧的 A2A 名字调用规则应删除，不做并存。

### send_file_to_agent 工具描述

工具描述也要改成 ID 语义：

- 发送文件给数字员工前，先通过 `query_roster` 找到目标。
- 使用 `target_agent_id`，不要使用名字。
- `file_path` 仍然是 source Agent workspace 内的相对路径。

### consult 内部提示

当前 `consult` 会给目标 Agent 注入文件回传规则。这里的示例也必须从名字参数改成 ID 参数。

旧示例：

```python
send_file_to_agent(agent_name="请求方名字", file_path="<path>")
```

新示例：

```python
send_file_to_agent(target_agent_id="source_agent_id", file_path="<path>")
```

实际 prompt 中应填入 source Agent 的真实 ID，避免目标 Agent 再按名字查回请求方。

### 原 Relationships prompt

Phase 1.3 必须修改原来的 Relationships prompt 语义，不能再把它作为 A2A 发送入口。

原来的提示大意是：

```text
Refer to the Relationships section in your system prompt for available digital employees.
Use send_message_to_agent with agent_name.
```

Phase 1.3 后应改成：

```text
To contact a digital employee, first call query_roster.
Use query_roster(query="...", member_type="agent") when you know a name, role, or capability.
Then call send_message_to_agent or send_file_to_agent with the returned target_agent_id.
Do not call digital employees by name. Do not guess target_agent_id.
```

Phase 1.3 不再把 Relationships 内容注入为 A2A 可联系背景。public/company/custom 的可见数字员工本来就应该通过 `query_roster` 实时查询，private 也按 Phase 1.1 的私人规则查询；继续在 prompt 里塞 Relationships 只会让模型回到旧路径。

最小实现是：

- 删除或改写 `send_message_to_agent` 工具描述里的 `Refer to the 'Relationships' section...`。
- 从 A2A prompt 中移除原 Relationships 数字员工列表。
- 如果仍有 human 相关 Relationships 内容，也不能用于数字员工发送入口；数字员工发送前必须调用 `query_roster` 获取 `target_agent_id`。
- 不再在 prompt 中鼓励模型记住或复用同事名字作为发送参数。

这样可以避免新工具参数已 ID 化，但模型仍然按旧 prompt 走名字发送路径。

## 发送前校验

发送前必须做两层校验：

### 1. 可见性校验

复用 Phase 1.1 的 Agent roster visibility 判断：

- `company/custom` source 可以发送给同租户 `company/custom` target。
- `private` source 只能发送给同创建者的其它 `private` target。
- `private` 和 `company/custom` 之间不能互相发送。
- 跨租户不能发送。
- source 自己不能发送给自己。

这一步决定“理论上是否可以调用”。

### 2. 可联系性校验

可见之后，再检查当前运行状态：

- target stopped：拒绝。
- target error：拒绝。
- target expired：拒绝。
- target 不存在或已删除：拒绝。

这一步决定“当前是否能成功投递”。

`query_roster` 中的 `can_contact` 只是查询时状态，发送工具必须再次校验，不能信任旧查询结果。

## 返回结构

工具返回 JSON 字符串。

成功：

```json
{
  "ok": true,
  "target_agent_id": "agent_uuid",
  "target_display_name": "OKR 助手",
  "msg_type": "task_delegate",
  "delivery_status": "queued",
  "message": "Task delegated to OKR 助手."
}
```

失败：

```json
{
  "ok": false,
  "error": {
    "code": "target_agent_not_found",
    "message": "Target agent was not found."
  }
}
```

建议错误码：

| error.code | 含义 |
| --- | --- |
| `invalid_target_agent_id` | `target_agent_id` 不是合法 UUID。 |
| `target_agent_not_found` | 目标 Agent 不存在。 |
| `source_agent_not_found` | source Agent 不存在。 |
| `cannot_send_to_self` | 目标是 source 自己。 |
| `different_tenant` | source 和 target 不在同一租户。 |
| `target_not_visible` | source 对 target 不可见。 |
| `target_not_contactable` | target 可见但当前不可联系。 |
| `target_stopped` | target 当前 stopped。 |
| `target_error` | target 当前 error。 |
| `target_expired` | target 已过期。 |
| `empty_message` | 消息为空。 |
| `invalid_file_path` | 文件路径非法或不在 workspace 允许范围内。 |
| `file_not_found` | 文件不存在。 |
| `file_too_large` | 文件超过大小限制。 |
| `a2a_send_failed` | 未预期发送失败，日志中记录具体异常。 |

不可见对象只返回 `target_not_visible`，不要进一步暴露 private、跨权限细节。跨租户因为调用方已经提供了具体 ID，可以返回 `different_tenant`，但不能返回目标详细信息。

## send_message_to_agent 行为

保留现有三种模式：

### notify

- 保存 source message。
- 异步 wake target。
- 不等待 target 回复。
- 返回 `delivery_status="queued"` 或类似状态。

### consult

- 保存 source message。
- 同步调用 target LLM。
- 保存 target reply。
- 返回 target reply。

### task_delegate

- 保存 source message。
- 给 source Agent 写 focus item。
- 创建 on-message trigger，等待 target 完成后唤醒 source。
- 异步 wake target。
- 返回委托已创建。

Phase 1.3 不改变三种模式的业务语义，只改变目标定位和权限校验。

## send_file_to_agent 行为

`send_file_to_agent` 同样改成 `target_agent_id`：

- 不再按 `agent_name` 查找目标。
- 发送前复用同一套 A2A target 校验。
- 文件安全检查、大小限制、复制到目标 workspace/inbox 的现有逻辑保留。
- 文件投递 note 中可以继续展示 `target_display_name` 和 source Agent 名字，但不能依赖名字做定位。

## OpenClaw Gateway

Phase 1.3 优先处理模型工具侧的 A2A 发送。

OpenClaw gateway 当前仍是 `body.target` 名字路径。是否同步改成 ID 需要单独评估，因为这涉及外部 OpenClaw API contract。Phase 1.3 可以先不改 gateway 外部接口，但内部如果已经拿到 target ID，应复用同一套可见性/可联系性判断，避免出现工具侧和 gateway 侧权限不一致。

## 与其它阶段的边界

Phase 1.3 不做：

- 不实现 human 发送工具 ID 化。
- 不把 `send_message_to_agent` / `send_file_to_agent` 返回值强制 JSON 化，继续保持现有字符串返回风格。
- 不修改 `msg_type` 的现有代码兜底逻辑；schema 仍要求必填，代码里可以继续默认 `notify`。
- 不清理当前未使用的按名字解析 helper，例如 `_resolve_a2a_target()`，后续 cleanup 再处理。
- 不整体移除 system prompt 中可能仍服务 human 关系的 Relationships 内容；但必须移除或改写其中的数字员工 A2A 可联系列表和按名字发送指引。
- 不隐藏旧 Relationships tab / API。
- 不删除旧 `AgentAgentRelationship` 表。
- 不实现完整通讯录 UI。
- 不做部门、高频联系人、智能推荐。
- 不改 OpenClaw gateway 外部 API contract，除非后续单独确认。
- 不落地完整结构化错误码表；错误先保持当前字符串风格，后续统一工具返回格式时再 JSON 化。

Phase 1.3 完成后，模型可以通过 `query_roster` 查 Agent，再用 `target_agent_id` 精确发送。Phase 1.4 再考虑继续清理 prompt 里残留的 human Relationships 或其它历史关系内容。

## 最小测试矩阵

| 场景 | 预期 |
| --- | --- |
| 合法 `company` source 发送给同租户 `company` target | 成功 |
| 合法 `company` source 发送给同租户 `custom` target | 成功 |
| 合法 `custom` source 发送给同租户 `company/custom` target | 成功 |
| `private` source 发送给同创建者其它 `private` target | 成功 |
| `private` source 发送给 `company/custom` target | 失败，`target_not_visible` |
| `company/custom` source 发送给 `private` target | 失败，`target_not_visible` |
| source 发送给自己 | 失败，`cannot_send_to_self` |
| 跨租户 target | 失败，`different_tenant` |
| target stopped/error/expired | 失败，稳定错误码 |
| UUID 格式错误 | 失败，`invalid_target_agent_id` |
| target 不存在 | 失败，`target_agent_not_found` |
| `message` 为空 | 失败，`empty_message` |
| `notify` | 不等待回复，异步 wake target |
| `consult` | 同步返回 target reply |
| `task_delegate` | 创建等待 trigger，并异步 wake target |
| `send_file_to_agent` 合法 target + 合法文件 | 文件投递成功 |
| `send_file_to_agent` 非法路径 / 不存在 / 过大 | 返回结构化错误 |
