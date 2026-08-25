# Contract: Schedule and Trigger Feishu Group Delivery

## Schedule CRUD

请求/响应新增可选字段：

```json
{ "delivery_target_id": "<directory-group-id-or-null>" }
```

创建或更新非空目标时必须验证当前用户可管理 Agent，且目标属于该 Agent/租户的有效飞书群 Session。

## Trigger API and Tools

`set_trigger`、`update_trigger` 以及 Trigger 管理 API 增加相同的可选 `delivery_target_id`。旧 config 内容与旧 Trigger 行为保持不变。

## Runtime registration

- 空目标：保持当前行为。
- 有效目标：注册 Run 时设置 `delivery_status=pending` 并冻结飞书群 `delivery_target`。
- 无效目标：不注册会产生错误投递的 Run；记录明确 intake/执行失败原因。
- 相同 Schedule occurrence 或 TriggerExecution 重试：复用现有 source execution/idempotency identity，不产生第二条 terminal delivery。
