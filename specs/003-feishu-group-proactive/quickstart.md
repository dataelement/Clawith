# Quickstart: 飞书群主动消息验证

## Local contract verification

1. 创建两个租户、两个 Agent 及各自飞书群 Session。
2. 验证 `query_directory(member_type=group)` 只返回当前 Agent/租户群且不泄漏 `chat_id`。
3. 验证 Direct Chat Tool 使用目录返回 ID，最终 Provider envelope 为 `chat_id`；重复 Tool receipt 不重复写外部消息。
4. 验证旧 `target_member_id` 飞书私聊和其他 Provider 测试不变。
5. 给 Schedule/Trigger 绑定群目标，验证 Run 注册时冻结 `delivery_target`，重复 occurrence/execution 只生成一个投递事实。
6. 运行 scoped pytest、Ruff、前端测试/build、Alembic heads 和 Architecture Guard。

## 3010 deployment verification

1. 重新发现 3010 当前代码、容器、数据库与迁移状态；部署前备份变更文件/数据库。
2. 部署同一候选代码并执行 migration；记录源码 marker/hash、容器 image/status/restart count 和 Alembic version。
3. 使用已明确的测试 Agent 主动查询 Bot 所在群并同步可信群 Session，无需群成员先发消息。
4. 浏览器 Direct Chat 请求 Agent 向该群发送唯一 marker；核对 Run、Tool ledger、ChannelDelivery、Provider receipt 和群内实收。
5. 创建一次性测试自动化绑定同一群；核对无需群内新消息即可投递唯一 marker，重复调度不重复发送。
6. 删除测试自动化；保留群消息和数据库证据，分别报告本地、部署和真实 Provider 验证边界。
