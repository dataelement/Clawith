# Research: 飞书群主动消息

## Decision 1: 复用 ChatSession 作为稳定飞书群目标

**Decision**: 调用飞书官方“获取用户或机器人所在的群列表”同步 Bot 当前所在群，并复用可信飞书入站进行增量更新；同步结果落为 Agent 范围内的 `ChatSession`。模型使用 Session UUID，Provider `chat_id` 保持内部。

**Rationale**: 官方接口能在群内无人先发消息时列出 Bot 已加入的群；Session 已有 tenant、agent、source channel、group 标志、展示名和唯一外部会话标识。同步到 Session 可避免新的重复群登记表和生命周期。

**Alternatives considered**:
- 新建 `feishu_group_targets` 表：重复保存 Session 已拥有的身份与状态，增加同步漂移。
- 允许模型直接传 `chat_id`：无法证明来源与 Agent/租户授权，且泄漏 Provider 寻址细节。
- 搜索 Bot 未加入的公开群：扩大权限和误发范围，本期不允许。

## Decision 2: 增强现有 send_channel_message

**Decision**: 增加 `target_recipient_id`，本期只解析飞书群 Session；保留 `target_member_id` 供现有各渠道人类收件人使用。

**Rationale**: 用户明确不要独立新 Tool；统一 Tool 让浏览器、Trigger 和 Schedule 共享同一授权、typed outcome 与审计路径，同时避免破坏旧模型调用。

**Alternatives considered**:
- `send_feishu_group_message`：Tool 数量和 Provider 特例持续增长。
- 把群塞入 `target_member_id`：语义错误，容易把群 Session UUID 误解析为 OrgMember UUID。
- 立即迁移所有人类调用到新字段：本次范围过宽，会影响其他 Provider。

## Decision 3: 自动化只保存目标引用，Runtime 注册时冻结 route

**Decision**: Schedule 与 Trigger 保存可选 `delivery_target_id`；Run 注册时共享 resolver 校验 Session 当前仍有效并生成现有 `delivery_target`/`channel_delivery` route。

**Rationale**: 配置保存用户意图，Run 保存该次执行冻结事实；授权撤销后新 Run 不应发送，已登记 Run 的幂等行为仍由 Runtime 管理。

**Alternatives considered**:
- 只把群名写进 instruction：模型会猜目标，无法保证幂等或授权。
- 完成后再查询目标：目标可能漂移，重试时不能证明冻结一致性。
- 自动化直接调用飞书 API：绕过 Runtime、ChannelDelivery 和 exactly-once 语义。

## Decision 4: 浏览器请求使用 Tool side effect，自动化使用 terminal delivery

**Decision**: Direct Chat 中明确“发到群”由模型调用 `send_channel_message`；绑定群的 Schedule/Trigger 则由 Run 的 terminal delivery 自动投递最终结果，避免依赖模型记得调用 Tool。

**Rationale**: 浏览器请求的具体内容和时点由用户指令决定，Tool 是合适的显式副作用；自动化绑定表达固定交付目的地，应由 Runtime 保证投递而不是 Prompt 约定。

**Alternatives considered**:
- 所有路径都要求模型调用 Tool：自动化可能漏发或重复调用。
- 所有路径都自动 terminal delivery：普通 Direct Chat 无法在一个 Run 中决定发送多个或中间结果。

## Decision 5: 文件仍按文本链接交付

**Decision**: 本期 `send_channel_message` 只发文本；浏览器要求发送文件时复用已有可访问链接生成能力，再把链接作为消息发送。

**Rationale**: 用户要求的是群主动消息，附件上传是独立 Provider 合同；不应把本地路径误当外部可访问资源。

**Alternatives considered**:
- 同期扩展群附件：显著扩大 Provider、存储、权限和测试范围。
