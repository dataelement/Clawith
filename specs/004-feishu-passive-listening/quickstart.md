# Quickstart: 飞书群常驻 Agent V1 验证

## Preconditions

1. 使用测试租户和测试 Agent。
2. 飞书自建应用开启机器人能力并订阅消息接收事件。
3. 飞书应用取得并发布全量群用户消息权限。
4. 将机器人加入隔离测试群。

## Local Contract Checks

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_feishu_channel_runtime.py \
  tests/test_agent_runtime_channel_delivery.py \
  tests/test_agent_runtime_delivery.py \
  tests/test_agent_runtime_session_context_background.py \
  tests/test_agent_runtime_session_context_compactor.py \
  tests/test_session_context_service.py

.venv/bin/ruff check \
  app/api/feishu.py \
  app/services/agent_runtime/channel_delivery.py \
  app/services/agent_runtime/delivery.py \
  app/services/agent_runtime/model_step_service.py \
  app/services/agent_runtime/session_context_background.py \
  app/services/agent_runtime/session_context_compactor.py \
  app/services/agent_runtime/session_context_service.py
```

如前端权限模板发生变化：

```bash
cd frontend
npm test
npm run build
```

架构检查：

```bash
scripts/arch-guard.sh
```

## Manual Scenarios

1. 普通群消息、不 @Agent：确认产生一个入站 ChatMessage 和一个 Run。
2. 重放同一 Provider message_id 三次：确认仍只有一个消息和一个 Run。
3. 模型输出精确 `NO_REPLY`：确认内部 Run completed、无 ChannelDelivery、飞书群无消息。
4. 模型输出 `正文\nNO_REPLY`：确认正常发送完整正文。
5. 飞书私聊输出相同 token：确认本版未改变私聊路径。
6. 生成超过压缩阈值的群历史：确认 SessionContextState 水位线前进，原始 ChatMessage 不减少。

## Evidence Boundary

- 单元测试证明本地契约，不证明飞书权限已审批或线上事件实际到达。
- 真实飞书验证必须分别提供：事件到达、Run 完成、无 outbox/有 outbox、Provider 发送结果和群内可见性的证据。
