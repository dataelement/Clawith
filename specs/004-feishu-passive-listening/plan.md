# Implementation Plan: 飞书群常驻 Agent V1

**Branch**: `004-feishu-passive-listening` | **Date**: 2026-08-18 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/004-feishu-passive-listening/spec.md`

## Summary

为现有飞书 Agent Channel 增加群内全部用户消息接收能力。每条被接受的群消息继续通过现有 `enqueue_channel_chat_runtime()` 创建 Durable Runtime Run；模型无需发言时输出 token-only `NO_REPLY`。Runtime 仍把它当作正常非空完成文本，产品侧仍可保留内部 Assistant `ChatMessage`，但外部渠道投递在创建 `ChannelDelivery` 之前识别“成功终态 + 飞书群路由 + 精确静默令牌”并抑制 Provider 出站。现有 Session Context 后台压缩扩展到 `group_id IS NULL` 的飞书外部群 Session，并使用该 Session 所属 Agent 的模型预算；不新增状态表、不改变 checkpoint 生命周期、不统一其他渠道。

## Technical Context

**Language/Version**: Python 3.12 deployment baseline（package metadata >=3.11）；React 19 / strict TypeScript
**Primary Dependencies**: FastAPI、SQLAlchemy 2.x async ORM、LangGraph 1.2.x、Pydantic、httpx、React/Vite；不新增依赖
**Storage**: PostgreSQL 15；复用 `chat_sessions`、`chat_messages`、`agent_runs`、`agent_run_events`、`channel_deliveries`、`session_context_states`
**Testing**: Pytest、Ruff；前端使用现有 test/build 命令
**Target Platform**: Clawith backend/API/Runtime workers + 飞书自建应用机器人
**Project Type**: Docker Compose monorepo web application
**Performance Goals**: 飞书事件回调只负责持久化消息与 Runtime Command 后返回；模型执行和外部投递保持异步。重复 Provider 消息不产生第二次 Run。长期群历史经滚动压缩后不进行无界全量装载。
**Constraints**: V1 每条群消息均调用模型；精确 `NO_REPLY` 只抑制飞书群最终出站；不得影响失败/取消语义、飞书私聊、原生群和其他渠道；不得伪造原生 `group_id`。
**Scale/Scope**: 单 Agent 多飞书群的长期 Session；本版不新增 Activation Gate、不统一企微/钉钉等渠道。

## Constitution Check

*GATE: Passed before Phase 0 and re-checked after Phase 1.*

| Gate | Result | Design evidence |
|---|---|---|
| Evidence Before Claims | PASS | 当前权限、入站幂等、ChannelDelivery 建立点和压缩器 `group_id` 限制均由源码与测试确认；OpenClaw 静默行为使用官方仓库与文档。 |
| Minimal Scoped Changes | PASS | 仅扩展飞书权限、飞书入站稳定 ID、外部投递静默过滤和飞书群压缩；不做跨渠道抽象或数据库迁移。 |
| Contract and State Ownership | PASS | 模型只拥有最终内容；Runtime checkpoint 不变；产品交付层决定是否建立飞书 outbox；Provider sender 仍拥有真实发送结果。 |
| Tests Prove Behavior | PASS | 计划先加入 token 精确匹配、零 outbox、普通回复、重复入站和外部群压缩水位线回归测试。 |
| Preserve Existing Work | PASS | 规格目录独立；现有 `docs/` 用户改动保持未触碰。 |
| C1 Runtime Boundary Isolation | PASS | 不增加 checkpoint 字段或第二状态机；API 仍只通过 Runtime Command Intake。 |
| C2 Multi-Tenant Scope | PASS | 新增查询分支必须同时按 `tenant_id`、Session、Agent 范围验证。 |
| C3 Idempotent Side Effects | PASS | 入站以飞书 `message_id` 幂等；静默路径不创建 `ChannelDelivery`；普通出站继续复用现有 outbox。 |
| C4 Wrapper Enforcement | PASS | 无新增前端 HTTP 请求；飞书发送继续通过现有 Provider sender。 |
| C5 DB/Performance | PASS | 无新表、无新 FK；压缩扫描保持批量扫描与现有 advisory lock/CAS。 |
| C6 Modularity | PASS | 静默识别作为小型纯函数；复用现有 Session Context policy/compactor/scanner。 |

## Project Structure

### Documentation (this feature)

```text
specs/004-feishu-passive-listening/
├── spec.md
├── design.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── feishu-passive-listening.md
├── checklists/
│   └── requirements.md
└── tasks.md                 # 下一阶段生成
```

### Source Code (repository root)

```text
backend/app/
├── api/feishu.py
├── services/
│   ├── agent_runtime/
│   │   ├── channel_delivery.py
│   │   ├── delivery.py
│   │   ├── model_step_service.py
│   │   ├── session_context_background.py
│   │   ├── session_context_compactor.py
│   │   └── session_context_service.py
│   └── llm/finish.py

