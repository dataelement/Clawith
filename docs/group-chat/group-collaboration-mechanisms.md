# Group 自组织协作机制（已确认基线与待讨论项）

> 本文只列机制，不写 PRD，不描述页面方案。
>
> `docs/group-chat/prd.md` 是 Group 产品规则来源。本文中已经逐项确认的 Planning v2、自组织公开交接等内容，是对旧 PRD 对应段落的后续明确修订；除此之外，如果审计推断、技术方案或实现建议与 PRD 冲突，以 PRD 为准，不把技术担忧自行升级成产品限制。
>
> 本文记录当前讨论形成的目标机制，不代表现有代码已经全部实现。已确定项与待讨论项分开维护。共享 Runtime、Prompt、Tool、Verifier 与 RunView 语义以 `docs/single-agent-runtime/runtime-architecture-decisions-2026-07-15.md` 为准；本文只补充 Group 的继承边界和产品机制。

## 0. 与单 Agent Runtime ADR 的继承边界

### 0.1 直接继承的共享机制

Group Planning Run、普通 Group Agent Run、公开 `@` child Run 和 Group 场景中的私下 A2A，都使用同一套已确认 Runtime 基线：

1. LangGraph checkpoint 是执行进度真值，`AgentRunCommand` 是 start/resume/cancel 控制命令真值；产品逻辑不得依赖 `projected_*` 或第二套执行状态。
2. 不保留 `RuntimeProjector`，不增加 projection table、execution job table、Group workflow 状态表或平行的 Runtime。
3. 一次 Command invocation 从其精确 checkpoint 继续执行，直到稳定 waiting 或 terminal；Graph 稳定后再由独立、幂等的产品同步处理公开消息、Planning 调度、A2A 回传和 lane 释放。产品同步失败不得重新执行 Graph 或 Tool。
4. `RunStateReader` 必须按目标 Run、Command 和 `applied_checkpoint_id` 精确读取状态；Group 页面和后台任务都只能消费 typed `RunView`，不能把 Thread 最新 checkpoint 或产品侧镜像当成目标 Run 状态。
5. cancel 采用 interrupt-and-preserve，只取消目标 Run 并保留最后稳定 checkpoint。默认不级联取消同一 `root_run_id` 下的 Planning、公开 child 或 A2A child；Workflow 级级联取消如有产品需求，必须另行定义。
6. 普通 Group Agent Run 共享 Run 级模型决策轮次 Step budget。`agent_id = null` 的 Planning Run 不进入 Agent loop，继续使用独立、有界的 planning attempt / repair 次数；不能把 Agent `max_tool_rounds` 当成 Planner 尝试次数。当前 Planning 上限为初次调用加最多两次 repair，即总计最多三次模型调用。canonical builtin Tool definitions、有效 Tool 集解析、既有 `ToolExecutionOutcome`、Tool Ledger、私有 Result Store、确定性 Verifier、Base Prompt V1 和动态上下文装配规则由 Group Agent Run 与其他 Agent 入口共享。
7. 每个 Group Agent Run 的确定性 Verifier 只判断当前 Run 是否满足 `finish` 协议、是否存在未结算 Tool，以及当前 Run 的可信 artifact/evidence 是否可读；它不判断整个群协作是否在业务上完成，也不调用另一个 LLM 做语义裁判。
8. Group 专属产品规则只能通过可信 Group context、条件化 Tool Schema/Capability Policy 和产品同步实现，不得在 GroupContext、Base Prompt、Tool description 与 Planner prompt 中重复注入互相竞争的指令。

### 0.2 明确不从 Direct Chat 继承的机制

Direct Chat 的一个窗口只服务一个 Agent，因此可以让一个 `ChatSession` 直接对应一个持续的 LangGraph Thread。Group 不满足这个前提：同一 Group Session 内会出现 Planning Run、多个 Agent、并行入口、公开 child Run 和私下 A2A。把整个 Group Session 映射成一个 LangGraph Thread 会混合不同 Agent 的消息、状态、工具权限和执行顺序。

Group 固定采用以下映射：

```text
Group ChatSession
  = 产品层公开群对话与共享 Session Context
  != 一个共享 LangGraph Thread

Planning Run
  = 一个独立 LangGraph Thread

每个 Group Agent Run / 公开 @ child Run
  = 一个独立 LangGraph Thread

同一 Run 的 wait -> resume
  = 在该 Run 自己的 Thread 上继续
```

因此：

1. Direct Chat 的 `thread_id = ChatSession.id` 只适用于 Direct Chat；Group Run 当前继续使用独立 Run thread identity，不得为了统一映射而改成 `Group ChatSession.id`。
2. Direct Chat 的 Thread FIFO 不能决定 Group 的 lane scope。Group 继续复用同一套 durable lane 基础设施，但 `group_mention:{tenant_id}:{agent_id}` 的范围、优先级和 Workflow 穿插仍是 Group 产品决策。
3. Direct Chat 的单套 Thread Running Summary 不替代 Group Session Context compact。Group 的共享公开上下文跨越多个独立 Agent Thread，因此继续保留一套 Group Session 级 compact；每个 Group Run 自己的模型上下文仍遵守共享的 token 预算、Tool Exchange 规范化和动态装配规则。
4. Direct Web Chat 的 `waiting_user` 回复恢复协议不能直接套到 Group。Group 没有可靠的群内回复关联之前，不允许让普通 Group Agent Run 进入无法恢复且长期占 lane 的 `waiting_user`；需要用户补充时，V1 应在公开终态中提出明确问题，后续由人类新的结构化 `@` 创建新的顶层 Run。私下 A2A 的 `waiting_agent` 仍恢复原 Run。
5. Direct Session 的 singular `activeRun`、单 waiting correlation 和“当前 Thread 最新 Run”接口不适用于 Group。一个 Group Session 可以同时有多个 Agent lane 和多个 active/waiting Run；Group 查询必须按明确 `run_id` 读取 `RunView`，群级列表如需聚合应返回集合，不能任选一个 active Run 代表整个群。

### 0.3 本文仍然负责的 Group 专属机制

以下内容不由单 Agent ADR 替 Group 做产品决定，继续以本文为准：

- 人类单 `@`、人类多 `@` 与 Planning 的入口规则；
- `advisory / enforced`、`plan_prompt` 和公开轮转；
- 结构化公开 mentions、公开消息与 child Runs 的原子产品同步；
- Group lane scope、触发消息截止点与 Workflow 穿插；
- 群公开 Session Context、Group Workspace/Memory 和私下 A2A 的可见性边界；
- 单 Run 完成与整个群协作完成之间的边界。

## 1. 总体执行模式

Group 使用同一套 Runtime，支持两种约束模式。

### 1.1 默认：简单初始编排 + 自组织执行

1. 人类消息确定原始目标和首批被 `@` 的 Agent。
2. Runtime 先按稳定 participant ID 去重有效 Agent mentions，再根据发送者类型和 mention 数量选择入口；不使用 LLM 判断人类任务内容是否“复杂”。
3. 人类单 `@` 不启动 Planner，Runtime 直接创建对应 Agent 的普通 Group Run。
4. 人类多 `@` 为该消息强制启动且只启动一次 Planner；Planner 输出统一的 `mode = advisory`、完整 `plan_prompt` 和入口任务。
5. 默认自组织模式下，Agent 执行中无论公开 `@` 一个还是多个 Agent，都不因 mention 数量自动启动 Planner；Runtime 直接创建对应的 Group child Run，并沿用现有 `parent_run_id / root_run_id` 自关联规则。
6. 所有后续参与 Agent 都获得同一份完整 `plan_prompt`，但 `advisory` 只表示建议，不能覆盖人类原始消息，也不成为执行期的权威硬 DAG。
7. 执行过程中，Agent 根据当前群上下文、私下 A2A 结果和已有产物，自主判断是否继续协作、找谁协作以及何时结束自己的工作。
8. 实际 Run 路径可以偏离初始计划。

### 1.2 人类显式 Workflow

1. 在当前入口规则下，人类多 `@` 先启动同一个 Planner；不增加前置意图分类器、UI 开关或关键词匹配器。
2. Planner 直接读取人类原始 Prompt，判断人类是否明确规定了 Agent、顺序、轮数、依赖、分工或完成条件。
3. 如果这些流程约束来自人类 Prompt，Planner 使用同一输出结构设置 `mode = enforced`；如果没有，则设置 `mode = advisory`，进入默认自组织模式。
4. Planner 不能把自己建议的流程升级为强制 Workflow；语义不明确时默认 `advisory`。
5. `enforced` 模式下，`plan_prompt` 是所有参与 Agent 都会获得的完整执行协议，包含角色、顺序、条件、循环、分支、公开交接和完成规则；它不要求被压成固定 DAG。
6. Planner 只启动 Workflow 的入口 Agent 或入口并行任务，不在后续执行中充当持续协调器。之后由当前 Agent 根据完整 `plan_prompt`、群上下文和当前结果决定下一步。
7. 当前 Agent 认为 Workflow 尚未完成时，在终态公开回复中按计划 `@` 下一位 Agent；认为完成条件已经满足时，输出最终结果且不再 `@`。
8. 后续每个由 Workflow 公开 `@` 创建的新 Run，都获得同一份不可变 `plan_prompt`、人类原始 Prompt、自己的当前责任以及必要的上游公开结果。
9. Agent 可以在 Workflow 内使用普通私下 A2A；A2A 是当前 Run 的内部辅助，不替代公开 Workflow 交接。
10. Runtime 不做 Workflow 语义判定，不调用另一个 LLM 判断某次公开 `@` 是否“符合计划”；V1 通过 Prompt 优先级约束 Agent，只校验身份、权限、预算、幂等和循环保护。
11. `enforced` 不新增独立 Scheduler、Workflow Runtime、状态表或系统调度消息。它与自由模式使用同一套公开 `@`、新 Run、A2A 和终态机制。
12. 未被人类 Workflow 限定的步骤内部实现仍可由 Agent 自主决定，但不能改变人类明确规定的流程约束。
13. 约束优先级固定为：

```text
平台安全、权限和预算
  > 人类原始 Prompt（包括显式 Workflow 和后续修改）
  > Planner 从人类 Prompt 编译出的 plan_prompt
  > Agent 的现场判断
  > 初始 Planner 建议
```

### 1.3 Planner 统一输出

自由模式和 Workflow 模式不使用两套 Planner schema，只通过 `mode` 改变同一份计划 Prompt 的约束强度：

```json
{
  "version": 2,
  "mode": "advisory | enforced",
  "goal": "协作目标",
  "plan_prompt": "完整计划、角色、条件、分支和完成规则",
  "entry_steps": [
    {
      "agent_id": "入口 Agent ID",
      "instruction": "当前入口责任"
    }
  ]
}
```

固定规则：

