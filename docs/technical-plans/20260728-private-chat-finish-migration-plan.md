# Finish 协议迁移方案：私信自然结束与群聊 at

状态：已按方案实施并通过本地回归，待最终审查与合并。

> 本文同时记录私信和群聊的完成协议。私信使用自然停止；群聊在此基础上使用独立的 `at` Tool 表达结构化 Agent mention。

## 第一部分：私信自然结束方案

决策：私信与主流 Agent Loop 保持一致，不再要求模型调用带完整正文的 `finish(content=...)`。最终回答使用普通 Assistant content，Runtime 根据 Provider 的原生停止原因和是否存在 Tool Call 判断本轮是否完成。不要改成 `<FINISH>` 等正文结束标记；文本标记仍可能被遗漏、重复、截断或与用户内容冲突。

私信阶段先独立落地，群聊现有结构化 mention、`group_handoff`、child Run 和同 Session 公开回复在第一阶段保持不变。

### 目标执行语义

1. 响应包含 Tool Call：执行工具、写入 Tool Result，并继续模型循环；正文不能绕过仍待执行的工具直接完成 Run。
2. 响应不包含 Tool Call，停止原因为自然结束且正文非空：把普通 Assistant content 作为内部完成候选，继续走现有 verify、finalize、checkpoint 和投递链路。
3. 停止原因为输出长度上限：视为截断，不得把已有半段正文当作完整答案发布；进行一次有界的“重新生成完整答案”修复，重复截断后以明确的 `model_incomplete_output` 失败。
4. 停止原因为安全过滤、拒绝或未知异常：进入对应的结构化非成功结果，不得伪装成已验证完成。
5. 自然停止但正文为空：进行一次有界空响应修复；重复为空后失败，不再提示模型调用 `finish`。

### 停止原因归一化

当前 `LLMResponse.finish_reason` 已在 Provider Client 层存在，但 `backend/app/services/llm/single_step.py` 的 `LLMCompletionStep` 没有该字段，`complete_llm_once()` 返回时会丢失停止原因。第一步应增加并透传规范化的 `finish_reason`：

| Provider 原始值 | Runtime 规范值 | 私信处理 |
| --- | --- | --- |
| `stop`、`end_turn`、`stop_sequence` | `stop` | 无 Tool Call且正文非空时进入验证 |
| `tool_calls`、`tool_use`，或响应实际包含 Tool Call | `tool_calls` | 执行工具并继续 |
| `length`、`max_tokens` | `length` | 截断修复，不得投递 |
| `content_filter`、`safety`、`recitation` | `content_filter` | 结构化非成功结果 |
| `refusal` | `refusal` | 结构化拒绝结果 |
| 未识别值 | `unknown` | 不得直接判定完成 |

兼容期可以允许旧 OpenAI-compatible 模型的“`finish_reason=None`、无 Tool Call、正文非空”按自然结束处理并记录诊断日志，避免本地模型立即回归；显式的 `length`、过滤或拒绝不能进入该兼容分支。

### 代码改动范围

1. `backend/app/services/llm/single_step.py`
   - 为 `LLMCompletionStep` 增加规范化的 `finish_reason`。
   - 从 Provider `LLMResponse` 透传该字段，Tool Call 存在时优先归一为 `tool_calls`。
2. `backend/app/services/agent_runtime/model_step_service.py`
   - 私信工具集合移除并过滤模型可见的 `finish`，停止通过 `_with_runtime_tools()` 为私信强制注入它。
   - `_parse_step()` 按停止原因区分自然完成、工具执行、截断、过滤、拒绝和空响应。
   - 保留内部 `ModelStepResult(intent="finish")`；它只是 Runtime 状态名，不再代表模型必须调用同名 Tool。
3. `backend/app/services/agent_runtime/node_executor.py`
   - 继续复用现有 `verifying -> completed`、`final_answer`、verification 和 finalization 主链。
   - 把私信的 `missing_finish` / `FINISH_PROTOCOL_REMINDER` 语义改为空响应或不完整输出修复，错误信息不再声称模型必须调用 `finish`。
