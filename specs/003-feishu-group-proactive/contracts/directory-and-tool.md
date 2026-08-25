# Contract: Directory and send_channel_message

## query_directory

新增过滤值：`member_type = group`。`all` 可包含群。

飞书群条目：

```json
{
  "member_type": "group",
  "target_recipient_id": "<stable-session-uuid>",
  "display_name": "项目交付群",
  "provider": { "provider_type": "feishu" },
  "can_contact": true,
  "contact_tools": ["send_channel_message"],
  "unavailable_reason": null
}
```

禁止返回 `chat_id`、`external_conv_id` 或凭证。

## send_channel_message

兼容参数：

```json
{
  "target_member_id": "<existing-human-member-id>",
  "message": "内容",
  "channel": "feishu"
}
```

新增飞书群参数：

```json
{
  "target_recipient_id": "<directory-group-id>",
  "message": "内容",
  "channel": "feishu"
}
```

规则：

- `message` 必填。
- 人类旧路径仍要求 `target_member_id`。
- 群路径要求 `target_recipient_id`，且只支持 `channel=feishu` 或由目标自动推导为飞书。
- 两种目标字段同时出现时拒绝，避免歧义。
- 群目标找不到、越权、失效或类型不符时，外部调用前返回 typed failure。
- Provider 明确成功/失败/未知使用现有 ToolExecutionOutcome 分类。