backend/tests/
├── test_feishu_channel_runtime.py
├── test_agent_runtime_channel_delivery.py
├── test_agent_runtime_delivery.py
├── test_agent_runtime_session_context_background.py
├── test_agent_runtime_session_context_compactor.py
└── test_session_context_service.py

frontend/src/
└── components/ChannelConfig.tsx
```

**Structure Decision**: 保持现有 API → Channel Runtime Intake → Durable Runtime → Product Delivery → Provider Worker 分层。静默属于产品侧外部渠道投递过滤，不进入模型协议解析器或 Runtime graph；压缩属于现有 Session Context 子系统。

## Phase 0: Research Decisions

详见 [research.md](./research.md)。已解决所有设计未知项：飞书全量权限、Provider 幂等键、OpenClaw 精确静默令牌、Clawith 投递截断点、飞书外部群压缩模型归属。

## Phase 1: Design

### 1. 飞书入站

- 权限模板增加 `im:message.group_msg`；保留现有单聊、群 @ 与发送权限。
- `im.message.receive_v1` 继续作为唯一事件入口。
- Provider `message.message_id` 作为 `channel_message_id()` 的外部稳定输入；`event_id` 仅用于观测，不作为消息幂等权威事实。
- 过滤机器人自身/机器人发送者；V1 处理飞书推送的用户消息，不扩展机器人间群消息。
- API 在同一事务中持久化 `ChatMessage` 与 Runtime Command 后提交，模型执行不阻塞 Provider 回调。

### 2. 模型静默协议

- 仅飞书群 Run 的系统指令增加 token-only 规则。
- 静默令牌常量为 `NO_REPLY`。
- 识别函数只接受 `text.strip().casefold() == "no_reply"`；正文前后存在任何非空内容均不是静默。
- 不修改 `finish`、`ModelIntent`、Verifier、checkpoint lifecycle 或 finalizer。

### 3. 出站抑制

- 在 `deliver_runtime_message()` 已确定实际 Session 和现有 `channel_delivery` route 后、调用 `stage_channel_delivery()` 前判定。
- 必须同时满足：`kind=terminal`、`lifecycle_status=completed`、route channel 为 `feishu`、target `receive_id_type=chat_id`、内容为精确静默令牌。
- 命中后仍保留内部 Assistant `ChatMessage` 和常规本地 delivery receipt，使 Run 能正常 settled；不创建 `ChannelDelivery`，因此 Provider worker 无工作项且不会调用飞书 API。
- `agent_runs.delivery_status` 沿用“产品 Session 已投递”的既有含义：无 outbox 时为 `delivered`。审计通过终态内容为精确令牌且缺少对应 `channel_deliveries` 行证明有意静默；不新增数据库状态。
- waiting、failed、cancelled 不进入静默判定，沿用既有投递策略。

### 4. 模型可见历史过滤

- 底层 `ChatMessage(content="NO_REPLY")` 保留用于审计。
- 仅对 `session_type=group AND source_channel=feishu AND group_id IS NULL` 的 Session，Session Context 读取与 compactable 集合过滤精确静默 Assistant 消息。
- 用户消息、正常 Assistant 回复、正文包含 `NO_REPLY` 的消息不被过滤。
- 过滤不删除数据库行、不改变消息时间线水位线的权威位置；压缩水位线仍由最后一个实际纳入压缩的消息 ID 决定。

### 5. 飞书外部群 Session 压缩

- Session 判定：`session_type=group`、`group_id IS NULL`、`source_channel=feishu`、`agent_id IS NOT NULL`、未删除。
- Policy resolver 验证 Session Agent 同租户、可用且未删除，使用该 Agent 的 active model 计算阈值；`source_agent_id=session.agent_id`。
- LLMSessionContextCompactor 对此外部群使用该 Agent active model并记录 usage_agent_id；原生群仍使用 tenant multi-agent compact model。
- Scanner 使用两个明确分支联合选择原生群候选和飞书外部群候选，保持现有批量游标、advisory lock 和 CAS 提交。
- 不创建 `Group`/`GroupMember`，不写 `chat_sessions.group_id`，不修改外部会话 ID。

### 6. 测试顺序

1. 先加入精确静默识别与零 `ChannelDelivery` 回归测试。
2. 加入飞书 `message_id` 重试幂等测试。
3. 加入飞书外部群 policy/model selection/scanner 测试。
4. 加入 Session Context 过滤和水位线测试。
5. 实现最小代码变更。
6. 运行 scoped pytest、Ruff、`scripts/arch-guard.sh`；前端权限常量改动后运行前端 test/build。

## Post-Design Constitution Re-check

Phase 1 后仍全部 PASS。特别确认：静默不会修改 Runtime checkpoint state machine；压缩继续使用唯一 `session_context_states` 真相与 CAS；普通飞书发送继续由 `channel_deliveries` 和 Provider receipt 管理。

## Complexity Tracking

无 Constitution 违规，无需例外批准。
