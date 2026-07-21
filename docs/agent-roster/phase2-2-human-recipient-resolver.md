# Phase 2.2 - Human recipient resolver

## 目标

新增统一的人类收件人解析函数，作为人类发送工具的共同前置校验层。

Phase 2.2 只实现解析和校验，不改发送工具行为，不直接发送消息。

## 当前代码现状

当前三个人类发送工具各自查人：

- `send_platform_message`：`username/display_name -> User -> AgentRelationship -> OrgMember`
- `send_feishu_message`：`member_name/user_id -> AgentRelationship -> OrgMember`
- `send_channel_message`：`member_name -> AgentRelationship -> OrgMember -> IdentityProvider`

这些路径都有校验，但校验的是旧关系网络，不是 roster visibility。

Phase 2 决策：第三方渠道对模型只主推一个入口 `send_channel_message`，再由后端根据 provider 分发到 Feishu / DingTalk / WeCom / Slack / Teams / WeChat。`send_feishu_message` 保留为 legacy shortcut，但不作为新主路径。

## 建议函数

```python
@dataclass(frozen=True)
class RosterHumanTarget:
    source_agent: AgentModel
    member: OrgMember
    provider: IdentityProvider | None
    provider_type: str | None
    platform_user: UserModel | None


async def resolve_roster_human_target(
    db: AsyncSession,
    agent_id: uuid.UUID,
    *,
    target_member_id: str | None = None,
    platform_user_id: str | None = None,
    provider_user_id: str | None = None,
    member_name: str | None = None,
    provider_type: str | None = None,
) -> RosterHumanTarget:
    ...
```

## 输入优先级

1. `target_member_id`
2. `platform_user_id`
3. `provider_user_id`
4. `member_name`

`member_name` 只作为旧参数兜底。命中多个成员时必须返回歧义错误，不能静默选第一个。

## 校验职责

- source Agent 存在。
- 参数 UUID 合法。
- member 与 source Agent 同租户。
- 复用 `evaluate_roster_human_visibility(source_agent, member)`。
- `OrgMember.status == active`。
- provider 类型归一化，例如 `microsoft_teams -> teams`。
- 如果指定 `provider_type`，必须和成员 provider 匹配。
- 如果需要 platform 发送，成员必须有 `user_id`，并能查到 active `User`。
- 如果需要外部 channel 发送，成员必须有 provider 身份 ID。

resolver 不负责判断具体 channel config 是否存在，也不负责调用具体发送 API。它只返回“这个人是谁、当前 Agent 能不能联系、这个人有哪些平台/第三方身份”。

## 第三方渠道抽象

resolver 输出统一的 `provider_type`，供 `send_channel_message` 做内部分发。

V1 支持的归一化 provider：

- `feishu`
- `dingtalk`
- `wecom`
- `slack`
- `teams`
- `wechat`

其中 `microsoft_teams` 归一化为 `teams`。

provider identity 先沿用 `OrgMember` 的通用字段：

- `external_id`
- `open_id`
- `unionid`

resolver 不把这些字段解释成某个渠道的最终发送参数；具体选择由 `send_channel_message` 的 channel adapter 负责。

## 返回错误

建议用发送工具当前习惯的字符串错误即可，先不引入新的异常体系。

典型错误：

- `invalid_target_member_id`
- `source_agent_not_found`
- `human_recipient_not_found`
- `human_recipient_not_visible`
- `human_recipient_inactive`
- `human_recipient_ambiguous`
- `missing_platform_user`
- `missing_provider_identity`
- `provider_type_mismatch`

## 不做

- 不发送消息。
- 不查 channel config。
- 不做 provider dispatch。
- 不改 tool schema。
- 不改 prompt。
- 不删除 `evaluate_human_relationship_status()`。

## 测试点

- `target_member_id` 成功解析。
- `platform_user_id` 成功解析。
- provider identity 成功解析。
- member_name 重名返回歧义。
- 跨租户拒绝。
- private Agent 不能解析非创建者 human。
- inactive human 拒绝。
- provider_type 不匹配拒绝。
