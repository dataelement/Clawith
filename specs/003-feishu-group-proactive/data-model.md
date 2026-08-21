# Data Model: 飞书群主动消息

## ChatSession（复用）

飞书群目标必须满足：

- `tenant_id` 等于当前 Agent 的 tenant
- `agent_id` 等于当前 Agent
- `session_type = group`
- `is_group = true`
- `source_channel = feishu`
- `external_conv_id` 符合已存在的飞书群会话格式
- `deleted_at IS NULL`

目录稳定标识：`target_recipient_id = ChatSession.id`。展示名称优先 `group_name`，再退到 `title`；永不向模型返回 Provider `chat_id`。

## AgentSchedule（扩展）

新增可空字段：

- `delivery_target_id: UUID | null`：指向当前 Agent 的有效飞书群 ChatSession UUID；不建立物理外键。

验证：创建、更新、手动触发和自动 tick 注册 Run 时均校验范围；空值保持原有 `delivery_status=not_required`。

## AgentTrigger（扩展）

新增可空字段：

- `delivery_target_id: UUID | null`：语义与 Schedule 相同；不建立物理外键。

验证：Tool/API 设置时校验，TriggerExecution 注册 Run 时再次校验。旧 Trigger 为 null，不改变原有 origin direct delivery逻辑；显式群目标优先于旧 origin direct 目标。

## AgentRun.delivery_target（复用）

冻结后的飞书群目标：

```json
{
  "kind": "session",
  "session_id": "<chat-session-uuid>",
  "channel_delivery": {
    "version": 1,
    "channel": "feishu",
    "target": {
      "receive_id": "<internal-provider-chat-id>",
      "receive_id_type": "chat_id"
    }
  }
}
```

该结构已由现有 external group terminal delivery 和 ChannelDelivery 使用；新功能只负责安全构造，不新增状态字段。

## State Rules

- Session 可用 → 可见、可选、可解析。
- Session 删除/错租户/错 Agent/非飞书/非群 → 不可见且解析失败。
- Run 注册成功后目标冻结；群改名不影响该 Run。
- Provider 成功/失败/未知沿用 ChannelDelivery 既有状态；未知不得自动重放。
