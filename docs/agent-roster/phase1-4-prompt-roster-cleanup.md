# Agent 通讯录 Phase 1.4 - Prompt 瘦身与 A2A Relationships 清理

## 目标

Phase 1.4 在 Phase 1.2 `query_roster` 和 Phase 1.3 A2A ID 化之后，清理 system prompt 中旧的数字员工 Relationships 依赖。

目标是让 Agent 查找和联系数字员工时，只走：

```text
query_roster -> target_agent_id -> send_message_to_agent / send_file_to_agent
```

不再依赖 prompt 里预先拼接的数字员工同事列表。

## 当前问题

当前 `build_agent_context()` 会通过 `_load_relationships_from_db()` 把关系网络拼进 system prompt，其中包括：

- 人类同事 Relationships
- 数字员工同事 Relationships
- company auto-contact 数字员工

数字员工部分会以类似下面的形式进入 prompt：

```md
## 🤖 数字员工同事

### OKR 助手 — 帮助团队收集、整理和追踪 OKR 进展
```

这在旧方案里是发送入口，但在新方案里会带来问题：

- prompt 中的列表可能过期。
- 列表变大后占用上下文。
- 模型会继续按名字调用旧发送路径。
- public/company/custom 的可见对象本来应该实时由 `query_roster` 查询。
- private 的可见对象也应该由 Phase 1.1 规则动态计算。

## 实现范围

Phase 1.4 只处理 prompt 和上下文拼接，不改权限规则，不改发送链路。

要做：

- 从 system prompt 中移除数字员工 Relationships 列表。
- 移除 company auto-contact 数字员工的 prompt 预拼接。
- 保留或过渡保留 human Relationships 内容。
- 在工具调用规则或 system prompt 中加入简短的 roster-first 原则。

不做：

- 不修改 `send_message_to_agent` / `send_file_to_agent` 参数。
- 不修改 human 发送工具。
- 不删除数据库里的 `AgentAgentRelationship` / `AgentRelationship` 表。
- 不隐藏 Relationships UI/API。
- 不做完整组织通讯录 UI。
- 不修改 OpenClaw gateway。

## Prompt 新规则

system prompt 中应加入一段短规则，明确数字员工发现方式：

```text
To find or contact digital employees, use query_roster.
Do not rely on preloaded colleague lists for digital employees.
If you know a target name, role, or capability, call query_roster with member_type="agent" and a query.
Then use the returned target_agent_id when calling send_message_to_agent or send_file_to_agent.
```

这段规则应该简短，不要把 `query_roster` 的完整 schema 复制进 prompt。具体参数说明由工具 schema 负责。

## Relationships prompt 调整

### 数字员工部分

删除 `_load_relationships_from_db()` 输出里的数字员工区块：

```md
## 🤖 数字员工同事
```

包括：

- 旧 `AgentAgentRelationship` 产生的数字员工条目。
- company auto-contact 产生的数字员工条目。

这些都改由 `query_roster(member_type="agent")` 实时返回。

### 人类部分

Phase 1.4 暂时保留 human Relationships，但标题固定改成：

```md
## 人类同事背景
```

原因是 human 发送工具还没有完成 ID 化，当前人类联系链路仍可能依赖 prompt 里的名字、职位、关系说明。

标题必须收窄，避免模型把它误读成统一通讯录入口或数字员工发送入口。

## 代码切口

最小代码切口应该集中在：

- `backend/app/services/agent_context.py`
  - `_load_relationships_from_db()`
  - `build_agent_context()`

建议做法：

1. `_load_relationships_from_db()` 不再查询或输出 `AgentAgentRelationship` 和 company auto-contact Agent。
2. 保留 human relationship 查询和格式化。
3. `build_agent_context()` 始终加入简短的数字员工 roster-first 规则，即使当前没有 human Relationships。
4. 如果没有 human Relationships，也不要因为数字员工为空而输出旧 Relationships 区块。

## 与 Phase 1.3 的关系

Phase 1.3 负责让发送工具只能通过 `target_agent_id` 精确发送。

Phase 1.4 负责让 prompt 不再给模型旧的数字员工名字列表。

两者配合后，模型不会再走：

```text
Relationships 名字列表 -> send_message_to_agent(agent_name=...)
```

而是走：

```text
query_roster -> target_agent_id -> send_message_to_agent(...)
```

## 最小测试矩阵

| 场景 | 预期 |
| --- | --- |
| 构建 company/custom Agent system prompt | 不包含 `## 🤖 数字员工同事` |
| 构建 private Agent system prompt | 不包含其它数字员工列表 |
| 有 human relationships | 仍包含 `## 人类同事背景` |
| 无 human relationships | 不输出旧 Relationships 空区块 |
| prompt 中数字员工规则 | 始终输出，并明确要求使用 `query_roster` |
| 工具调用路径 | 模型能看到 `query_roster` 和 ID 化后的 A2A 工具说明 |
