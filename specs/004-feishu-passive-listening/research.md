# Research: 飞书群常驻 Agent V1

## Decision 1: 使用飞书消息事件接收全部群用户消息

**Decision**: 继续订阅 `im.message.receive_v1`，新增敏感权限 `im:message.group_msg`。

**Rationale**: 飞书官方事件根据应用权限决定推送范围；现有 `im:message.group_at_msg:readonly` 只能收到群内 @ 机器人消息，`im:message.group_msg` 才覆盖机器人所在群的全部用户消息。

**Alternatives considered**:

- 定时调用历史消息 API：增加延迟、分页与重复读取复杂度，不适合实时常驻 Agent。
- 保持群 @ 权限：无法实现普通消息进入上下文。

**Primary source**: [飞书接收消息事件](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive?lang=zh-CN)

## Decision 2: 入站幂等使用 Provider message_id

**Decision**: 使用事件体中的飞书 `message.message_id` 生成本地稳定消息 ID；`event_id` 不作为消息幂等权威键。

**Rationale**: 飞书官方明确提示特殊情况下可能重复推送，应使用 `message_id` 去重而不是依赖 `event_id`。

**Alternatives considered**:

- 继续优先 `event_id`：同一消息若以不同事件投递会重复执行。
- 仅用内存集合：进程重启和多 worker 下不可靠。

## Decision 3: 采用 OpenClaw token-only 静默模式

**Decision**: 模型无需发言时输出精确 `NO_REPLY`；仅 token-only、大小写不敏感、允许首尾空白的结果静默。

**Rationale**: OpenClaw 当前将精确静默令牌从出站 payload 中过滤，并专门限制为 token-only，以避免吞掉正文末尾包含 `NO_REPLY` 的有效回答。

**Alternatives considered**:

- 新增 Runtime `no_reply` intent：超过用户要求，扩大 checkpoint/Verifier/终态契约。
- 空字符串：被现有 finish、node transition、Verifier 和 completed checkpoint 规则拒绝。
- `endswith("NO_REPLY")`：存在吞掉有效正文的已知风险。

**Primary sources**: [OpenClaw agent loop](https://github.com/openclaw/openclaw/blob/main/docs/concepts/agent-loop.md), [OpenClaw tokens.ts](https://github.com/openclaw/openclaw/blob/main/src/auto-reply/tokens.ts)

## Decision 4: 在外部 ChannelDelivery 建立前抑制

**Decision**: 保留正常 Runtime 完成和内部 Assistant ChatMessage，只跳过飞书群 `ChannelDelivery` 建立。

**Rationale**: 用户要求仅为“不发到飞书群”。该位置能确保 Provider worker 没有可发送 outbox，同时不修改 Runtime state machine，也不伪造发送失败。

**Alternatives considered**:

- 在模型解析层吞掉：会触发非空 finish/Verifier 修复。
- 在飞书 Sender 内丢弃：已经创建 pending outbox，容易产生状态与重试语义不一致。
- 删除内部 ChatMessage：削弱审计且扩大 delivery 事务差异。

## Decision 5: 飞书外部群复用现有 Session Context

**Decision**: 扩展现有 policy resolver、compactor model selection 和 scanner，使 `group_id IS NULL` 的飞书群 Session 使用 `session.agent_id` 对应模型预算。

**Rationale**: 当前外部飞书群已经是 `session_type=group`，但没有 Clawith 原生 `group_id`；现有 scanner inner join `groups`，因此不会压缩。复用现有水位线、advisory lock、CAS 和 summary schema 能避免第二套上下文状态。

**Alternatives considered**:

- 将飞书 chat_id 写入原生 group_id：类型和领域都错误。
- 为飞书新建上下文表：形成重复状态真相。
- 暂不压缩：全量群消息会造成无界 pending history。

## Decision 6: V1 不统一其他渠道

**Decision**: 不迁移外部 ID，不建立统一 Conversation Adapter，不修改企微/钉钉/Slack/Teams/Discord。

**Rationale**: 用户明确选择先验证飞书临时版本，等更多渠道具备相同行为后再根据真实差异统一。

**Alternatives considered**:

- 本次完成跨渠道统一：范围与迁移风险显著增加，且不是验证核心体验所必需。
