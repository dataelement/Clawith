# 本地模型兼容性问题记录

> 记录日期：2026-07-14
>
> 适用版本：当前开发版本
>
> 状态：已实现并完成离线回归；真实 Ollama Chat 模型联调仍需在有可用模型的环境补测

## 背景

Clawith 通过 OpenAI-compatible 接口接入 Ollama、vLLM、SGLang 和 Custom Provider。当前本地模型路径存在两个相互独立的问题：

1. onboarding 请求可能包含多条 `system` message，部分本地模型会直接拒绝请求；
2. 模型没有按 Clawith 约定调用 `finish` 时，Agent 会持续重试，最终报 `Too many tool call rounds`。

第一个问题发生在请求进入模型时，第二个问题发生在模型已经返回内容之后。两者不能通过同一个轮数配置解决。

---

## 问题一：Onboarding 请求出现多条 system message

### 现象

Custom Agent 或 Template Agent 首次进入 onboarding 时，本地模型可能立即返回 HTTP 400。常见错误信息为：

```text
System message must be at the beginning
```

问题请求的形状类似：

```json
[
  { "role": "system", "content": "Agent 主提示词" },
  { "role": "system", "content": "Onboarding 指令" },
  { "role": "user", "content": "用户消息" }
]
```

部分云端模型或兼容层会容忍这种输入，但部分 Ollama 模型模板及严格的 OpenAI-compatible 实现只接受一条、且位于消息数组首位的 `system` message。

### 根因

旧调用路径中，LLM 层已经生成 Agent 主 `system` message，WebSocket onboarding 逻辑又把 onboarding 内容作为另一条 `role=system` 的消息插入，最终形成多条 system message。

当前 Runtime 分支已经把 onboarding 内容放入 `runtime_instruction`，并在上下文构造阶段合入首条 system 内容。这是中间层修复，但目前仍缺少最终 Provider payload 的统一约束，旧入口、fallback 或后续改动仍可能重新产生该问题。

### 本版本修复要求

1. 所有 Agent Runtime 请求最终只能包含一条 `system` message，并且必须位于 `messages[0]`。
2. Agent 主提示词、动态提示词、onboarding 指令和 Runtime 指令必须按固定顺序合并到同一条 system message 中。
3. Session 历史中的旧 system 记录不能直接透传给 Provider。
4. streaming、non-streaming、primary、fallback 以及不同 OpenAI-compatible Provider 必须使用相同的消息规范化逻辑。
5. Provider 调用前必须校验最终 payload；发现多条 system 或 system 不在首位时，返回明确的请求形状错误，不能依赖模型自行兼容。

### 完成标准

- Custom Agent 和 Template Agent 的 onboarding 均能在 Ollama 上正常开始。
- 捕获到的最终 Provider JSON 只有一条 `role=system`，且位于第一条。
- 普通聊天、onboarding、模型 fallback 和包含旧历史数据的会话都满足相同约束。

---

## 问题二：本地模型出现 Too many tool call rounds

### 现象

本地模型能够正常返回普通文本，但没有按 Clawith 的 Agent 协议调用 `finish(content=...)`。Clawith 随后不断追加 finish 提醒并再次请求模型，直到耗尽轮数：

```text
Too many tool call rounds
```

在新的 Runtime 路径中，同类问题可能表现为：

```text
model_step_limit_reached
```

### 根因

Clawith 当前把 `finish` tool call 作为 Agent 完成一次 Run 的正式终止信号。但 Ollama、vLLM、SGLang 和 Custom Provider 的工具能力主要按 Provider 类型静态判断，没有验证具体模型是否真的能够输出符合 OpenAI 协议的 `tool_calls`。

因此会出现以下错配：

- 模型连接测试能够返回普通文本 `ok`；
- 系统据此认为模型可用于 Agent；
- 实际运行时模型只返回文本，不调用 `finish`；
- Runtime 把相同协议提醒重复发送，直到轮数或步骤上限。

调大 `max_tool_rounds` 或 `max_model_steps` 只会延迟报错，不能解决协议不兼容。

### 本版本修复要求

1. 模型能力必须按具体模型记录，不能只按 Provider 名称推断。
2. 模型测试除普通文本连通性外，还必须发送最小 `finish` tool，并确认模型返回合法的 OpenAI `tool_calls`。
3. 已确认支持原生 tool calling 的模型才能进入需要工具的 Agent Runtime。
4. 不支持 tool calling 的模型只能进入明确的纯聊天模式；如果 Agent 必须使用工具，应在 Run 开始前直接报告模型能力不支持。
5. 对声明支持工具但只返回普通文本的模型，协议修复必须有界；最多提醒一次，仍不符合协议就返回明确错误。
6. 轮数上限只用于限制合法的多步工具任务，不能作为同一种协议错误的重试预算。
7. 管理后台需要分别展示“连接成功”和“原生工具调用可用”，不能把普通文本响应视为完整兼容。