1. `plan_prompt` 在两种模式下都完整提供给所有参与 Agent；`advisory` 可以偏离，`enforced` 必须服从人类明确规定的部分。
2. `entry_steps` 只决定第一批启动谁，可以包含一个或多个并行入口，不描述后续 DAG。
3. 后续协作统一由 Agent 终态公开 `@` 推进，不再由 Planner 持续调度。
4. 参与者的稳定 `agent_id / participant_id` 继续来自 Runtime 已有的 `candidate_agents / mention_targets`，Planner 不重新生成身份映射。
5. Runtime 只校验 schema、入口 Agent 是否属于候选集合、字段非空与去重；不校验自然语言计划的业务正确性。
6. 每个 Agent 实际获得“平台固定执行规则 + 人类原始 Prompt + 完整 `plan_prompt` + 当前责任”。Planner 只生成任务专属计划，不重复平台规则。
7. 平台固定执行规则根据 `mode` 明确计划是建议还是强约束，从而降低 Planner 和弱执行模型理解两套协议的压力。
8. Planner 生成并校验合法 v2 plan 后，Planning Run 直接进入 `completed`，checkpoint 保存不可变 `mode + plan_prompt + entry_steps`。稳定 checkpoint 之后由独立产品同步幂等创建入口 child Runs；Planning Run 不再进入 `waiting_agent`，child 完成后也不 resume Planning Run。

### 1.4 Planning v2 落地后的计划与上下文边界

1. Planning Run checkpoint 只保存已通过候选 scope 重新校验的完整 `version = 2 / mode + goal + plan_prompt + entry_steps`；不再保存静态 `steps`、依赖、步骤进度或 child 结果。
2. 合法 plan 生成后 Planning Run 直接进入 `completed`。它不进入 `waiting_agent`，入口或后续 child 终态也不 resume Planning Run。
3. 稳定 completed checkpoint 之后，独立产品同步先一次性重新校验 Planning root、原始触发消息、发送者、权威 mentions、候选映射和全部入口 Agent，再在同一事务内幂等创建且只创建 `entry_steps` 对应的 children；任一入口失效或任一写入失败时，不保留部分 child。
4. 所有入口 child 的 `parent_run_id / root_run_id` 都等于 Planning Run ID，沿用同一原始触发消息、scheduling position、不可变 `mode + plan_prompt`；只有自己的 `goal / current_responsibility / target_participant_id` 不同。未列入 `entry_steps` 的候选 Agent 不会在初始同步中被创建。
5. child payload 不再携带 `planning_root_run_id`、`planning_step_id`、`planning_instruction`、`depends_on_step_ids` 或 Planning 专属 `related_run_summaries`。`group_context.planning_hint` 只接受 `mode + plan_prompt + current_responsibility`，不保留 v1 兼容回退。
6. 后续协作进展只通过当前 Agent 的公开终态交接、群公开消息和已有 Group Session Context 表达；Runtime 不维护步骤状态、完成百分比、剩余任务或独立 `progress_summary`。
7. 入口 child 携带原始触发消息的同一 `context_cutoff` 和 scheduling position；Context Builder 会同时校验 payload、Run source 和 scheduling position，并让 recent messages 与 Group Session Context compact 严格消费同一截止位置。具体已落地边界见 4.3 和 10.2.3。

当前阶段不定义步骤级进度、完成百分比、剩余步骤、步骤状态表或独立 `progress_summary`，也不要求 Runtime 从自然语言 `plan_prompt` 推导这些信息。协作进展只通过当前公开交接消息和已有群 Session Context 表达。以后如果正式引入 DAG，再连同节点状态、依赖完成条件、分支汇合和进度计算方式一起设计；不能在没有 DAG 的情况下先造一套伪精确的步骤进度。

### 1.5 目标 Prompt 与 Tool 装配边界

Group Agent Run 继承 Base Prompt V1，但 Planning 根节点例外：Planning 根节点继续使用独立、无业务工具、只输出 Planning JSON 的专用 Prompt，不套数字员工 Base Prompt，也不暴露 `finish / wait`。

普通 Group Agent Run 的目标装配顺序固定为：

```text
稳定前缀：Name + Soul + Base Prompt V1
+ 条件化的 Group Capability Policy（仅一份）
+ 动态后缀：当前 Group scope、触发消息、公开上下文、plan_prompt、当前责任
+ 规范化的当前 Thread messages / Tool Exchanges
+ 本轮有效 Tool Schema
```

具体约束：

1. 当前 Agent 的 `role_description` 不再作为身份或指令注入 Group self context；其他成员的 role 可以作为通讯录/成员发现数据保留。
2. `GroupContextBuilder` 不再重复注入 Base Prompt 已有的 `scope_rules`、`tool_permissions` 或同一 Planning instruction。`group_context.planning_hint` 只保存必要的结构化 `mode + plan_prompt + current_responsibility`，不复制整份 `initial_input`。
3. 人类原始消息、当前责任、`plan_prompt`、直接上游公开结果和 recent messages 必须各有唯一来源；不得同时在 `current_run.goal`、`initial_input`、Group context 和普通 messages 中重复成多份可执行指令。
4. Group 资源 scope 仍受限：只能访问当前群成员、当前 Group Workspace 和当前 Group Memory，并且只能写当前 Agent 自己在该 Group 中的 Memory；这些授权边界只在可信 Group Capability Policy 中出现一次，不能为了缩短 Prompt 删除。该限制不禁用 Agent 自身的 Memory、Skills、Tools 或 Workspace，也不把其中内容自动公开到群里。
5. 本轮有效 Tool 集必须先解析并追加 Group tools，再据此生成 `allowed_tool_names` 和 Capability Policy，避免 Prompt 宣传实际不可调用的能力。
6. Group tools 必须进入 canonical builtin definition 数据模块，并且只有经过校验的 `group_context` 存在时才可见；不增加 Registry class 或第三层 adapter，自定义工具不得覆盖 `group_*`、`finish` 或 `wait`。
7. `group_query_members` 对 Agent participant 必须显式返回稳定 `agent_id`，同时保留兼容字段 `participant_id / participant_ref_id`；后续 `group_read_memory(agent_id)` 和终态公开 mention 不得依赖显示名猜测身份。
8. Group tool handler 与普通/A2A tool handler 统一返回扩展后的既有 `ToolExecutionOutcome`。Tool 结果、effect、retry policy、artifact/evidence 和大结果引用都遵守共享 Tool Ledger 契约，不返回只靠字符串前缀判断成败的旁路结果。
9. 模型正常通过当前公开上下文和 `group_query_members` 得到协作对象；本版不增加 Tool Search、向量检索、Graph Retriever 或复杂语义工作集优化。
10. 按 Group PRD，Agent 自身上下文和普通工具能力继续复用单 Agent 机制，包括 Agent 自身 Memory、Skills、Tools 和 Workspace；不因为进入 Group 就改成一套只允许 Group tools 的封闭 allowlist。Group scope 只限制 Group member、Group Memory、Group Workspace 与群消息等群资源，不能把 Agent 私有内容自动视为群共享内容。
11. Agent Workspace、私信或其他空间的内容复制到 Group，或把 Group 内容发送到私信/外部渠道，必须遵守 PRD 2.10.1：由人显式触发、只处理用户明确指定内容、校验触发人权限、必要时预览/确认、记录来源与审计。`send_channel_message / send_channel_file / send_platform_message` 等语义等价动作必须归一到同一 canonical action，不能通过工具别名绕过授权。
12. 群公告、成员 role、Group Memory、Workspace 正文、Planner 生成的 `plan_prompt` 和用户消息都是低信任数据。稳定 system prefix 只放平台边界；这些正文必须进入明确的 dynamic data / user-context 通道，不能因为包在 `group_context` JSON 中就获得 system instruction 权限。
13. Group read tool 的大正文不能只被 8 KiB inline 上限截成不可继续的摘要。文件读取应提供有界 chunk/offset 或 continuation；已有 Workspace revision/path 作为稳定 artifact ref，私有 Tool Result Store 只保存执行归档，不替代 Group Workspace 本身。
14. Group write Tool 的模型回执只返回有界 summary、path、content hash、version/revision 和必要 ref，不回显刚写入的完整正文。storage 成功而 revision/ledger 未 settle 时必须靠稳定 operation ID 对账或补偿，绝不自动重做一次写入。

## 2. 群内公开 `@` 与唤醒

### 2.1 统一心智

群聊中的 Agent 唤醒与公开 `@` 使用同一个用户心智：

```text
业务 Agent 开始一个公开协作 Run
  => 群成员一定能看到与该次协作入口对应的结构化 @Agent
```

硬规则：

1. 群内没有不可见的业务 Agent 唤醒。
2. 只要业务 Agent 被群消息唤醒，群成员就必须看到对应的公开 `@`。
3. 人类单 `@` 和 Agent 终态公开 `@` 直接创建目标 Agent Run；人类多 `@` 先创建唯一 Planning Run，mentions 是 Planner 的候选参与者集合，不要求所有候选立即并发启动。
4. 如果只想在文字中提到某个 Agent、但不想唤醒，应直接写名字，不使用 `@`。
5. `@` 必须保存为结构化 mention，不能只依赖模型输出文本或正则解析。
6. 同一条消息重复 `@` 同一个 Agent，只产生一次唤醒。
7. 一条消息可以 `@` 多个 Agent，对应多个公开协作入口。
8. 人类多 `@` 是 Planning 的公开入口：所有 mentions 都成为 Planner 的候选参与者，但 Planner 只启动计划要求的入口 Agent。非入口 Agent 真正轮到执行时，由上一位 Agent 再次公开 `@`，不生成系统调度 `@`。

### 2.2 原子性

一次公开 `@` 必须原子形成：

```text
公开 ChatMessage
+ 结构化 mentions[]
+ 对应的 Planning Run 或 Agent Run
+ Runtime dispatch command
```

不得出现“看见 `@` 但没有唤醒”，也不得出现“Agent 已被群聊唤醒但群里没有可见 `@`”。公开 `@` 只创建新 Run，不恢复已经结束的旧 Run。

### 2.3 Agent 之间互相 `@`

以下规则同时适用于默认自组织模式和 `enforced` Workflow；区别只是前者可以现场偏离 `plan_prompt`，后者必须遵守其中来自人类的硬约束。

