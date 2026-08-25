# Data Model: 飞书群常驻 Agent V1

## 结论

本功能不新增表、不新增列、不新增迁移。所有事实继续由既有实体持有。

## Existing Entities

### ChatSession

飞书外部群形态保持：

```text
tenant_id          必填，租户范围
session_type       group
group_id           NULL；只为 Clawith 原生群保留
agent_id           飞书 Channel 所属 Agent
source_channel     feishu
external_conv_id   既有飞书外部会话映射，V1 不迁移
is_group           true
```

验证规则：不得把飞书 `chat_id` 写入 `group_id`；同一 Session 的 Agent 必须属于同一 tenant。

### ChatMessage

- 入站用户消息：Provider `message_id` 经确定性映射得到本地 UUID。
- 正常 Assistant 回复：保持现有落库行为。
- 静默 Assistant 回复：允许保存精确 `NO_REPLY` 作为内部审计记录，但后续飞书群 Session Context 不把它当作有意义历史。

### AgentRun

- 每条被接受飞书群消息对应一次现有 chat Run。
- 不新增 lifecycle 或 completion mode。
- 精确 `NO_REPLY` 仍是 completed Run。
- 无外部 outbox 时沿用现有 settled delivery 行为。

### ChannelDelivery

- 正常回答：创建一条 `pending` outbox，Provider sender 成功后转为 delivered。
- 精确静默：不创建记录。
- 失败/取消：不受 token 规则影响。

### SessionContextState

- 继续以 `tenant_id + session_id` 持有滚动摘要、版本与 `covered_through_message_id`。
- 飞书外部群的 `agent_id` 范围保持现有 group Session 规则；压缩模型归属通过 Session 的 `agent_id` 解析，不改变 state schema。
- 水位线只在成功 CAS 提交有效摘要后前进。

## State Transitions

### Normal reply

```text
Inbound ChatMessage
→ AgentRun completed(content=normal text)
→ Assistant ChatMessage
→ ChannelDelivery pending
→ Provider delivered/failed
```

### Silent reply

```text
Inbound ChatMessage
→ AgentRun completed(content=NO_REPLY)
→ Internal Assistant ChatMessage
→ no ChannelDelivery
→ no Provider call
```

### Session compaction

```text
Messages after watermark reach threshold
→ lock Session
→ build compact request excluding exact silent Assistant rows
→ model returns candidate
→ CAS SessionContextState
→ watermark advances
```

失败时保持旧 state 和全部原始 ChatMessage。
