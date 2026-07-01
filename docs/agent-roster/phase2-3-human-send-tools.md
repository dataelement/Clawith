# Phase 2.3 - Human send tools ID 化

## 目标

把人类发送工具接到 Phase 2.2 的 resolver，让发送链路和 A2A 一样变成：

```text
query_roster -> stable ID -> send_platform_message / send_channel_message -> hard check
```

## 改动范围

- `send_platform_message`：平台内人类消息入口。
- `send_channel_message`：第三方渠道统一入口。
- `send_feishu_message`：保留为 legacy shortcut，不作为新主路径。

旧参数先保留为兜底，但兜底也必须走 resolver。

## 工具入口决策

对模型主推的入口只有三类：

- 数字员工：`send_message_to_agent(target_agent_id=...)`
- 平台内人类：`send_platform_message(target_member_id=... / platform_user_id=...)`
- 第三方渠道人类：`send_channel_message(target_member_id=..., channel=...)`

不再主推：

- `send_feishu_message`

原因：Feishu、DingTalk、WeCom、Slack、Teams、WeChat 都是第三方 IM channel。模型不应该为每个 provider 学一套发送工具，而应统一调用 `send_channel_message`，由后端按 provider 分发。

## send_platform_message

### 新参数

```json
{
  "target_member_id": "org_member_uuid",
  "platform_user_id": "user_uuid",
  "message": "..."
}
```

### 参数优先级

1. `target_member_id`
2. `platform_user_id`
3. 旧 `username`

### 发送前硬校验

- resolver 通过。
- member 有 `user_id`。
- `User` active 且同租户。
- 可创建或找到 primary platform session。

### 注意

当前 `send_platform_message` 会调用 `ensure_access_granted_platform_relationships()` 并要求 `AgentRelationship` 存在。Phase 2.3 主路径不应再依赖这个旧关系物化逻辑。

## send_channel_message

### 新参数

```json
{
  "target_member_id": "org_member_uuid",
  "channel": "feishu",
  "message": "..."
}
```

### 参数优先级

1. `target_member_id + channel`
2. `target_member_id`，由 provider 推导 channel
3. 旧 `member_name + channel`

### 发送前硬校验

- resolver 通过。
- 如果指定 `channel`，必须和 provider 匹配。
- 如果未指定 `channel`，根据 provider 推导。
- channel config 存在且已配置。
- provider-less 但有 `platform_user_id` 的成员，转给或提示使用 `send_platform_message`。

### 内部分发

`send_channel_message` 内部按 provider/channel 分发到具体 adapter：

```text
send_channel_message(...)
  -> resolve_roster_human_target(...)
  -> provider_type / channel
  -> dispatch:
      feishu
      dingtalk
      wecom
      slack
      teams
      wechat
```

统一入口不代表统一发送实现。每个 channel 仍保留自己的发送条件：

- Feishu：优先使用 `external_id` 作为飞书 `user_id`，必要时再考虑 `open_id`。
- DingTalk：可能需要 `agent_id`、`external_id`、`unionid/open_id`。
- WeCom：使用企业微信用户 ID。
- Slack：需要打开 DM channel。
- Teams：需要用户先和 bot 有过 conversation/service_url。
- WeChat：需要已有 context token。

### send_feishu_message legacy 处理

`send_feishu_message` 本期保留，避免旧触发器或旧业务逻辑立刻断掉。

处理原则：

- prompt 不再主推 `send_feishu_message`。
- 新主路径使用 `send_channel_message(target_member_id=..., channel="feishu")`。
- `send_feishu_message` 可以复用 Feishu adapter，作为兼容入口。
- 旧 `member_name` / `user_id` 参数如果继续保留，也必须逐步接入 resolver，不能绕过 roster visibility。

## 不做

- 不改 `send_channel_file`。
- 不删除旧参数。
- 不删除旧关系表。
- 不改 OKR 旧提示。

## 测试点

- `send_platform_message(target_member_id=...)` 成功定位。
- `send_platform_message(platform_user_id=...)` 成功定位。
- `send_channel_message(target_member_id=..., channel=...)` 成功定位 provider。
- `send_channel_message(target_member_id=..., channel="feishu")` 能分发到 Feishu adapter。
- 缺 platform user 失败。
- 缺 provider identity 失败。
- 缺 channel config 失败。
- 旧 `member_name` 重名时提示使用 `query_roster`。