1. Agent 认为公开协作还没有结束时，可以在当前 Run 的终态公开群回复中继续 `@` 其他 Agent。
2. 公开 `@` 是一次 Run 回合结束后的公开交接；Run 执行到一半时，正文里出现的 `@` 不产生唤醒。
3. 当前 Agent 发布带 `@` 的终态公开回复后，当前 Run 结束；每个被 `@` Agent 都创建新的 Group child Run。
4. 新 child Run 按现有 AgentRun 自关联规则记录 `parent_run_id / root_run_id`；不增加 `workflow_execution_id`。如果来源 Run 来自 Planner 协作，同时把不可变 `mode + plan_prompt` 传给新 Run。
5. 被 `@` 的 Agent 获得当前群 Session 的公开上下文，并在群里公开回应；它如果需要把公开责任交回先前 Agent，必须在自己的终态回复中再次公开 `@` 对方，由 Runtime 创建一个新的 Run，不恢复已完成的旧 Run。
6. 默认模式下，Agent 认为不再需要新的公开协作者时不再创建新的公开 `@`；Workflow 模式下，Agent 在完成条件满足时停止 `@`。
7. Agent 在同一条终态消息中公开 `@` 多个 Agent 时，所有目标 child Run 使用同一条不可变的触发消息和相同的上下文截止点；每个 child Run 只额外获得自己的 `target_participant_id`。
8. Runtime 不调用 LLM 为多个目标重新拆写任务；各目标的责任由发起 Agent 在同一条公开消息中用自然语言说明。

### 2.4 Agent 发起公开 `@` 时的任务说明

Agent 自己发起公开 `@`，说明它已经掌握当前上下文并决定继续协作，因此应由发起 Agent 在公开消息中说明希望目标 Agent 做什么，不再自动启动 Planner 补写意图。

Prompt 行为要求：

1. 公开 `@` 其他 Agent 时，应说明希望对方完成、判断或回应什么。
2. 同时 `@` 多个 Agent 时，应尽可能分别说明每个 Agent 的责任。
3. 如果希望多个 Agent 围绕同一问题公开讨论，应明确说明这是共同讨论，而不是不同子任务。
4. 任务说明使用自然语言，不强制弱模型生成复杂的 instruction、dependency 或 completion schema。
5. `enforced` Workflow 中，公开消息还应说明当前判断、满足了哪条转换条件，以及希望下一位 Agent 执行的责任。

Runtime 只强制机器可判断的结构：

- mention 指向当前群内有效、可唤醒的 Agent participant；
- 公共消息非空，mention 使用稳定 ID；
- message、mentions、child Run 和 dispatch command 满足原子性与幂等；
- 权限、预算和当前来源 Run 的状态允许继续协作。

Runtime 不判断“任务是否真的说清楚”，也不因为语义模糊自动启动 Planner。合法 JSON 或非空字段不能证明任务语义明确，因此不设置语义清晰度硬门槛。

Agent 终态消息同时 `@` 多个目标时采用全有或全无校验：Runtime 必须在来源 Run 提交终态前验证全部 participant ID、群成员资格、可用状态、权限和预算；任一目标无效时，不发布群消息、不创建任何 child Run，而是把可修复错误返回当前模型。全部有效后，产品侧在同一事务中提交公开消息、mentions、child Runs 和 dispatch commands。来源 Run 的 terminal checkpoint 属于 Runtime checkpoint 事务，不宣称与产品侧投递是同一数据库事务。Agent 不知道稳定 participant ID 时，应先通过现有群成员查询工具获取，不能根据显示名猜测。

目标 Agent 如果无法根据公开消息和群 Session Context 理解请求，应公开 `@` 发起 Agent 追问；该追问在同一 `root_run_id` 下创建新的 child Run。结构化 mention 无效时，Runtime 可以向当前模型返回简单错误并允许有限重试，但不得先发布一个无法兑现唤醒的公开 `@`。

#### 2.4.1 Group 终态的结构化协议

共享 Base `finish` 仍然只有必填 `content`。只有在已校验的 Group Agent Run 中，Runtime 才把同一个 `finish` Tool Schema 条件化扩展为：

```json
{
  "content": "最终公开群回复",
  "mention_participant_ids": ["稳定 participant UUID"]
}
```

固定规则：

1. `mention_participant_ids` 可选、去重、有界，只接受当前群内可唤醒 Agent participant；空数组与未提供都表示当前 Run 只公开回复、不继续交接。
2. 这不是第二个完成协议，也不是 artifact/evidence 申报入口。artifact/evidence 仍只能由当前 Run 已成功的 typed `ToolExecutionOutcome` 派生。
3. 非 Group Run 的 `finish` Schema 不出现该字段，parser 也拒绝旁路字段；Group Planning 根节点不使用 `finish`。
4. mention 是产品交付动作，不进入通用语义 Verifier。Group delivery preflight 在 terminal checkpoint 前校验全部目标的 scope、成员资格、Agent 状态、权限、数量、深度、预算和循环保护；可修复错误进入现有 repair loop。
5. preflight 通过后，terminal checkpoint 只保存不可变 delivery intent。随后产品同步以该 intent 为输入，原子创建公开 `ChatMessage + mentions + child Runs + start commands`，并写幂等回执。
6. 如果 preflight 后成员状态发生竞态变化，产品同步必须 fail closed，并以同一 intent 重试或形成可观察的 delivery failure；不得部分发布、猜测替代目标、回滚 checkpoint 或重新执行来源 Run。
7. 不从 `content` 文本中解析 `@名字`，也不新增平行的 `group_finish` 或 `group_handoff` Tool。

### 2.5 `enforced` Workflow 中的 Agent 公开 `@`

1. Agent 公开 `@` 是 Workflow 的正常轮转方式，不是计划外行为；仍然只能发生在当前 Run 的终态公开回复中。
2. 当前 Agent 根据 `plan_prompt` 和当前结果选择下一位 Agent：未满足完成条件时公开 `@` 下一位，满足完成条件时返回最终结果且不再 `@`。
3. “直到达成共识”“审核不通过返回重做”和结果分支都由当前 Agent 按照 Workflow Prompt 判断，并通过公开 `@` 形成下一 Run。
4. 每个新 Run 继承同一份不可变 `mode + plan_prompt`。Agent 不得改写人类流程、增加计划外目标或把自己的建议提升为新硬约束。
5. Runtime V1 不判断公开 `@` 的业务语义是否符合计划；它只验证结构化 mention、成员身份、权限、预算、幂等、深度和循环保护。模型偏离 Workflow 属于行为可靠性风险，不伪装成 Runtime 已经形式化保证。
6. 人类公开 `@` 不受 Agent Workflow 约束。人类新消息会创建独立执行，当前实现不会自动把它并入正在运行的 Workflow。

### 2.6 同一 Agent 连续被人类 `@`：当前实现

当前代码已经存在串行机制，但文档此前没有说明。现状如下：

1. 不同人类群消息每次有效 `@` 都立即保存自己的 `ChatMessage`，并创建独立的新 `AgentRun + start command`；不会合并、覆盖或恢复该 Agent 已有的 Run。
2. 人类直接 `@` 创建的 Run 没有 `parent_run_id / root_run_id`，因此即使 Agent 此时正在执行另一个 Planning/Workflow child，它仍然是独立顶层 Run，不会自动归入旧 Planning Run。
3. 普通人类单 `@` Run 和 Planning child Run 使用同一个 `scheduling_lane_key = group_mention:{tenant_id}:{agent_id}`。
4. 同一 Agent 的这些公开 Group Run 不会同时执行。当前 Run 持有 lane 时，后续消息、Run 和 command 已经落库，但 start command 保持 pending；只有前一个 Run 进入 `completed / failed / cancelled` 后才释放 lane。
5. `waiting_user / waiting_agent / waiting_external` 都不会释放 lane。因此当前 Run 私下 A2A 并处于 `waiting_agent` 时，新的人类公开 `@` 会继续排队，而不是并发启动。
6. lane key 不包含 `group_id / session_id`。因此当前串行范围实际是“同一 tenant 内的同一 Agent”，不同群和不同群 Session 也会互相排队。
7. ACK“收到，我开始处理。”不是入队时发送，而是在该 start command 真正取得 lane、准备执行时才发送。排队期间用户看不到独立的“已排队”提示。
8. 每个 Run 结束后独立向原群 Session 写一条普通 Agent `ChatMessage`。正常路径中，前一个 Run 的终态回复先投递，然后释放 lane，之后下一个 Run 才 ACK 并开始，因此同一 Agent 通常表现为：

```text
第一条 @ 消息
Agent ACK 1
第二条 @ 消息（此时只入队）
Agent 最终回复 1
Agent ACK 2
Agent 最终回复 2
```

9. 不同 Agent 拥有不同 lane，回复仍可能在群里交错出现。
10. 当前 `ChatMessage` 没有 `reply_to_message_id` 或 `source_run_id`。内部可以通过 `AgentRun.source_id -> 触发消息` 以及 delivery event `run_id -> 最终 message_id` 追溯，但当前群消息 API/UI 只显示普通时间线，用户不能直接看出某条回复对应哪一次 `@`。
11. 输入快照仍在 start command 真正执行时捕获，但 Group Agent Run 只读取触发消息 `(created_at, id)` 及以前的 recent messages 和 Session Context；排队延迟不会把前一个 Run 在截止点之后产生的 ACK、最终回复或其他新增群消息混入该 Run。后续结果如需该 Agent 处理，必须由新的公开 `@` 创建拥有新截止点的新 Run。

Workflow 执行期间再次被人类 `@` 时，当前实现还有一个明确边界：

1. 新的人类 Run 不会打断正在执行的 Workflow child，只会等待同一 Agent lane。
2. 当前 child 终态公开 `@` 下一位 Agent 时，会直接创建新的公开 handoff child；已经 completed 的 Planning Run 不 resume，也不参与后续 lane 竞争。
3. 同一 Agent lane 的 pending starts 按各自公开触发消息的 `(created_at, id)` scheduling position 排序。人类新 `@` 如果早于后续 handoff 公开消息，就会在当前 lane 释放后先执行；如果晚于 handoff，则排在 handoff child 之后。该排序不依赖单 worker 或多 worker 的偶然竞争。
4. 新的人类 Run 不会取消、修改或完成原 Workflow；它只是独立执行，但可能延迟同一 Agent 的后续 Workflow 步骤。

以上是现有实现，不代表边界已经全部确定。后续需要分别决定：

- lane 是否继续按 `tenant + agent` 全局串行，还是缩小到 Group/Session；
- 人类新 `@` 与 `enforced` Workflow 后续步骤谁优先，是否允许穿插；
- 群消息是否需要显式展示回复所对应的触发消息；

`waiting_user` 已按 0.2 和 7.2 收敛：在群内没有稳定回复关联前，普通 Group Agent Run 不进入该状态，而是公开提出问题并结束；后续人类 `@` 创建新 Run。

queued Group Run 的 cancel 也按共享 cancel 语义固定：后续 start 因同 Agent lane 尚未取得而阻塞时，cancel 必须能越过该 Run 自己尚未 applied 的 start，直接把它结算为 `cancelled_before_start`；不发送 ACK、不创建 checkpoint、不影响当前 lane holder，也不得被 earlier pending start 永久阻塞。

当前 Planning 已按 1.3 落为统一 `mode + plan_prompt + entry_steps + Agent 公开 @ 轮转`；只创建入口 children，不再维护静态 DAG、resume Planning Run 或调度后续步骤。入口 children 与后续公开 handoff children 都按 4.3 消费各自已冻结的严格触发消息 cutoff。

