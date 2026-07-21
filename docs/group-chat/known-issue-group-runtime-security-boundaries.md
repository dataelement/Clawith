# 已知问题：群 Runtime 的跨空间授权与低信任 Context 边界

## 状态

- 记录日期：2026-07-15
- 状态：本版已完成已确认的最小后端边界。问题一、问题二已由 2026-07-16 产品决策判定为非问题；问题三已对五个 builtin 跨空间 alias 落后端 fail-closed，完整人类 grant、MCP 等扩展动作归一和公开回复内容 provenance/DLP 待后续；问题四已完成信任通道拆分和回归
- 发现方式：PRD/技术方案与当前 Runtime 代码路径对照

## 2026-07-16 产品决策校正

出现审计推断与 Group PRD 产品语义冲突时，以 PRD 和后续明确产品决策为准。Group 内发起的 A2A 可以直接复用普通的全局 Agent-pair A2A Session、私下消息历史和 Session Context，不按来源 Group/Session/User/Run 隔离；这是允许的 Agent 间长期私下协作，不是跨群泄漏。因此本文“问题一”及修复要求 1 中的 source-scoped A2A 方案作废，不进入代码实施范围。

公开边界仍然有效：全局私下 A2A 内容不得自动写入当前 Group 的公开 `chat_session_id`、Group Session Context、Group Memory 或 Group Workspace；只有调用 Agent 主动整理并公开表达的部分进入群上下文。

## 问题一：A2A 上下文按 Agent pair 全局复用（已判定为产品允许行为）

`ensure_a2a_session()` 只按以下条件查找 Session：

- `tenant_id`
- `session_type = a2a`
- 排序后的两个 Agent ID

来源群 ID、群 Session ID、`origin_user_id` 和 source Run scope 都不参与隔离。群内 A2A 的请求正文又会写入这个 Session 的 `chat_messages`，后续 A2A Run 会通过统一 Context Builder 加载它的 recent messages 和 Session Context；terminal handler 还会继续更新该 Session Context。

这意味着同一对 Agent 在 G1/U1 中进行的未公开协作，可能进入它们在 G2/U2 中的后续私下 A2A 上下文。根据后续明确产品决策，这正是全局 Agent-pair A2A 连续性的预期行为；`technical-design.md` 4.3 的“不写入 `chat_messages`”应理解为“不写入当前 Group 的公开消息链”，不能据此禁止私下 A2A Session 自己的消息和 Context。

## 问题二：Group 应封闭私有 Agent 工具（已判定与 PRD 冲突）

旧审计建议把 Group Runtime 改成封闭 allowlist，并让文件系统只挂 Group Workspace；这与 Group PRD 及后续明确产品决策冲突，不进入本版实现。

普通 Group Agent 仍是同一数字员工，继续拥有自己的 Workspace、Memory、Skills 和普通工具。真正的边界是：这些内容不会因为进入 Group 就自动成为群共享内容。Agent 可以在本 Run 内使用自己的私有上下文完成工作，但把私有内容复制到 Group、把 Group 内容发送到私信/外部渠道，或把内容复制到另一个 Agent Workspace，都属于跨空间动作，必须单独授权。

## 问题三：外部发送可以绕过 autonomy 和用户确认

`_TOOL_AUTONOMY_MAP` 只映射 `send_feishu_message`，没有映射：

- `send_channel_message`
- `send_channel_file`
- `send_platform_message`

而 `send_channel_message` 可以在内部路由到 `_send_feishu_message()`。所以即使 Agent 对飞书发送设置为 L3 审批，也可能通过语义等价别名绕过。

Durable Runtime 的 tool ledger 也没有在首次 external-write 前要求确认：新 reservation 固定返回 `requires_confirmation=false`，tool step 随后直接调用通用 executor。确认机制只覆盖“结果 unknown 后是否重试”，不是“首次外部副作用是否被授权”。

这与 PRD 2.10.1 的统一规则冲突：跨空间发送必须由人显式触发、展示内容、必要时确认并审计。