4. `backend/app/services/llm/caller.py`
   - 旧调用入口同步接受自然停止的普通正文，删除私信的 `FINISH_PROTOCOL_REMINDER` 循环。
   - 发送 Provider 请求前过滤 `finish`；`skip_tools=True` 时发送空工具集合。
   - 最终正文确认完成后再交给用户输出回调，避免中间工具轮的普通文字被误投递为最终答案。
5. `backend/app/api/enterprise.py`
   - 模型工具调用能力探针不再要求 `finish(content="ok")`，改用无副作用的 `capability_probe(value="ok")`。
   - 探针只判断原生工具调用、工具名和参数 JSON 是否正确，不再把 Clawith 私有收尾协议当作通用工具能力。
6. `backend/app/services/agent_tools.py` 与 builtin 定义
   - 当前实际数据库已经确认不存在 `finish` Tool row，因此不需要数据库清理或数据迁移。
   - 第一阶段直接删除 `FINISH_TOOL_SEED` 及 `SYNC_IS_DEFAULT_TOOL_NAMES` 中的 `finish`，防止后续 bootstrap Seeder 创建该 row。
   - 暂时保留旧 parser 和 `execute_tool("finish")` no-op，仅用于部署切换时恢复旧 checkpoint；这项兼容不依赖数据库 Tool row。
   - 稳定一个版本并确认没有旧调用后，再单独删除遗留 parser、no-op executor、Prompt 和旧协议测试。

### 回归测试与验收标准

1. 私信模型请求的 Tool Schema 不再包含 `finish`。
2. `finish_reason=stop`、无 Tool Call、正文非空时，一次模型响应即可进入验证和完成，不产生 `FINISH_PROTOCOL_REMINDER`。
3. 长最终回答始终保存在普通 Assistant content 中，不进入任何 Tool arguments JSON。
4. `finish_reason=length` 即使带非空正文也不得完成或投递；一次有界重生成后仍截断则结构化失败。
5. `content_filter`、`refusal`、未知停止原因和重复空响应不得被误记为成功 Run。
6. 普通应用 Tool Call 仍按原顺序执行并继续模型循环；同时存在正文时也不能提前完成。
7. `skip_tools=True` 的私信可以在没有任何 Tool Schema 的情况下自然完成。
8. OpenAI、Anthropic、Gemini以及缺少停止原因的 OpenAI-compatible 模型均有停止原因归一化回归。
9. 旧的合法 `finish` 响应保留一条过渡兼容测试，但不再作为正常私信成功路径或工具能力标准。
10. 现有群聊结构化 mention、预检、checkpoint handoff、公开投递和 child Run 回归在第一阶段必须保持不变。

### 实施顺序

1. 先增加 `finish_reason` 透传和停止原因单元测试。
2. 再移除私信模型可见 `finish`，启用自然正文完成。
3. 同步旧 caller 和 Enterprise capability probe。
4. 运行私信 Runtime 定向回归、LLM Client 测试以及后端全量测试和静态检查。
5. 私信阶段稳定后，再实施本文第二部分的群聊 `at` 协议；两个阶段保持独立提交和验证。

---

## 第二部分：群聊 at 协议

### 决策

群聊把“说什么”“@谁”“是否自然结束”和“最终路由副作用”拆成四个概念：

| 概念 | 表达方式 | 职责 |
| --- | --- | --- |
| 最终公开回复 | 普通 Assistant content | 只包含群成员应该看到的业务正文 |
| 结构化 Agent mention | `at` Tool | 只设置下一条最终回复需要唤醒的 Agent |
| 模型结束 | Provider `finish_reason` | 区分自然停止、Tool Call、截断和异常停止 |
| child Run 路由 | Runtime `group_handoff` | 预检通过后冻结并在投递事务中执行 |

模型不再调用 `finish`，也不再把公开正文放入 Tool arguments。

### 模型可见的 at Tool

`at` 只在 Group Agent Run 中注入：