## 3. 私下 A2A

### 3.1 直接复用全局 A2A

Group 不新增一套“群内私信协议”。按 Group PRD 2.7.4，群内发起 A2A 与普通 A2A 相同，直接复用现有：

- `send_message_to_agent` 工具；
- `notify`；
- `consult`；
- `task_delegate`；
- 独立 `session_type = a2a` 的 ChatSession；
- delegated Run、等待、结果返回和原 Run 恢复机制。

全局 A2A 的产品语义已经确定：同一 tenant 内同一 Agent pair 可以复用现有 pair-global A2A `ChatSession`、私下消息历史和 Session Context；不按来源 Group、Group Session、来源用户或来源 Run 再切分上下文。Agent 之间的长期私下协作连续性是允许的，不作为跨群泄漏缺陷修复，也不新增 source-scoped A2A 表或上下文容器。

Group 在这里新增的只有公开边界：

1. A2A 请求、过程和原始结果保存在全局私下 A2A 会话中，不写入当前 Group `chat_session_id` 的公开消息、Group Session Context、Group Memory 或 Group Workspace。
2. A2A Tool contract、delegated `AgentRun`、Tool Ledger、`parent_run_id / root_run_id`、correlation 和 `waiting_agent -> resume` 全部沿用普通 A2A。
3. Group 上下文可以记录来源 Run 用于 trace、权限和回传关联，但这些字段不参与 A2A Session 隔离，也不改变 pair-global 消息历史。
4. 只有调用 Agent 后续整理并公开表达的内容才进入 Group ChatMessage 和 Group Session Context。

### 3.2 可见性边界

1. A2A 是 Agent 的内部工具调用和私下执行过程。
2. 人类和群成员看不到 A2A 请求、过程消息和原始结果。
3. A2A 不在群里渲染 `@`，也不写入当前群 `chat_messages`。
4. A2A 内容不自动进入群 Session Context、群 summary、群 workspace 或群 memory。
5. `consult` 和 `task_delegate` 的结果返回调用 Agent，并恢复原调用 Run。
6. 调用 Agent 认为结果对群协作有价值时，必须自行整理成公开群回复；只有整理后公开表达的部分才进入群上下文。
7. A2A 结果没有群价值时，可以完全不在群里出现。
8. `enforced` Workflow 内的 A2A 仍遵守以上规则：它是当前 Run 的内部协助，不替代 `plan_prompt` 要求的公开发言或公开 `@` 交接。

因此，可见性规则固定为：

```text
公开 @ 和公开回复 -> 群成员可见，进入群 Session Context
私下 A2A          -> 群成员不可见，只返回调用 Agent
```

### 3.3 公开 `@` 与私下 A2A 的选择

Agent 使用以下固定判断：

```text
没有目标 Agent 的返回结果，当前 Run 就不能完成
  -> 使用私下 A2A；当前 Run 可以 waiting_agent，并在结果返回后恢复

当前 Run 已经可以结束，接下来由其他 Agent 在群里公开继续
  -> 在终态公开回复中 @ 对方；当前 Run 结束，对方创建新 Run

只需要让对方知道，不需要返回结果
  -> 使用 A2A notify
```

因此，群聊公开协作层不使用“公开等待并恢复旧 Run”的机制。`waiting_agent` 只属于私下 A2A 的内部执行过程，群成员不可见。

### 3.4 A2A 对共享 Runtime 变更的继承

1. Group 场景里的 `send_message_to_agent` 与 A2A delegated Run 继续走既有 A2A 入口和同一 `ToolExecutionOutcome`，不得返回一套 Group 专属字符串结果。
2. `notify` 完成后当前 Run 可以继续；`consult / task_delegate` 创建 delegated Run 后，来源 Run 进入 `waiting_agent`，目标完成时以精确 correlation 恢复同一来源 Run 和同一 Thread。
3. A2A 的 reservation、effect、retry policy、started/succeeded/failed/unknown、结果引用和幂等全部写入共享 Tool Ledger；产品同步失败不得导致重复发送或重复创建 delegated Run。
4. cancel 当前 Group Run 时，只中断该 Run。已经产生的外部副作用不回滚；是否取消仍在执行的 A2A child 不是默认行为，需要独立产品策略。

## 4. Runtime 必需的执行关联

本方案不再引入 Collaboration Graph，也不引入独立的 Execution Root 对象。Runtime 只复用现有 AgentRun、ChatMessage、Planning checkpoint 和 A2A 记录。

### 4.1 AgentRun 现有父子字段

`parent_run_id / root_run_id` 是 `AgentRun` 上已有的可空自关联字段，不是另一张 Root 表，也没有独立 Root 状态或生命周期。

固定规则：

1. 人类单 `@` 创建的顶层 Agent Run：`parent_run_id = null`，`root_run_id = null`。
2. 人类多 `@` 创建的顶层 Planning Run：`parent_run_id = null`，`root_run_id = null`。
3. Planning 创建的入口 child：`parent_run_id = Planning Run.id`，`root_run_id = Planning Run.id`。
4. A2A delegated Run：`parent_run_id = 调用 Run.id`，`root_run_id = 调用 Run.root_run_id or 调用 Run.id`。
5. 默认自组织模式和 `enforced` Workflow 由 Agent 公开 `@` 创建 child Run 时，都沿用同一规则：`parent_run_id = 来源 Run.id`，`root_run_id = 来源 Run.root_run_id or 来源 Run.id`。Planner 协作 child 额外继承来源 Run 输入快照中的不可变 `mode + plan_prompt`。
6. 人类在任何时候发出的新公开 `@` 都创建新的顶层 Run，不继承当前正在运行的 Planning/Workflow lineage，也不恢复已经结束的旧 Run。
7. 这些字段只记录执行来源，不用于判断群消息是不是“同一个长期任务”，也不作为额外业务上下文注入普通 Agent。
8. 不做 Root 级完成聚合、Root 状态机或全局静止检测；Planning Run 只负责生成计划并启动入口 children，后续公开协作由 Agent 自己推进。

### 4.2 使用现有业务记录

需要保留的事实分别由已有业务对象记录：

| 事实 | 记录位置 |
|---|---|
| 群公开内容和公开 `@` | `ChatMessage + mentions` |
| Agent 执行及父子关系 | `AgentRun` |
| 私下 A2A 调用、delegated Run 和返回 | A2A 工具调用及 Runtime 关联记录 |
| Planner 建议或人类强制 Workflow | Planning Run checkpoint 中的 `mode + plan_prompt + entry_steps` |
| Workflow 入口任务 | Planning 创建的入口 child `AgentRun` |
| Workflow 后续公开轮转 | 来源 AgentRun、公开 ChatMessage mentions 和新 child AgentRun |

Run 至少需要以下字段或等价信息：

```text
run_id
agent_id
root_run_id / parent_run_id
source_id（Group Run 中指向触发消息 ID）
payload.message_id
scheduling_position_created_at / scheduling_position_id
runtime_thread_id（Group 中每个 Planning/Agent Run 独立）
graph_name / graph_version（仅作观测元数据，不参与 checkpoint 恢复路由）
start/resume/cancel AgentRunCommand
command.applied_checkpoint_id
LangGraph config 中的 thread_id
checkpoint metadata 中的 clawith_run_id / clawith_command_id
delivery_target / delivery_status
mode + plan_prompt（Planner 协作 Run 输入快照内；不是新表或 AgentRun 列）
```

执行状态、waiting、结果、错误和验证事实由 `RunStateReader` 从目标 Run 的精确 checkpoint 读取并转换为 `RunView`，不再存储或读取 `projected_execution_status / projected_result_summary / projection_checkpoint_id / projection_updated_at`。

tenant、Run 与 Thread scope 先由 `AgentRun` 校验；Thread identity 来自 LangGraph config。恢复和对账只使用 namespaced `clawith_run_id / clawith_command_id` metadata，不能把 LangGraph invocation 的通用 `run_id` 当成 Clawith `AgentRun.id`。`graph_name / graph_version` 仅保留为观测元数据，部署后的当前 Graph 代码用于恢复，不按旧 version 路由历史代码。

不为了“协作关系”新增 Graph、节点表、边表、Workflow execution 表或独立事件日志。`mode + plan_prompt` 保存在现有 Planning checkpoint，并复制进相关 Run 的不可变输入快照；现有记录不足以完成 Runtime 路由、隔离、幂等或审计时，优先复用已有字段、Command 和 checkpoint payload。

### 4.3 群上下文快照

目标机制是：每个由公开 `@` 创建的新 Run，都把触发消息作为群消息上下文截止点：

```text
source_id / payload.message_id
+ scheduling_position = (trigger_message.created_at, trigger_message.id)
+ parent_run_id（Agent 公开交接时）
-> new_run_id
```

这里不要求新增 `trigger_message_id` 或 `context_cutoff_*` 列；现有 `source_id`、payload 和 scheduling position 已经携带触发位置。

**已落地实现（2026-07-16）：** Group Agent Run 在首次 start 捕获输入时，会交叉校验 payload `message_id / context_cutoff`、`AgentRun.source_id` 和 scheduling position；随后再到权威 Group `ChatMessage` 校验完整 `(created_at, id)`。任一字段缺失、scope 不符或位置不一致都 fail closed。Planning 根节点不注入可变 Group Session Context；它完成后创建的入口 Agent Runs 才按原始触发位置执行严格截止。

recent 与 pending 查询都使用 `message_position <= cutoff`，即 `created_at` 优先、同一时间用 UUID `id` 排序。共享 rolling Session Context 只有同时满足以下条件才可直接使用：`covered_through_message_id` 可解析且位置不晚于 cutoff，`SessionContextState.updated_at` 带时区且不晚于 `cutoff.created_at`。第二个条件不能由 watermark 代替，因为 cutoff 之后完成的 Run 可以通过 terminal delta 更新 rolling summary，而最近公开回复仍留在 recent 区，导致 watermark 没有同步前进。`updated_at == cutoff.created_at` 允许复用；时间缺失、无时区、晚于 cutoff，或 watermark 越过 cutoff 时，Context Builder 都从仍保留的截止点前原始群消息出发，调用同一个无副作用、带确定性批处理的 Session compactor 临时重建有界 snapshot，且不回写共享 Session Context。重建不可用、失败或 watermark 不一致时同样 fail closed。最终 snapshot 只写入该 Run 的 LangGraph checkpoint；resume 和 checkpoint 重放只复用这份快照，不重新读取 Group Session。

固定规则：

