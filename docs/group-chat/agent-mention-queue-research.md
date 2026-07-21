# Agent @ 唤醒与异步队列调研

本文用于讨论 Clawith 群聊 v1 中，Agent 被 @ 后如何处理「聊天回复」和「长任务执行」之间的关系。本文不是最终技术设计，具体建表、状态机和并发策略应进入 `technical-design.md`。

> 终版方案更新：LangGraph checkpoint 是 Run 执行生命周期唯一事实源；`agent_runs` 只作为 Run Registry、交付事实和可重建查询投影，`agent_run_commands` 可靠承载 start / resume / cancel。下文的状态枚举和待讨论项仅保留为历史调研，不作为实现契约。

## 1. 问题背景

群聊里会出现一种高频场景：

1. 用户或 Agent 在群 session 中 @ 某个 Agent。
2. 被 @ 的 Agent 需要在当前群 session 中回复。
3. 如果这个 Agent 正在执行长任务，又收到新的 @，需要决定新请求是立即处理、排队、拒绝，还是只给出状态提示。

这里的核心问题不是「群消息怎么存」，而是「消息流」和「任务执行」是否是同一个东西。

## 2. 调研结论

更成熟、也更容易落地的做法是：

1. 群 session 只承载用户可见的消息流。
2. Agent 被 @ 后，可以先在群 session 中给出可见响应。
3. 需要长时间执行的工作，进入独立的后台 run / job。
4. 后台任务的状态变化，可以按规则写回群 session。
5. 一个 Agent 忙碌时再次被 @，新请求不应该阻塞群聊本身，而应该进入等待处理、并发处理、取消/替换或提示等待等调度策略。

也就是说：聊天是聊天，工作是工作。群 session 负责协作语境，Agent run 负责执行生命周期。

## 3. 开源框架观察

### 3.1 AutoGen

AutoGen 的 group chat 更关注多 Agent 对话编排，例如共享上下文、选择下一个发言者、终止条件等。它能说明「多 Agent 如何围绕同一段 conversation 协作」，但不直接解决生产系统里一个 Agent 忙碌时的排队、并发、取消和资源锁问题。

参考：

- https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/selector-group-chat.html

### 3.2 OpenAI Agents SDK

OpenAI Agents SDK 的 sessions 主要用于保存和恢复对话历史，帮助 Agent 在后续轮次中获得上下文。它解决的是「上下文如何延续」，不是「后台任务如何排队和调度」。

参考：

- https://openai.github.io/openai-agents-python/sessions/

### 3.3 LangGraph

LangGraph 更强调状态图、checkpoint、恢复和长流程执行。它适合表达 Agent 工作流的状态和可恢复执行，但排队、并发度、资源隔离仍然需要产品和工程侧明确策略。

参考：

- https://docs.langchain.com/oss/python/langgraph/persistence

### 3.4 CrewAI

CrewAI 把协作组织成 crew、agent、task、process 等结构，适合描述多 Agent 任务协作。它的启发是：Agent 协作中的任务执行可以被建模成独立对象，而不是直接等同于聊天消息。

参考：

- https://docs.crewai.com/en/concepts/crews

### 3.5 Celery / Temporal

Celery 和 Temporal 属于更通用的后台任务或工作流基础设施。它们的共同启发是：长任务应该有独立的队列、状态、重试、超时、取消和可观测性，而不是绑死在聊天消息表里。

参考：

- https://docs.celeryq.dev/en/stable/getting-started/introduction.html
- https://docs.temporal.io/task-queue

## 4. 对 Clawith 的产品建议

v1 产品层可以先定一个简单规则：

1. Agent 被 @ 后，需要在群 session 中产生可见响应。
2. 如果只是问答或短操作，可以直接回复。
3. 如果需要长时间执行，应异步执行，不阻塞群 session 中其他人继续聊天。
4. Agent 正在执行长任务时再次被 @，新请求进入等待处理或按调度规则处理。
5. 具体是串行、并发、取消旧任务、插队，还是提示用户等待，不放在 PRD 的核心概念里定死。

这能避免把群聊 v1 设计成复杂任务系统，同时保留后续扩展 Agent run、任务状态和工作流引擎的空间。

## 5. 对技术设计的启发

技术设计中可以把以下对象拆开：

1. 群消息：用户可见的消息流。
2. Agent run：一次被 @ 后产生的执行实例。
3. Run 状态：queued、running、succeeded、failed、cancelled 等。
4. Run 输出：可以选择写回群 session 的结果消息。
5. 内部日志：thinking、tool call、debug log、trace，不一定都是群消息。

需要继续讨论的技术问题：

1. Agent run 是否需要独立表。
2. 一个 @ 多个 Agent 时，是多个 run 并行，还是按顺序调度。
3. 同一个 Agent 同时收到多个 @ 时，默认串行还是允许并发。
4. 同一个群工作区上的文件写入是否需要锁。
5. 长任务状态如何在群 session 中展示，避免刷屏。

## 6. 暂定原则

当前更推荐的方向是：

1. 群 session 不承担任务队列职责。
2. 群消息不直接等同于 Agent 执行日志。
3. @ Agent 触发一次 Agent run。
4. Agent run 可以异步执行。
5. 群 session 只接收必要的可见回复和最终结果。