2026-07-16 已落本版最小后端门禁：消息 alias 统一为 `external_message`，文件 alias 统一为 `external_file`；Group Run 在 Provider 调用前以 `group_cross_space_confirmation_required` 结算为确定性失败。该实现不会信任模型提供的 approval ID，也没有伪造审批流程。完整的人类 grant、预览、权限与审计流程仍待后续，因此当前行为是 fail-closed，不是“已经支持确认后发送”。私下 A2A `send_message_to_agent` 和非 Group 发送不受该门禁影响。

## 问题四：用户可控内容被嵌入 system message（已修复）

Group Context Builder 正确标注 announcement、memory、member message 是 user-provided data，但整个 `initial_input.group_context` 随后被 JSON 序列化进 system role 的 `dynamic_content`。

其中包括：

- trigger 原文
- 群公告正文
- Agent 群 memory
- 群描述和成员角色
- Workspace 路径索引
- Planning instruction/hint

trigger 同时还会作为普通 user message 进入上下文。旧实现因此既发生重复注入，也把不可信内容放到了 system 通道；仅用一句“data, not instructions”不能构成确定性 prompt-injection 防护。

2026-07-16 已按信任级别拆分：稳定平台 Prompt、Group Capability Policy、unknown-outcome 平台规则和可信 `runtime_instruction` 保留在 system role；Agent dynamic data 与 Runtime Context 进入独立 user-role reference-data message；触发正文只由唯一 current user message 提供。恶意 Group announcement、Memory、Workspace index 或成员资料不能再出现在 system content/dynamic content。该修复不替代 Tool policy，因此问题三的跨空间 Provider 前门禁仍独立生效。

## 影响

- 在没有真实人类授权时把内容发送到私信、外部渠道或其他 Agent Workspace。
- 通过语义等价 alias 绕过统一授权门禁。
- 恶意群公告、角色描述或 memory 对 system prompt 形成持久化注入。
- 数据泄露后缺少明确 source/consent 审计链。

## 修复要求

1. Group 来源 A2A 继续复用 pair-global A2A Session，不新增 source-scoped 隔离；仅保留 source Run trace/correlation 用于回传和审计。
2. 未公开 A2A 结果不能自动进入当前 Group 的公开 Session Context、Group Memory 或 Group Workspace；可以保留在 pair-global 私下 A2A 上下文。
3. 普通 Group Agent 保留自身 Workspace、Memory、Skills 和普通工具；这些资源不得被自动视为 Group 共享内容。
4. 本版把已确认的五个 builtin 跨空间 alias 归一到两个 canonical action，并在真实 grant 流程缺失时统一 fail-closed；后续 MCP 或新增 alias 也必须进入同一 policy，不能只依赖名字清单。
5. 后续 external-write 在首次执行前消费由真实人类产生、与来源 scope 和内容绑定的结构化 grant；unknown-outcome confirmation 只用于结果恢复，不能充当首次授权。
6. 用户可控正文不要拼入 system instruction；使用明确低信任消息/typed data channel，并对 tool arguments 做独立 policy enforcement。

## 验收建议

- 同一 Agent pair 在 G1 与 G2 连续 A2A，允许后一次私下 A2A 继续使用该 pair 的历史；但两个 Group 的公开消息、Session Context、Group Memory 和 Group Workspace 均不得被自动写入未公开 A2A 内容。
- Group Run 仍可正常使用 Agent 自身 Workspace/Memory/Skills；没有显式 grant 时，上述五个 builtin 消息/文件跨空间 alias 都在 Provider 前失败且零外部调用。
- 后续接入授权流后，L3 飞书发送通过所有 alias/MCP/渠道工具都必须进入同一个 canonical action 与审批流程。
- 恶意公告不能进入 system role；五个已知 external-action alias 由 policy 层在 Provider 前拒绝。`finish` 公开回复尚没有内容 provenance/DLP 门禁，因此本版不能声称所有私有内容外泄都能被确定性拦截。
- 事件和审计能关联触发人、源群、源消息、源 Run、目标渠道和实际发送内容摘要。