1. 新 Run 的基础群消息上下文由现有 Session Context 和压缩机制生成，但只读取截止位置及以前的消息，不按 Worker 真正开始执行的时间读取最新消息。
2. 同一条公开消息同时创建的多个 Run 使用相同的群消息截止点；排队和实际执行先后不能改变它们的语义输入。
3. 触发消息之后产生的普通群消息或其他 Run 结果，不自动注入已经创建的 Run。
4. 后续信息如果需要某个 Agent 处理，必须通过新的公开 `@` 创建新的消息、上下文截止点和 Run。
5. Run 状态、队列状态和其他 Runtime 控制面变化不得反向改写已启动 Run 的语义输入快照。
6. 后续新 Run 按自己的新截止点读取群 Session Context；历史 Run 的输入保持可重放。
7. Run 父子树、A2A trace 和 Runtime 控制记录不默认注入普通 Agent 上下文。
8. `mode + plan_prompt` 例外：它们会直接影响 Agent 行为，因此属于 Planner 协作 Run 的必要业务输入，必须提供给入口 Run 和后续公开 child Run；它们不是协作图或 lineage 摘要。
9. Group Session Context compact 必须使用同一消息截止点生成或选择可用版本；不能在 recent messages 截断到触发消息的同时，注入包含触发消息之后内容的最新 compact。
10. Group Session 原始消息和产品记录不因 compact 被删除；compact 只是多个独立 Group Run 共享公开历史的有界派生上下文，不是 LangGraph 执行真值。

### 4.4 LLM 与 Runtime 的固定分工

| 动作 | 谁决定 | 谁验证并保存记录 |
|---|---|---|
| 人类公开 `@Agent` | 人类 | Runtime |
| Agent 公开 `@Agent` | Agent LLM | Runtime |
| `send_message_to_agent` | Agent LLM | Runtime |
| A2A 结果返回 | A2A 终态回调 | Runtime |
| 人类 Workflow 编译为完整 Prompt | Planner LLM | Runtime 保存到 Planning checkpoint 和入口 Run snapshot |
| Workflow 中下一位公开 `@` | 当前 Agent LLM 按 `plan_prompt` 判断 | Runtime 校验并创建新 Run |
| `parent_run_id / root_run_id` | Runtime | Runtime |
| 人类多 `@` 的初始建议 | Planner LLM | Runtime 保存到 Planning Run |

硬规则：

1. LLM 只提交结构化 mention、工具调用、Planner 的 `mode + plan_prompt + entry_steps` 或终态动作。
2. Runtime 校验目标身份、群或 A2A scope、权限、预算、幂等和当前 Run 状态，再写入对应的现有业务记录。
3. 不增加 Graph Retriever、Graph Resolver、Embedding、`continue/unrelated` 分类或任何 Graph 相关 LLM 输出。
4. 不把 Run 父子关系或其摘要作为额外业务上下文提供给 Agent。
5. 不因为判断“是否继续旧任务”而阻塞 Agent 执行；Agent 直接根据当前消息和压缩后的群 Session Context 工作。

### 4.5 明确删除的机制

当前方案不包含：

- `collaboration_graph_id`；
- `goal_version / continues_from_root_id`；
- `suggested_graph / actual_graph`；
- Graph 节点、边、归属、候选检索和语义合并；
- Graph 相关可视化、持久化、上下文注入和模型调用；
- 将历史 Graph 提升为 Workflow 的机制。

如果以后需要执行路径分析或可视化，应作为独立需求，从已有消息、Run、A2A 和 Planning checkpoint 离线查询，不回到当前 Agent 执行关键路径。

### 4.6 Group Run 状态与产品同步

Group 的每个 Run 都按自己的 Command/checkpoint identity 独立结算：

```text
Command claim
-> 读取该 Run 的精确 checkpoint
-> 执行到 stable waiting / terminal
-> Command applied_checkpoint_id settle
-> Group 产品同步读取 RunView / delivery intent
-> 幂等写 ACK、公开回复、mentions、child Runs、Planning 入口同步和 lane release
```

产品同步需要拆成可独立重试、带稳定幂等键的窄处理器。ACK、普通 delivery、带 mention 的 handoff delivery、Planning 入口 child 创建、A2A completion、Session Context merge 和 lane release 可以由不同 handler 负责，但任何 handler 都不得决定 Graph 下一节点，也不得因自身失败重新运行模型或工具。

同一 Group Run 的 `completed` 只代表该 Run 已通过确定性验证并形成稳定 delivery intent。公开消息已经可见、child Run 已创建等产品事实必须分别以同步回执为准；不得把 terminal checkpoint 当成所有产品副作用已经完成的证明。

## 5. Run 结果与公开群回复

当前实现不增加 `internal_result`、`public_reply` 或 `GroupPublicReply` 领域对象：

1. 普通 Group Run 完成后，现有 checkpoint 中的 `final_answer / delivery_request` 经过 delivery 写成群 `ChatMessage`。
2. delivery event 内部保存 `run_id -> message_id` 回执，用于幂等和追溯；`ChatMessage` 自身目前不保存 `source_run_id` 或 `reply_to_message_id`。
3. 私下 A2A Run 的结果返回调用 Agent，不直接写入群消息；调用 Agent 决定如何整理成自己的公开答案。
4. 默认自组织模式或 `enforced` Workflow 需要 Agent 终态公开 `@` 时，最小改动是在现有 `finish -> lifecycle.delivery_request -> delivery` 链路中增加可选的结构化 mention participant IDs，并复用群消息 intake 创建 mentions 和 child Runs；不新增平行交付契约。
5. Planner 来源 Run 创建公开 child Run 时，必须把不可变 `mode + plan_prompt` 继续传入 child 的输入快照；自由模式与 Workflow 模式使用同一传递机制。
6. 当前来源 Run 的 terminal checkpoint 与产品侧群消息投递不是同一个数据库事务；实现时可以保证“公开消息 + mentions + child Runs + commands”原子落库，但不能把来源 terminal checkpoint 也宣称为同一事务。
7. terminal checkpoint 中的 delivery intent 必须包含公开 `content`、已预检的 `mention_participant_ids`、来源 `run_id`、`parent/root` 传播输入、Group/Session scope、上下文截止点和稳定幂等键；不得依赖同步时重新解析模型文本。
8. 不带 mentions 的普通 Group 回复继续复用现有 delivery；带 mentions 的 handoff delivery 复用同一交付契约，只在一个产品事务中额外创建 mentions、child Runs 和 start commands。
9. delivery 成功回执必须能从 `run_id` 定位最终 `message_id`；同一 delivery intent 的重试只能返回已有结果，不能重复消息或重复唤醒。
10. delivery 失败不会把已完成 Run 改回 running。可重试错误由产品 reconciler 重试；不可恢复的 scope/权限竞态以明确 delivery failure 暴露，不伪装成 Run 未完成。

## 6. 公开多轮讨论示例

人类要求 A、B 公开讨论直到达成共识，属于人类 Workflow：

```text
人类：@A @B 围绕方案讨论，直到达成共识

Planner（内部 plan_prompt，mode = enforced）：
- A 首先提出观点
- 尚未达成共识时，当前发言者公开说明分歧并 @另一方
- 达成共识时，当前发言者输出共同结论且不再 @

A：第一轮观点…… @B
B：仍有分歧…… @A
A：修订观点…… @B
B：已达成共识，最终结论是……
...
```

规则：

1. 每轮有用内容都作为普通群消息公开，并进入群 Session Context。
2. 最初的人类结构化 `@A @B` 是 Planning 入口；Planner 启动入口 Agent A，后续每次业务 Agent 被唤醒，群里都有上一位 Agent 的公开结构化 `@`。
3. A、B 每次获得同一份完整 `plan_prompt`，根据当前公开上下文判断继续还是结束。
4. Agent 必须按 Workflow 选择公开交接目标，不得添加计划外参与者或擅自改变人类规则。
5. 公开辩论轮次不是 A2A。
6. 如果辩论 Agent 私下调用第三个 Agent 辅助研究，该支线才是普通 A2A；A2A 内容不可见，调用 Agent只能在后续公开发言中整理引用。

## 7. 完成、等待与协作停止

### 7.1 当前 Run 的确定性完成下限

普通 Group Agent Run 与其他入口共用同一完成协议：

1. 模型只有在当前责任已经完成、必要验证已经执行后才能调用 `finish`；`finish` 必须非空且是该响应唯一 Tool Call。
2. 当前 Run 不得存在 pending Tool Call，Tool Ledger 中也不得存在未结算的 `started / unknown` 执行；reservation 落库后即为 `started`，没有独立 `reserved` 状态。
3. 最终 artifact/evidence 只从当前 Run 已 `succeeded` 的 typed `ToolExecutionOutcome` 收集，并校验 tenant/run scope、真实存在与可读性。
4. Group 结构化 mentions 由 2.4.1 的 delivery preflight 校验，不由自然语言语义 Verifier判断。
5. 可修复协议错误最多进入两次 repair；权限、配置或不可恢复错误形成 typed failure。Verifier 只返回 pass/repair/fail，不增加第二个等待路由。

### 7.2 协作停止边界

1. 默认自组织模式下，Agent 可以结合现场情况偏离 `plan_prompt` 并决定是否发出新的公开 `@`；`enforced` Workflow 下，Agent 根据完整 `plan_prompt` 的转换条件决定是否公开 `@` 下一位。
2. Agent 需要私下协助时，显式调用普通 A2A 工具；没有目标 Agent 的结果就无法完成时，当前 Run 可以 `waiting_agent` 并在精确回传后恢复。
3. Group Agent 需要用户补充时，不使用当前缺少群回复关联的 `waiting_user`。它以终态公开回复提出问题且不继续 `@`；用户后续新的结构化 `@` 创建新顶层 Run。
4. Agent 认为自己的当前责任完成时，不再发起新的协作，并返回结果。它结束的是自己的 Run 或责任分支，不会自动关闭其他 Agent 分支。
5. 不新增 execution root 级静止检测器、Root 状态机或语义完成裁判。Planning Run 只生成计划并启动入口 children，不承担后续全局协调和完成判定。
6. 当前 root 下没有新的公开 `@`、没有新的 A2A，且既有 Runs 均已稳定终态，只能说明协作执行已经静止；不等于业务答案客观正确、完整或满足了人类 Workflow 的所有自然语言条件。
7. 本版没有“重新打开旧终态 Run”。后续用户输入或 Agent 公开交接都创建新 Run；只有已经稳定 waiting 的同一 Run 才能通过合法 correlation resume。

## 8. Session、Workspace 与 Memory 边界

已经确定：

- 群 Session 保存公开群消息，是群内共享对话上下文。
- A2A Session 保存私下 Agent 协作，不进入群上下文。
- `root_run_id / parent_run_id` 和现有触发消息引用只用于 Runtime 执行关联，不替代消息正文或产物正文，也不默认进入 Agent 上下文。
- 新的人类 Run 通过压缩后的群 Session、Workspace 和 Memory 获得语义连续性，不依赖旧 Run 归属判断。
- Group Session Context compact 是公开群历史的唯一共享 compact。它独立于每个 Group Run 的 LangGraph Thread，不与 Direct Chat Thread Running Summary 合并，也不再额外叠一份 Group Run summary 作为第二份公开历史真相。
- Workspace 文件、Group Memory、成员资料和公告属于动态数据，不进入 Base Prompt 稳定内核；模型通过有界常态快照和真实可用的 Group tools 渐进读取。
- 当前指令和最新的人类消息高于历史 Session compact、Workspace 与 Memory；这些数据不能覆盖平台规则、人类当前目标或 `enforced` 中来自人类的流程约束。