### 完成标准

- 支持工具调用的 Ollama 模型可以通过 `finish(content=...)` 正常结束 Run。
- 不支持工具调用的模型能够被识别，并进入纯聊天模式或在工具 Run 开始前明确拒绝。
- 模型持续返回普通文本或非法 tool call 时，不会重复运行几十轮。
- 用户看到的是具体的模型能力或协议错误，而不是统一的轮数耗尽错误。

---

## 两个问题的边界

| 问题 | 失败阶段 | 典型表现 | 修复重点 |
|-|-|-|-|
| Onboarding 多 system | Provider 处理请求之前或请求解析阶段 | HTTP 400、system ordering 错误 | 统一最终消息形状，保证唯一首条 system |
| finish/tool call 不兼容 | Provider 已返回模型内容之后 | 重复请求，最终 rounds/steps 耗尽 | 真实能力探测、正确路由和有界协议失败 |

## 本版本结论

这两个问题已按当前版本边界完成修复。以下做法不能视为修复：

- 仅调大 `max_tool_rounds` 或 `max_model_steps`；
- 仅让模型连接测试返回普通文本；
- 只在 onboarding 中间对象上合并内容，但不约束最终 Provider payload；
- 依赖某个模型或 Provider 对多 system message 的宽松兼容。

## 已实现方案

### 唯一 system Provider 边界

1. 在最终 LLM Provider 请求边界统一规范化消息，而不是只修某个 onboarding 入口。
2. 第一条 system 保留为静态、可缓存前缀；其动态内容以及后续 onboarding、Runtime 和历史 system 内容按出现顺序并入同一条动态尾部。
3. 非 system 历史消息保持原顺序。
4. OpenAI-compatible、OpenAI Responses、Gemini、Anthropic，以及 Ollama、vLLM、SGLang、Custom Provider 都经过同一约束。
5. OpenAI 风格的最终 payload 若仍包含多条 system，或 system 不在第一条，会以明确的 `LLMRequestShapeError` 失败，不再把非法形状交给模型碰运气。

### 具体模型的原生工具能力

1. `llm_models` 记录具体模型配置的三态能力：`True`（已支持）、`False`（已确认不支持）、`NULL`（未知或探测失败）。
2. 管理后台模型测试分两步显示：普通文本连通性、唯一合法 `finish` tool call。成功返回普通文本但不调用合法 `finish` 会记录为不支持；超时、网络错误或 Provider 异常只记录为未知，不能误判为不支持。
3. 只有已保存且测试期间配置指纹未变化的模型才会持久化探测结果。provider、model、base URL 或 API key 变化会清除旧结果，要求重新测试。
4. 新建表单中的草稿测试不持久化；保存后列表显示“未验证”，需在编辑态对已保存配置再次测试。
5. 为兼容现有已在线使用的云模型，迁移只把已存在的内置云 Provider 记录标记为 `builtin_registry`；Ollama、vLLM、SGLang 和 Custom 始终保持未知，不能按 Provider 名称假定具体模型支持工具。
6. Agent Runtime 在创建持久化 Run 前拒绝未知或不支持的模型，model-step 和 legacy tool loop 继续保留防御性门禁；本版本不新增独立的纯聊天产品路径。
7. `missing_finish`、非法 `finish`、非法 `wait` 和非法 tool call 按稳定错误码分别最多修复一次；同类错误再次出现时立即返回明确的 protocol violation。Group handoff 等业务约束修复不计入该预算。

## 回归与剩余边界

- 后端完整回归：`1824 passed`。
- 前端测试：`12 passed`；生产构建通过。
- Provider 请求形状、模型能力探测、Runtime/legacy 门禁、配置失效和有界协议修复均有定向测试。
- 迁移仍合并在相对主分支的单个统一迁移中，没有追加零散迁移脚本。
- 已使用临时空 PostgreSQL 16 实例执行完整 `alembic upgrade head`，确认会自然到达 `unify_runtime_group_schema`，且当前 64 张 ORM 表全部存在；不使用 `stamp`。
- 统一迁移同时把 `gateway_messages`、`notifications`、`tenant_settings`、`trigger_executions` 四张历史上仅依赖 `create_all` 的表纳入 Alembic。缺表时按当前 ORM 创建，完整旧表保持不动，已知的 Gateway `conversation_id` 缺列和 Notification 016 旧形态只做无损增量修复，未知残缺结构仍在任何 DDL 前 fail closed；降级不会删除这些主分支代码已经依赖的历史基线数据。
- 当前开发机只有 Ollama embedding 模型缓存，没有可执行 Agent tool calling 的 Chat 模型，因此没有伪造“真实 Ollama 已联调”的结论。线上重建空库后也需要先配置并验证一个真实模型，才能执行完整 Agent 模型端到端回归。