```json
{
  "type": "function",
  "function": {
    "name": "at",
    "description": "Set the complete list of Group Agents that must be visibly mentioned and woken by the next final public reply. This only stages routing and does not send a message or finish the Run.",
    "parameters": {
      "type": "object",
      "properties": {
        "participant_ids": {
          "type": "array",
          "items": {
            "type": "string",
            "format": "uuid"
          },
          "maxItems": 100,
          "uniqueItems": true
        }
      },
      "required": ["participant_ids"],
      "additionalProperties": false
    }
  }
}
```

调用约定：

1. `participant_ids` 是下一条最终公开回复需要唤醒的完整 Agent 集合，不是增量列表。
2. 模型必须先通过 `group_query_members` 获取稳定 participant UUID，不能根据显示名猜测 ID。
3. 后一次成功的 `at` 调用覆盖此前暂存集合。
4. `at([])` 清除当前暂存集合。
5. `at` 可以和普通 Tool Call 共存，不要求成为本轮唯一 Tool Call。
6. `at` 不包含公开正文，不表示 Run 已完成，也不立即创建 child Run。

### 标准两步 Tool Loop

使用 Provider 通用的 Tool Call → Tool Result → Final Assistant content 流程：

```text
group_query_members
    ↓
取得 participant_id
    ↓
at(participant_ids)
    ↓
Runtime 暂存目标，不发送消息、不创建 child Run
    ↓
返回 Tool Result
    ↓
模型输出普通最终 Assistant content
    ↓
finish_reason=stop
    ↓
正文与结构化目标双向校验
    ↓
preflight → verify → finalize
    ↓
原子发布公开消息并创建 child Run
```

如果 Provider 在 `at` Tool Call响应中同时返回 Assistant content，该 content 只作为工具轮草稿进入历史，不能直接作为公开最终回复。Runtime 仍然返回 Tool Result，并等待下一轮自然最终正文。

### 分层结构

#### 1. 模型输出层

模型只输出：

- 普通 Assistant content；
- 真实业务 Tool Call；
- Group Run 中可选的 `at` Tool Call。

模型不再看到 `finish`、`finish.content`、`FINISH_PROTOCOL_REMINDER` 或文本结束标记。

#### 2. Provider 响应归一化层

继续使用第一部分定义的 `LLMCompletionStep`：

```python
LLMCompletionStep(
    content: str | None,
    tool_calls: tuple[dict, ...],
    finish_reason: str | None,
    reasoning_content: str | None,
    retry_instruction: str | None,
    usage: TokenUsage,
)
```

该层只统一 Provider 差异，不执行群聊业务。

#### 3. Runtime 响应解释层

现有 `model_step_service._parse_step()` 继续承担响应解释，但删除模型可见 `finish` 的特殊分支：

```python
if step.tool_calls:
    return tool_calls_route()

if step.finish_reason == "stop" and step.content:
    return final_candidate(step.content)

if step.finish_reason == "length":
    return incomplete_output_repair()

return abnormal_completion()
```

这不是新增 Runtime 层，而是收敛现有职责：Tool Call进入 Tool Node，自然正文进入 Verify Node，截断和异常停止不得误判完成。

为了降低第一阶段改动风险，内部 `intent="finish"` 和 `finish_content` 可以暂时作为兼容命名保留；它们只代表内部最终候选，不再对应模型 Tool。后续再机械重命名为 `intent="final"` 和 `final_content`。

#### 4. at 暂存状态层

`at` 进入标准 Tool Node，但只更新 checkpoint lifecycle：

```json
{
  "pending_group_at": {
    "participant_ids": [
      "participant-uuid"
    ],
    "tool_call_id": "call_xxx",
    "staged_at_model_step": 4
  }
}
```

Runtime 同一次状态更新写入：

- `pending_group_at`；
- 对应的 `role=tool` Tool Result。

Tool Result建议保持简短：

```json
{
  "status": "staged",
  "participant_count": 1
}
```

`pending_group_at` 的生命周期：

- `at` 成功后写入 checkpoint；
- 后续模型和工具轮次继续保留；
- 新 `at` 调用覆盖，`at([])` 清除；
- 最终预检通过并冻结正式 handoff 后清除；
- Run 失败或取消时丢弃；
- 不直接写入业务数据库表。