仍需讨论：

1. 哪些内容只保留在 Session，哪些内容应沉淀到 Workspace 或 Memory。
2. Workspace 产物如何记录 `group_id / session_id` 归属以及可选的 `root_run_id / run_id` 来源，并处理并发写入和版本冲突；Run 关联只能作为 provenance，不能限制后续新 Run 读取共享 Workspace。
3. 群 Memory 是事实、决策、协作经验还是摘要，谁有权写入，何时验证后生效。
4. 当前消息、压缩后的 Session、Workspace 和 Memory 同时存在时，Agent 应按什么优先级读取。
5. 用户修改目标后，旧上下文和旧产物如何降级为历史证据，而不是继续成为当前指令。

## 9. Runtime 控制面

Runtime 不参与业务判断，但必须集中维护：

- tenant、group、session、user 和 Planning scope；
- Run 身份、`parent_run_id / root_run_id`、触发消息引用、scheduling position 和幂等；
- Planning、普通 Group Run、公开 child Run 和 A2A delegated Run 的创建与状态；
- 每个 Run/Command 的 checkpoint metadata、`applied_checkpoint_id`、typed `RunView` 和独立产品同步回执；
- 并发、深度、Run 数、token、时间和成本预算；
- 循环检测、取消、超时、失败和恢复；
- A2A 与群公开通道的权限隔离；
- Workspace/Memory 的版本和写入约束。

Runtime 控制面不维护 `projected_*`、Group workflow 全局状态或语义完成结论。Group lane 只负责产品级执行互斥，LangGraph checkpoint 只负责单 Run 执行进度，两者不能互相替代。

## 10. 实施顺序与仍待产品决定的事项

### 10.1 已确认实施顺序

1. 先落地共享 Runtime 正确性：checkpoint/Command 真值、删除 projector、精确 `RunStateReader`、稳定 checkpoint 后的幂等产品同步和 interrupt-and-preserve cancel；Group 作为共享入口做代表性回归，不在这一阶段改变 Planning 产品语义。
2. 落地 canonical Tool definitions、扩展既有 `ToolExecutionOutcome`、Tool Ledger/Result Store、确定性 Verifier和有效 Tool 解析；同步修复 Group member `agent_id`、Group tool typed outcome 与条件化可见性。
3. 落地 Base Prompt V1 和 Context assembly，删除 Group self Role、重复 scope/tool permission、Planning instruction 与相关摘要，保证 Group Agent Run 只有一个当前责任和一份 Group Capability Policy。
4. 把 Planning `version = 1` 静态 DAG 改为已确认的 `version = 2 / mode + plan_prompt + entry_steps`，只启动入口 children，并把不可变计划传给后续公开 child Run。
5. 实现 Group 条件化 `finish.mention_participant_ids`、terminal 前全有或全无 preflight，以及公开消息、mentions、child Runs、commands 的原子幂等产品同步。
6. 实现严格触发消息截止，包括 recent messages 与 Group Session Context compact 的同一 cutoff 语义。
7. 最后做共享入口回归和 Group 专项回归；表结构修改按与 `main` 的最终 schema 差异一次生成一份合并 migration，不在当前开发分支按实现阶段叠加多份迁移脚本。

### 10.2 Group 专属回归门禁

至少覆盖：

1. 人类单 `@` 只创建一个 Group Agent Run；人类多 `@` 只创建一个 Planning Run。
2. Planning v2 只启动 `entry_steps`，入口和后续公开 child 都获得同一不可变 `mode + plan_prompt`。
3. Group/Planning Run 保持独立 thread identity；Direct Chat 的 Session Thread 改动不能把多个 Group Agent 写入一个 Thread。
4. 同一 Group Agent lane 串行，不同 Agent 可按既有策略并行；waiting_agent、cancel、terminal 与 lane release 行为明确。
5. Group Context 不含 self `role_description`、重复 scope/tool policy、重复当前指令或重复 related summaries；Group tools 追加后再计算有效工具名。
6. 非 Group Run 看不到 Group tools 或 Group finish 扩展；合法 Group Run 的 `group_query_members` 返回稳定 `agent_id`。
7. 多目标 handoff 全有或全无；失败不发布消息、不创建部分 child；同步重试不重复消息或唤醒。
8. 每个 child 使用相同触发消息 cutoff；排队延迟不能混入 cutoff 之后消息或更新后的 compact。
9. 私下 A2A 不写群消息，consult/task_delegate 恢复原 Run；公开 `@` 创建新 child Run，不恢复终态 Run。
10. `RunView` 精确返回目标 Group Run，而不是同 Session、同 root 或同 Agent 的另一个 Run checkpoint；产品同步失败不重跑模型/Tool。

#### 10.2.1 Group terminal public `@` handoff 已落地核对（2026-07-16）

本小节只记录 10.1 第 5 项已经落地的事实，不改变前文产品语义：

| 文档约束 | 已落地事实 | 代码入口 | 回归证据 |
| --- | --- | --- | --- |
| 2.3.2、2.4.1：只接受终态结构化 mention，不解析正文 `@name`，也不新增 handoff Tool | 共享 `finish` 仅在合法 Group Agent Run 中增加可选、去重且有界的 `mention_participant_ids`；非 Group parser 拒绝该字段 | `services/llm/finish.py`、`agent_runtime/model_step_service.py` | `test_group_finish_parser_accepts_only_bounded_stable_participant_ids`、`test_non_group_finish_rejects_group_or_unknown_bypass_fields`、`test_non_group_finish_cannot_bypass_group_handoff_field` |
| 2.4、2.4.1：terminal 前全目标 preflight，失败留在当前 Run repair | 在写 terminal intent 前一次性校验 Group/Session/source、active wakeable Agent、model、预算、Runtime rollout 与 cycle guard；任一目标失败不写公开消息或 child Run | `agent_runtime/group_handoff.py`、`agent_runtime/model_step_service.py` | `test_multi_target_preflight_failure_is_all_or_none_and_repairable`、`test_cycle_limit_fails_preflight_before_terminal`、`test_group_handoff_preflight_failure_repairs_without_finishing` |
| 2.2、2.3.3–2.3.7、5.6–5.8：公开消息、mentions、每目标新 child Run 与 start command 原子落库 | checkpoint 只冻结 delivery intent；稳定 checkpoint 后在调用方同一产品事务中创建一条公开消息、结构化 mentions、每目标独立新 child Run 和 command，不 resume 已终态来源 Run | `agent_runtime/node_executor.py`、`agent_runtime/checkpoint_side_effects.py`、`agent_runtime/delivery.py`、`agent_runtime/group_handoff.py` | `test_group_finish_intent_is_frozen_into_terminal_delivery_request`、`test_completed_group_handoff_preserves_frozen_intent_from_checkpoint`、`test_atomic_apply_creates_public_message_and_one_new_child_per_target`、`test_caller_transaction_rolls_back_every_handoff_write_failure` |
| 2.3.4、2.3.7、5.7：冻结 scope、lineage、目标顺序、触发消息、cutoff 及已有 mode/plan_prompt | apply 只按冻结 participant 顺序重校验，不能改序或重算 lineage/mode/plan_prompt；所有 child 使用同一 message/cutoff，仅 `target_participant_id` 不同；延迟同步按 `max(existing, cutoff)` 维护 Session 时间，不回拨 | `agent_runtime/group_handoff.py` | `test_preflight_freezes_all_targets_scope_lineage_plan_and_cutoff`、`test_frozen_intent_rejects_a_noncanonical_participant_sequence`、`test_apply_revalidation_cannot_reorder_the_frozen_targets`、`test_delayed_apply_does_not_move_the_session_clock_backwards` |
| 5.9–5.10：产品同步幂等且失败可观察 | delivery 先读取稳定回执；同一 intent 重试返回已有 message，不重复创建消息或唤醒；apply 前竞态以明确 failure receipt 暴露，写入阶段异常交给调用方事务回滚与 reconciler 重试 | `agent_runtime/delivery.py` | `test_group_handoff_race_failure_publishes_nothing_and_is_observable`、`test_group_handoff_delivery_retry_does_not_repeat_message_or_child_runs` |
| 3.1、5.3：普通 Group Tool 与 pair-private A2A 不因公开 handoff 改语义 | Group finish schema 组装不删除 `send_message_to_agent`；普通无 mention Group finish 继续走原 delivery | `agent_runtime/model_step_service.py`、`agent_runtime/delivery.py` | `test_group_snapshot_adds_only_current_group_tools_and_platform_rules` 及 `test_agent_runtime_a2a.py` 回归组 |

边界说明：本小节只核对 Group Agent terminal public `@` handoff；Planning v2 的后续落地事实单独见 10.2.2。表中“冻结同一 cutoff”只证明 handoff 正确透传位置，recent messages 与 Group Session Context compact 的严格消费由 10.2.3 单独核对。

#### 10.2.2 Planning v2 已落地核对（2026-07-16）

本小节只记录 10.1 第 4 项已经落地的事实；严格 cutoff 的消费实现与证据单独见 10.2.3：

