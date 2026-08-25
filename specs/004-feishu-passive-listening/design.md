# Design: 飞书群常驻 Agent V1

**Status**: Implemented
**Detailed plan**: [plan.md](./plan.md)
**Research**: [research.md](./research.md)
**Data contract**: [data-model.md](./data-model.md)
**Behavior contract**: [contracts/feishu-passive-listening.md](./contracts/feishu-passive-listening.md)

## Decision Summary

1. 飞书应用增加全量群用户消息权限，事件入口保持不变。
2. 入站消息以 Provider `message_id` 幂等，每条有效群消息进入现有 Durable Runtime。
3. 模型无需发言时输出 token-only `NO_REPLY`；只做精确、大小写不敏感匹配。
4. 不修改 Runtime 状态机。Run 和内部 Assistant ChatMessage 正常完成并可审计。
5. 在创建飞书群 `ChannelDelivery` 之前过滤精确静默结果，因此没有 outbox，也不会调用飞书发送接口。
6. 飞书外部群 Session 复用现有 Session Context summary/watermark/CAS；压缩模型来自 `session.agent_id`。
7. 精确静默 Assistant 消息不进入后续模型可见 Session Context，但底层记录不删除。
8. 不迁移外部渠道 ID，不统一其他渠道，不新增表、依赖或 checkpoint 状态。

## Critical Boundaries

- 飞书 `chat_id` 永远不是 Clawith 原生 `group_id`。
- `正文 + NO_REPLY` 必须发送，只有 token-only 才静默。
- failed/cancelled/waiting 不属于静默结果。
- 正常回答继续由现有 ChannelDelivery outbox 与 Provider receipt 保证。
- 压缩失败不得推进水位线或删除历史。

## Constitution Verdict

设计通过 [docs/constitution.md](../../docs/constitution.md) C1–C6 检查，无例外项。实现阶段必须先写回归测试，再修改代码。