#### 5. 最终正文与路由预检层

模型自然停止并输出最终正文时，Runtime 同时读取：

```text
final_content
pending_group_at
```

执行双向一致性校验：

1. 正文没有 Agent `@名字`，也没有 `pending_group_at`：普通群聊回复。
2. 正文包含 Agent `@名字`，但没有匹配的结构化 ID：不发布，要求模型查询成员并调用 `at`。
3. `pending_group_at` 包含目标，但正文没有相应可见 `@名字`：不发布，防止后台唤醒用户看不到的 Agent。
4. 正文目标与结构化 ID 不一致：不发布。
5. 双向匹配后调用现有 `preflight_group_agent_handoff()`。

完整预检继续校验：

- Group 和 Session；
- source Run、parent/root lineage；
- sender participant；
- 目标仍是当前群成员；
- 目标是可运行的 Agent participant；
- 目标模型、预算和 rollout；
- cycle guard；
- cutoff 和 idempotency key。

通过后生成现有 `GroupAgentHandoffIntent`，并冻结为 `group_handoff_intent`。

如果正文或预检需要修复：

- 不发送公开消息；
- 不创建 child Run；
- 保留 `pending_group_at`，允许模型修改正文；
- 模型可以重新调用 `at` 覆盖或清除目标。

#### 6. Verify、Finalize 与 Delivery 层

继续复用现有终态主链：

```text
final content
    ↓
business verification
    ↓
terminal checkpoint
    ↓
delivery revalidation
    ↓
同一事务：
- 创建公开 ChatMessage
- 创建结构化 mentions
- 创建目标 child Runs
- 创建 Start Commands
- 写入 delivery event/receipt
```

正式 checkpoint 保持现有业务语义：

```json
{
  "final_answer": "公开回复正文",
  "delivery_request": {
    "content": "公开回复正文",
    "group_handoff": {
      "mention_participant_ids": [
        "participant-uuid"
      ]
    }
  }
}
```

模型面对的是 `at`；Runtime 和 checkpoint 继续使用 `group_handoff`，因为它表达的是经过预检的 child Run 路由事实。

### 数据库影响

核心方案不需要数据库 Schema 变化：

- 不新增表、列、索引或外键；
- `pending_group_at` 存在现有 LangGraph checkpoint JSON 中；
- 正式 `group_handoff` 继续使用现有 checkpoint/delivery 结构；
- ChatMessage、mentions、AgentRun 和 Start Command 继续写入现有表。

`at` 是 Runtime 专用 Tool，由代码注入，不进入可配置 Tool 数据库。

2026-07-21 已直接连接当前项目配置指向的实际 `clawith` 数据库核对：

```text
tools_table_exists=True
finish_rows=[]
matching_columns=[]
```

确认当前数据库：

- `public.tools` 表存在，但没有 `name='finish'` 的 row；
- 没有 `finish_content`、`finish_delivery_intent`、`final_answer`、`group_handoff` 或 `pending_group_at` 独立列；
- 不需要清理旧 row；
- 不需要 Alembic 数据迁移或 Schema migration。

代码中仍有 `FINISH_TOOL_SEED`，bootstrap 的 `seed_builtin_tools()` 未来可能创建 `finish` row。因此协议迁移必须同时删除该 seed 和 `SYNC_IS_DEFAULT_TOOL_NAMES` 中的 `finish`，从源头防止以后写入数据库。

### 新风险与控制