| 文档约束 | 已落地事实 | 代码入口 | 回归证据 |
| --- | --- | --- | --- |
| 1.1–1.3：自由模式和 Workflow 使用唯一 v2 schema，Runtime 不做 DAG 或业务语义裁判 | Planner 只接受精确的 `version / mode / goal / plan_prompt / entry_steps` 字段；只校验字段、非空、有界、入口唯一且属于冻结候选集合，拒绝 v1、额外字段、重复或越权 Agent | `agent_runtime/planning.py` | `test_plan_validator_accepts_an_entry_subset_without_inventing_a_dag`、`test_plan_validator_rejects_non_v2_or_nonstructural_input` |
| 1.2、1.5：Planning root 使用独立无工具 Prompt，失败仅做有界 repair | Planning model 固定 `tools = None`，只读取原始目标与候选 Agent；初始调用失败后最多 repair 两次，总计最多三次模型调用，仍不合法则明确 failed | `agent_runtime/planning.py` | `test_planning_model_uses_the_pinned_platform_model_without_tools`、`test_invalid_plans_receive_two_repairs_then_fail_the_checkpoint` |
| 1.3 第 8 条、4.6：合法 plan 直接完成并冻结，稳定 checkpoint 后再做产品同步 | 合法 plan 原样规范化保存到 `lifecycle.planning` 后直接 `completed / terminal`；没有 `waiting_agent`，也没有 child terminal resume 路径 | `agent_runtime/planning.py`、`agent_runtime/worker_service.py` | `test_valid_plan_completes_without_waiting_and_freezes_the_exact_v2_plan`、`test_checkpoint_plan_revalidates_the_frozen_candidate_scope`、`test_planning_executor_has_no_child_resume_path`、`test_noncompleted_planning_checkpoint_never_schedules_or_resumes` |
| 1.1–1.3、4.1：只启动入口 Agent，所有入口获得同一不可变计划与原始触发位置 | completed checkpoint 同步只创建 `entry_steps`；非入口候选不创建。每个 child 的 `parent_run_id / root_run_id` 都是 Planning Run，继承相同 `mode + plan_prompt + message/cutoff`，仅当前责任和目标 participant 不同 | `agent_runtime/planning_scheduler.py` | `test_completed_plan_creates_only_entry_children_with_one_immutable_plan` |
| 4.6、5.4：入口同步幂等、全有或全无，失败不重跑 Graph/Planner | 同步在一个产品事务中先校验全部入口再写入；稳定 source execution/idempotency key 使 checkpoint 重放只复用已有事实；入口失效时零创建，后一个写入失败时整批回滚，异常留给产品 reconciler 重试 | `agent_runtime/planning_scheduler.py` | `test_completed_plan_product_retry_is_idempotent`、`test_entry_revalidation_failure_creates_no_partial_child`、`test_later_child_write_failure_rolls_back_the_whole_entry_batch` |
| 1.4、1.5：删除 v1 双协议与重复 Planning 输入 | 运行路径已删除 DAG ready-step 调度、dependency summaries、Planning child completion handler 及 `planning_mode / planning_instruction` 回退；`planning_hint` 只保留 `mode + plan_prompt + current_responsibility` | `agent_runtime/planning_scheduler.py`、`agent_runtime/group_context_builder.py`、`agent_runtime/group_handoff.py`、`agent_runtime/worker_service.py` | `test_group_context_freezes_authoritative_scope_files_and_sender_metadata`、`test_group_prompt_has_one_source_for_trigger_plan_and_responsibility`、`test_component_builder_installs_pinned_agent_and_planning_graphs` |

边界说明：本小节只证明 Planning v2 和入口同步本身；它复制给入口 child 的 `source_id / scheduling_position / context_cutoff` 是否被严格消费，由 10.2.3 的 Context Builder 与 Session Context 回归证明。

#### 10.2.3 Strict Group trigger cutoff 已落地核对（2026-07-16）

本小节只记录 10.1 第 6 项已经落地的事实，不扩大 Group lane、Planning 或 Session Context 的产品语义：

| 文档约束 | 已落地事实 | 代码入口 | 回归证据 |
| --- | --- | --- | --- |
| 4.3.1–4.3.4：新 Group Agent Run 只读取触发位置及以前的公开消息，排队延迟不改变语义来源 | 首次 snapshot 捕获交叉校验 payload `message_id / context_cutoff`、Run `source_id` 和 scheduling `(created_at, id)`，再用权威 Group `ChatMessage` 校验完整位置；pending/recent 都按 `created_at < cutoff OR (created_at = cutoff AND id <= cutoff.id)` 查询 | `agent_runtime/context_builder.py`、`agent_runtime/session_context_service.py`、`agent_runtime/langgraph_driver.py` | `test_missing_or_mismatched_group_cutoff_fails_before_context_read`、`test_group_cutoff_pack_fails_closed_when_trigger_position_mismatches`、`test_group_cutoff_pack_uses_full_position_for_pending_and_recent_messages`、`test_snapshot_factory_passes_immutable_source_and_scheduling_position` |
| 4.3.2、4.3.6：同源 siblings 使用同一 cutoff，后续 Run 使用自己的新 cutoff | 人类单 `@` 和 Planning root payload 都保存权威 cutoff；Planning entry siblings 与公开 handoff siblings 继续透传相同冻结位置。每个后续公开 `@` 产生新的 ChatMessage、位置和 Run | `group_message_service.py`、`agent_runtime/planning_scheduler.py`、`agent_runtime/group_handoff.py` | `test_public_message_and_single_mention_start_share_one_session`、`test_multi_agent_message_creates_one_planning_root_in_the_same_transaction`、`test_queued_siblings_with_one_cutoff_freeze_equal_inputs`、`test_later_group_run_uses_its_own_later_cutoff_data` 及 10.2.1/10.2.2 的 cutoff 透传回归 |
| 4.3.3、4.3.5、4.3.9–4.3.10：不能注入越过 cutoff 的消息、terminal delta 或最新 compact，也不能为历史 Run 改写共享状态 | rolling snapshot 只有在 watermark 不晚于 cutoff，且带时区的 state `updated_at <= cutoff.created_at` 时才可使用；这避免 terminal delta 在不推进 message watermark 时泄漏 cutoff 后结果。其余情况从保留的 cutoff 前原始消息调用既有 `LLMSessionContextCompactor` 临时重建。该 compactor 只返回 candidate，Context Builder 不执行 CAS 或共享写入；失败或 watermark 不一致时 fail closed | `agent_runtime/session_context_service.py`、`agent_runtime/context_builder.py`、`agent_runtime/session_context_compactor.py`、`agent_runtime/worker_service.py` | `test_group_cutoff_rebuilds_when_terminal_delta_updated_state_after_cutoff`、`test_group_cutoff_pack_rebuilds_when_current_compact_is_after_cutoff`、`test_latest_compact_after_cutoff_is_transiently_rebuilt_without_mutation` |
| 4.3.5–4.3.7：已启动 Run 的语义输入不可被 queue、Runtime 状态或 resume 刷新 | 严格 cutoff 只在 start 创建 `RunInputSnapshots` 时读取一次；snapshot 进入 LangGraph checkpoint 后，正常执行、resume 与 checkpoint 重放都只读取 checkpoint copy | `agent_runtime/context_builder.py`、`agent_runtime/langgraph_driver.py` | `test_group_checkpoint_replay_never_refreshes_cutoff_snapshot`、`test_resume_build_reuses_checkpoint_snapshot_without_refreshing_session` |
| 共享边界：Direct/non-Group 语义不随 Group cutoff 改变 | Direct 仍以原生 LangGraph Thread 为短期上下文，不加载 Session compact/recent；其他非 Group Session 继续走既有 current pack。Planning root 不注入可变 Group public history | `agent_runtime/context_builder.py` | `test_direct_chat_does_not_reload_session_compact_or_recent_messages`、`test_capture_new_run_freezes_latest_session_context_and_recent_messages` |

边界说明：这里保证的是同一权威 Message Position 和不越界的公开语义来源；没有新增 cutoff 表、历史 compact 表、第二套 Group Context 框架或迁移。共享 rolling compact 的消息窗口只由后台阈值扫描维护；每个 terminal Run 只把结构化 delta 做确定性 CAS 合并，不调用 Compact LLM，也不推进消息 watermark。严格历史 snapshot 只属于对应 Run checkpoint。

#### 10.2.4 Group Workspace write/delete 对账已落地核对（2026-07-16）

本小节只核对 1.5 第 14 项的 Group Workspace 文件写入/删除，不扩展到 Group Memory、外部发送授权或跨空间复制：

| 文档约束 | 已落地事实 | 代码入口 | 回归证据 |
| --- | --- | --- | --- |
| 每次 Runtime 文件副作用必须有稳定 operation ID | `AgentToolExecution.id` 直接作为 `operation_id` 和 `WorkspaceFileRevision.id`，由现有 revision 主键拦住并发重复 prepare；`group_key` 只保留可观测映射。同一 Tool call 的 prepared revision、最终 revision 和 Tool receipt 共用这一身份，不新增表、依赖或迁移 | `agent_runtime/tool_step_service.py`、`agent_runtime/group_runtime_tools.py`、`workspace_collaboration.py` | `test_group_workspace_write_uses_ledger_id_and_reconciles_without_reexecution`、`test_group_runtime_revision_uses_operation_id_and_prepared_is_hidden_from_history`、`test_concurrent_prepare_reuses_the_revision_primary_key_winner` |
| storage 之前必须留下可对账意图，未显式传版本也不能做无条件覆盖 | 先提交 `prepared_write / prepared_delete` revision，再执行且只执行一次 storage CAS；没有 `expected_version_token` 时也使用准备阶段捕获的当前 version，文件不存在时使用 require-absent | `group_file_service.py`、`workspace_collaboration.py` | `test_runtime_write_cas_uses_captured_version_even_without_model_token`、`test_group_workspace_mutation_prepares_applies_and_finalizes_one_operation` |
| storage 成功而 revision/ledger 未 settle 时只补账，绝不自动重写 | 已提交 revision 直接重建稳定 receipt；prepared write 只有当前内容命中 after hash 才 forward-finalize，prepared delete 只有文件已不存在才 forward-finalize；这些路径不再调用 storage write/delete。其他状态直接落为明确 unknown/conflict，不等待 lease 后重做 | `group_file_service.py`、`agent_runtime/group_runtime_tools.py`、`agent_runtime/tool_step_service.py` | `test_prepared_write_with_after_hash_forward_finalizes_without_rewriting`、`test_prepared_delete_with_missing_file_forward_finalizes_without_redeleting`、`test_prepared_write_third_storage_state_is_unknown_conflict_and_never_rewritten`、`test_group_workspace_ledger_settlement_failure_stays_reconcilable` |
| prepared 不是历史，模型回执必须有界且可稳定重放 | 历史查询排除 prepared 状态；成功回执只含 `operation_id / revision_id / operation / path / content_hash / deleted`，不回显正文。Ledger 已成功时直接复用原 receipt；Ledger 未 settle 时从同一 committed revision 重建相同事实 | `workspace_collaboration.py`、`agent_runtime/group_runtime_tools.py` | `test_committed_revision_rebuilds_stable_receipt_without_storage_access`、`test_group_workspace_write_uses_ledger_id_and_reconciles_without_reexecution` |
| 条件检查与物理 mutation 必须是同一个原子动作 | Local backend 对全部 mutation 使用同一 storage root 的跨进程 `flock`，并以同目录临时文件 + `os.replace` 提交写入；S3/GCS 路径直接使用 Provider `IfMatch / IfNoneMatch`。`404 / NoSuchKey / NotFound` 以及无 error code 的 HTTP 404 按 object-missing 处理；`NoSuchBucket / WrongEndpoint` 等具名非对象错误、权限、超时和 5xx 均 fail closed；409/412 才映射为 conflict | `storage_runtime/local.py`、`storage_runtime/s3.py` | `test_storage_conditional_atomicity.py`、`test_group_workspace_reconciliation.py`；storage/S3/fallback/files/group 联合回归 `52 passed` |
| started receipt 只能由当前 lease owner 推进，后台恢复不得重放 storage | 每个执行/恢复者使用唯一 lease owner；prepare、storage apply、revision reconcile 和 settle 都校验同一 ledger fence。过期 started 先原子 takeover，再只读 revision/storage 对账；active lease 只 defer，不消耗 Command attempt。独立 Product Reconciler 扫描过期 started 和延迟 unknown，late success 只能通过只读 reopen/reconcile 转成功 | `agent_runtime/tool_execution.py`、`agent_runtime/tool_step_service.py`、`agent_runtime/group_runtime_tools.py`、`agent_runtime/product_reconciler.py`、`agent_runtime/command_worker.py` | Group Workspace/Tool Ledger/Runtime 定向回归 `519 passed`，Group 专项 `32 passed` |

