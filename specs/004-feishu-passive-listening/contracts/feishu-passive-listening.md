# Contract: 飞书群常驻 Agent V1

## 1. Inbound Event Contract

接受条件：

- event type 为现有飞书消息接收事件；
- message chat type 为 group；
- Provider `message_id` 非空；
- sender 为用户而非当前机器人；
- 消息类型属于现有已支持集合。

幂等键：

```text
channel_message_id(agent_id, "feishu", provider_message_id)
```

同一键的重试必须收敛到同一 ChatMessage 和同一 Runtime source execution。

## 2. Silent Reply Contract

常量：

```text
NO_REPLY
```

规范化与判定：

```text
silent := content.strip().casefold() == "no_reply"
```

不得使用 contains、suffix 或正则尾部匹配代替精确判定。

## 3. Suppression Scope

只有以下条件全部成立才抑制：

```text
delivery kind        terminal
lifecycle status     completed
channel              feishu
receive_id_type      chat_id
content              exact silent token
```

结果：

```text
Internal ChatMessage     allowed
ChannelDelivery          absent
Feishu provider call     absent
Run terminal status      completed
```

## 4. Non-silent Examples

以下内容必须正常发送：

```text
我来处理
我来处理\nNO_REPLY
NO_REPLY：因为不相关
`NO_REPLY`
请输出 NO_REPLY
```

## 5. Session Context Contract

对飞书外部群 Session：

- 入站用户消息始终可进入 pending/recent/compactable 选择。
- 正常 Assistant 回复可进入选择。
- 精确静默 Assistant 消息不进入模型可见 pending/recent/compactable 内容。
- 原始数据库行不得删除。
- 压缩水位线必须对应实际纳入 compact request 的最后一条消息位置。

## 6. Prohibited Changes

- 不修改 Runtime `ModelIntent`、checkpoint lifecycle 或 finish schema。
- 不写入或伪造原生 `group_id`。
- 不将静默规则应用到飞书私聊或其他渠道。
- 不在 Provider sender 收到 pending outbox 后才静默。