| 风险 | 级别 | 控制方式 |
| --- | --- | --- |
| Provider `finish_reason` 缺失或不一致 | 高 | 统一归一化；兼容 `None + 非空正文`；显式截断和过滤不得完成 |
| `pending_group_at` 与 Tool Result写入不一致 | 高 | 在同一次 LangGraph state update 中原子写入 |
| 旧 checkpoint 仍包含 `finish` | 高 | 保留一版旧 parser/no-op，只是不再向新请求暴露 |
| at目标与最终正文不一致 | 高 | 最终发布前双向校验，不一致时零消息、零 child Run |
| `at` Tool Call重放 | 中 | Tool Call ID 幂等；重复执行不能产生外部副作用 |
| 多一次模型调用带来延迟和 Token | 中 | 接受标准两步 Tool Loop，换取跨 Provider 兼容性 |
| 目标在 at后退出群聊或失效 | 低 | 最终 preflight 和 delivery 二次校验 |
| Bootstrap Seeder 未来创建 finish row | 低 | 与协议迁移同时删除 `FINISH_TOOL_SEED` 和默认同步名单中的 `finish` |

关键安全约束：

1. `at` 永远不能直接发送公开消息或创建 child Run。
2. `pending_group_at` 与 Tool Result必须原子进入 checkpoint。
3. Tool Call存在时永远优先进入 Tool Node，同轮 content不能提前完成。
4. 最终正文和结构化目标必须双向匹配。
5. child Run 只允许在 verify 通过后的 terminal delivery 中创建。
6. delivery retry 继续使用现有 idempotency key，不能重复发送或重复创建 child Run。

### 对 BUGS_TO_FIX.md 的影响

采用私信自然结束和群聊 `at` 后：

1. 第 14 条中的 `invalid_finish` / `missing_finish` 主路径消失，不需要按原来的 `partial_answer` 方案修复。若未来仍要展示失败草稿，应作为通用失败恢复体验重新设计。
2. 第 15 条的 finish开关主体问题被协议迁移取代：`finish` 不再是模型工具，也不再需要可配置开关。
3. Enterprise 使用 finish探测工具能力的问题仍需修复，已纳入第一部分的 `capability_probe`。
4. `repair_draft` 被当作普通流式 Assistant 内容展示仍是独立 Bug，不能因为移除 finish而忽略。
5. “Native tool calling is not working”错误分类过宽仍需修复，应该区分 Tool JSON、at路由、Provider 能力和停止原因错误。
6. 第 6 条模型验证和分配门禁仍需修复；Agent 仍依赖真实 Tool Calling能力。
7. Group Planning/Compact 错误复用 Agent Tool Calling门禁的问题仍需独立处理。
8. 长 `write_file` 参数截断与 finish正文迁移无关，仍是独立可靠性问题。

### 群聊回归测试与验收标准

1. Group Run 的 Tool Schema 包含 `at`，不包含模型可见 `finish`。
2. 普通群聊回复无 Tool Call、自然停止后直接完成。
3. `at` schema不包含正文，只接受 participant UUID 数组。
4. `at` 成功时只写入 `pending_group_at` 和 Tool Result，不发送消息、不创建 child Run。
5. checkpoint恢复后 `pending_group_at` 不丢失、不重复执行。
6. 新 `at` 调用覆盖旧目标，`at([])` 可以清除。
7. 同一响应含 content和 `at` 时只执行 Tool Loop，不提前发布 content。
8. 字面 `@Agent` 缺少结构化 ID 时不发布。
9. 结构化 ID缺少对应可见 `@Agent` 时不发布。
10. 无效、离群、非 Agent或不可运行目标预检失败时零消息、零 child Run。
11. 预检通过后 terminal checkpoint 包含完整 `group_handoff`。
12. 成功投递时公开消息、mentions、child Runs和 Start Commands同事务创建。
13. delivery retry 不重复消息、mentions或 child Run。
14. B child Run 在同一 Group Session中公开回复。
15. 私信和旧 checkpoint兼容测试保持通过。

### 分阶段实施

1. 先完成第一部分：停止原因透传和私信自然结束。
2. 私信稳定后，增加 Group-only `at` Tool和 `pending_group_at`。
3. 接入 Tool Node原子 checkpoint更新。
4. 增加最终正文与结构化目标的双向校验。
5. 复用现有 preflight、verify、terminal checkpoint和原子 delivery。
6. 保留旧 `finish` 响应兼容一版，但不再向新模型请求暴露。
7. 完成定向、全量和恢复/幂等测试后，再清理遗留 parser、no-op executor、Prompt、UI兼容文案和旧协议测试。