边界说明：这里没有改变人类 Group 文件 API、普通 Agent Workspace、Group Memory 或其他工具的写入语义。S3/GCS 真实 Provider 在线联调和数据库断连后对象晚成功的故障注入尚未完成；当前正确性实现会在 storage I/O 期间持有对应 Tool Ledger 行锁，优先保证 fence 与单次副作用，后续需监控数据库连接池和长尾延迟。滚动发布时必须先排空不理解新 fence 的旧 worker。10.3 第 5 项所列长期产物所有权与 Group Memory 治理仍是后续产品决定。

#### 10.2.5 Group 跨空间外部动作的本版 fail-closed 边界（2026-07-16）

本版没有伪造一套尚不存在的人类审批流。`send_channel_message / send_platform_message / send_feishu_message` 先归一为 `external_message`，`send_channel_file / send_file_to_agent` 归一为 `external_file`；Group Agent Run 对这些动作先创建 Tool Ledger reservation，再在任何 Provider 调用之前以确定性 `failed / group_cross_space_confirmation_required` 结算。模型参数里自行声称“已经确认”不会改变该结果。

这条后端门禁只阻止 Group 内容通过上述五个 builtin alias 流向私信、外部渠道或其他 Agent Workspace，不删除普通 Group Agent 的自身 Workspace、Memory、Skills 或普通工具，也不阻止全局私下 A2A `send_message_to_agent`。非 Group Run 继续使用原有发送路径。对应回归覆盖全部五个 alias 的零 Provider dispatch、非 Group 发送不受影响，以及 Group 私下 A2A 不受影响。它不分析 `finish` 公开回复的内容来源，也不覆盖未来新增的 MCP/动态 alias；私有内容复制进 Group 的 provenance/DLP 仍属于后续结构化 grant 设计，不能误报为本版已确定性拦截。

完整能力仍是后续产品事项：只有在实现由真实人类触发的结构化 grant、内容预览、权限校验与审计记录后，Group 跨空间动作才能从 fail-closed 改为可执行；不能把模型生成的 approval ID、Prompt 声明或 unknown-outcome 重试确认当作授权。

#### 10.2.6 Group 低信任 Context 已退出 system role（2026-07-16）

Prompt 装配现在按信任边界分成三部分：`Identity + Soul + Base Prompt + Capability/Group Policy` 以及可信 `runtime_instruction` 保留在 system role；Agent 的有界 Memory/Company/Relationships/当前时间等 dynamic data 与 allowlist 后的 Runtime Context 进入独立 user-role reference-data message；规范化历史和唯一 Current Input 随后按原顺序进入 messages，当前输入仍只出现一次。

因此 Group announcement、Group Memory、Workspace 索引、成员资料、`plan_prompt` 和 Group Context metadata 即使包含类似指令的文字，也只拥有 user-data 权限，不再因为 `initial_input.group_context` 被 JSON 序列化就进入 `system.dynamic_content`。触发消息正文继续从 Group Context copy 移除，只由权威 current user message 提供；Group Capability Policy 和 unknown-outcome 的公开确认规则仍是平台可信边界，不与低信任正文混装。

定向回归同时覆盖 Trigger payload、Heartbeat context、native A2A metadata、Task directive、Group Planning hint、AgentBay screenshot 下一轮回注和 Provider 消息序列；Prompt/Context 相关联合回归 `81 passed`，Compact/finish 相邻回归 `39 passed`，AgentBay A0/A1/Model Step 合跑 `134 passed`。

#### 10.2.7 Group handoff 使用决策与 ACK 后来源校验已闭环（2026-07-16）

Group Agent 的常驻可信 Policy 现在明确区分三种动作：只需要其他 Agent 私下提供事实或建议、且当前 Agent 仍负责最终公开答案时使用 A2A；另一位 Agent 必须公开继续或接管下一责任，尤其用户明确要求“让某 Agent 继续”时，当前 Agent 完成本轮责任后做公开 handoff；任务已完成且无人需要继续时正常 finish，不携带 mention。

公开 handoff 仍不新增平行 Tool。模型必须先用 `group_query_members` 取得稳定 participant ID，再在同一个、唯一的 `finish` 调用中把 ID 放入 `mention_participant_ids`。正文里的 `@名字` 永不参与路由。`finish` Tool description 与 Group Policy 使用同一条约束，避免模型先写正文再遗漏结构化字段。

Group Run 启动时的普通 ACK 会先把来源 Run 的产品 `delivery_status` 从 `pending` 推进为 `delivered`；该字段不是 Runtime 生命周期。handoff preflight/apply 因此接受仍在执行且 delivery 为 `pending / delivered` 的合法来源，并继续拒绝 `failed / not_required`。这修复了“ACK 已公开后终态 handoff 被误判为非法来源”的稳定复现。

对应回归覆盖：ACK 后 handoff 可通过、失败/不需要投递的来源 fail closed、Group Prompt 的 A2A/handoff/normal finish 决策、同一 finish call 的稳定 ID 要求，以及文本 `@name` 不触发隐式路由。

#### 10.2.8 Group Session Context 终态合并与阈值 Compact 已拆开（2026-07-16）

每个 Group Run 进入 terminal 后不再无条件调用 Compact LLM。终态 handler 只在产品事务内把结构化 `SessionContextDelta` 做确定性 CAS 合并：requirements、decisions、evidence/workspace refs 按稳定 JSON identity 去重；resolved open item 按精确结构删除；result summary 追加到现有摘要；同一 checkpoint receipt 与 Context 更新原子落库。冲突时重新读取胜出版本再合并，同一 checkpoint 重放为 no-op。

消息窗口压缩仍由原有 `SessionContextCompactionScanner -> SessionContextMessageCompactionService -> LLMSessionContextCompactor` 负责。只有新增消息数量或有效预算达到既定阈值时才调用独立 Compact 模型并推进 `covered_through_message_id`；短会话完全可以不调用 Compact LLM。Compact 模型失败保留旧 Context 和原始 ChatMessage，后续扫描重试，不阻塞当前 Run 的 terminal、公开 delivery 或 handoff。

这两个通道不能混用：terminal delta 没有证明任何公开消息已被压缩，因此不得推进 watermark；后台消息 Compact 不负责重放 terminal Graph 或工具。对应回归覆盖终态去重/resolve/CAS 冲突/receipt、水位不前进、后台阈值以下不压缩、达到阈值后压缩以及失败保留旧 Context。

#### 10.2.9 Group 实时消息与断线回补已闭环（2026-07-16）

群聊使用 `REST send/history + 一个 Group 一条 WebSocket + after cursor 回补 + 仅故障时轮询`。`GET .../messages` 的 `before / after` 互斥并统一使用 `(created_at, id)`；`after` 返回严格更新的消息且升序。`WS /ws/group/{group_id}` 使用 JWT、active tenant membership 和 30 秒权限复验，一个事件携带 `session_id` 与完整 `GroupMessageOut`。

人类消息、规划失败系统消息、ACK、waiting/terminal 以及带 mention 的 handoff 都只在产品事务提交后发布 `message.created`。推送是至少一次通知，失败不回滚已提交事实；前端按 message ID 去重并以 `after` 追回空窗。健康 WebSocket 建立后立即清理 4 秒轮询 timer，避免页面持续请求消息接口。详细契约和回归表见 `docs/group-chat/frontend-realtime-contract.md`。

#### 10.2.10 Group 交互边界回归修复（2026-07-16）

本轮浏览器 E2E 暴露的交互缺陷按既有产品语义做最小修复，没有增加第二套 Runtime 或文件版本协议：

1. 新建 Group Session 允许提交空标题，由后端生成默认标题；创建 Group 仍要求非空名称。`PromptModal` 只通过显式 `allowEmpty` 开放这一处行为。
2. URL 中的 Group 不在当前用户可见列表时，前端必须等待本次页面挂载后重新取得的权威 Group 列表再返回 `/groups`；React Query 旧缓存只能用于渲染，不能授权 Sessions、Members、Messages 或群树 Session 汇总请求。Toast Context 保持稳定引用，错误提示本身不再触发请求 effect 重跑。
3. Group Workspace 列表返回的 `version_token` 必须来自 storage `get_version` 的同一 canonical token，不能直接使用 Local/S3 list metadata 的近似 token。用户可以把列表返回的 token 原样用于条件删除；真正发生并发修改时仍返回 conflict。
4. 删除全局 Agent 时，Direct/A2A Session 被软删除并解除 Agent 外键，`ChatMessage.agent_id` 被置空；Agent Participant 作为历史显示 tombstone 保留，活跃 Group membership 置为 removed。因此 Agent 不再出现在群成员或邀请候选中，但既有群消息及发送者名称仍可读取。
5. Group 发送接口返回的每个精确 `run_id` 都可通过 Group/Session scoped Run State 接口读取，并通过显式 cancel 接口写入既有 `AgentRunCommand(cancel)`。前端只在状态仍可取消时显示停止按钮；一次操作只取消这些目标 Run，不级联 root、公开 handoff child 或私下 A2A child。

对应回归覆盖空标题的显式开放、未授权 route 的请求门禁与稳定 Toast、list-token 直接删除、Agent 删除时历史保留语义，以及 Group Run scope/cancel Command。未受这些改动影响的既有群消息、Planning、handoff 与 A2A 用例不重复执行整套浏览器回归。

### 10.3 仍待后续产品决定

以下事项不阻塞上述正确性修复：

1. Group lane 最终继续按 `tenant + agent` 全局串行，还是缩小到 Group/Session。
2. 人类新 `@` 与 `enforced` Workflow 后续步骤谁优先，是否允许穿插。
3. 群消息是否增加 `reply_to_message_id / source_run_id` 的显式 UI 关联。
4. Workflow 偏离、弱模型不交接和无界循环的进一步产品策略；V1 先使用现有深度、Run 数、token、时间与成本预算兜底。
5. Workspace 产物所有权，以及 Group Memory 的长期治理；并发写入与版本冲突的本版正确性边界已经落在 10.2.4。
6. 是否提供 Workflow/root 级观察页面；即使提供，也只能聚合既有事实，不能变成新的执行真值或语义完成裁判。
7. Group 跨空间消息/文件的结构化人类 grant、预览、权限与审计流程；在该流程落地前保持 10.2.5 的后端 fail-closed。
