# Implementation Plan: 飞书群主动消息

**Branch**: `003-feishu-group-proactive` | **Date**: 2026-08-18 | **Spec**: [spec.md](spec.md)
**Input**: 浏览器 Direct Chat、定时任务和后台 Trigger 均可通过现有 `send_channel_message` 主动向已登记飞书群发送消息；不新增 Tool，不扩展其他 Provider。

## Summary

复用现有飞书群 `ChatSession` 作为稳定群目标，以 Session UUID 作为模型可见的 `target_recipient_id`，不暴露 `chat_id`。`query_directory` 增加只属于当前 Agent/租户的飞书群条目；`send_channel_message` 在保留 `target_member_id` 的同时解析飞书群目标并复用 Durable Runtime 的 ChannelDelivery/Provider 发送链路。Schedule 与 Trigger 保存可选目标 Session UUID，注册 Run 时解析、校验并冻结现有 `delivery_target`，从而复用已存在的幂等终态投递，不创建第二套执行或发送状态机。

## Technical Context

**Language/Version**: Python 3.12 deployment baseline（package metadata >=3.11）；React 19 / strict TypeScript
**Primary Dependencies**: FastAPI、SQLAlchemy async ORM、Pydantic、LangGraph Runtime、React Query、Vite；不新增依赖
**Storage**: PostgreSQL 15；复用 `chat_sessions` 与 `channel_deliveries`，为 Schedule/Trigger 增加可选目标 Session UUID
**Testing**: pytest、Ruff、前端 Vitest/build、Architecture Guard、3010 真实飞书群验证
**Target Platform**: Docker Compose Linux deployment，目标环境 3010
**Project Type**: FastAPI + React monorepo web application
**Performance Goals**: 目录查询保持分页且无 N+1；自动化完成后正常条件下 60 秒内投递
**Constraints**: 只做飞书；不新增 Tool；不暴露 Provider `chat_id`；租户/Agent 双重范围；未知结果不重放；保留现有各渠道 `target_member_id` 行为
**Scale/Scope**: 一个 Agent 的既有飞书群会话目录；文本消息；浏览器、Schedule、Trigger 三种入口

## Constitution Check

*GATE: Phase 0 前通过；Phase 1 设计后再次通过。*

- **Evidence Before Claims — PASS**: 当前 `send_channel_message` 只解析人；飞书 Provider 已支持 `chat_id`；飞书入站已持久化 Agent 归属的群 Session；Trigger/Schedule 当前未冻结飞书群目标。
- **Minimal Scoped Changes — PASS**: 复用 `ChatSession`、`query_directory`、`send_channel_message`、`AgentRun.delivery_target`、`ChannelDelivery`，不新增 Provider、不新增依赖、不新增 Tool。
- **Contract and State Ownership — PASS**: `ChatSession` 拥有稳定会话目标；Runtime 拥有 Run 与投递编排；`ChannelDelivery` 拥有外部投递事实；飞书 Adapter 拥有 `chat_id` 映射。
- **Tests Prove Behavior — PASS**: 先覆盖目录/Tool/自动化入口的成功、越权、重复和未知结果，再实施。
- **Preserve Existing Work — PASS**: 仅修改 feature spec 与相关 backend/frontend 文件，不触碰已有 `docs/` 脏改动。
- **P0 C1 — PASS**: Schedule/Trigger 只向 Runtime 提交冻结目标，不写 checkpoint 生命周期。
- **P0 C2 — PASS**: 每个群目标查询同时约束 `tenant_id`、`agent_id`、`source_channel=feishu`、`is_group=true`、未删除。
- **P0 C3/C4 — PASS**: 外部写复用幂等 ChannelDelivery 和飞书 service wrapper。
- **P0 C5 — PASS**: 不新增物理外键；分页查询；不引入循环查询。
- **P0 C6 — PASS**: 目标解析抽成小型共享服务，不继续把飞书群分支堆在 `agent_tools.py`。

## Project Structure

### Documentation

```text
specs/003-feishu-group-proactive/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── directory-and-tool.md
│   └── automation-delivery.md
└── tasks.md
```

### Source Code

```text
backend/
├── alembic/versions/                     # Schedule/Trigger target columns
├── app/models/{schedule,trigger}.py
├── app/services/
│   ├── agent_directory.py                # Feishu group directory projection
│   ├── feishu_group_targets.py           # scoped target resolve + route builder
│   ├── agent_tools.py                    # existing Tool delegates to resolver
│   ├── heartbeat_runtime.py              # Schedule target freeze
│   └── trigger_runtime/intake.py          # Trigger target freeze
├── app/api/{directory,schedules,triggers}.py
└── tests/                                # contract/runtime/regression coverage

frontend/
├── src/services/api.ts
├── src/pages/agent-detail/AgentDetailPage.tsx
└── src/i18n/{zh,en}.json
```

**Structure Decision**: 使用现有 backend/frontend 布局；新增一个窄的后端飞书群目标服务，保持 Tool handler、API 和 Runtime intake 共享同一校验与 route 构造逻辑。

## Design Sequence

1. 用回归测试锁定现有用户私聊和其他 Provider 的 `target_member_id` 行为。
2. 让可信飞书群入站继续创建 `ChatSession`，目录将符合约束的 Session 投影为 `member_type=group`、`provider_type=feishu`、`target_recipient_id=<session UUID>`。
3. `send_channel_message` 优先识别 `target_recipient_id`，交给共享 resolver；人类旧参数沿原路径执行。群目标解析为 frozen channel route 后进入现有 typed Tool outcome 与 ChannelDelivery，不直接调用 Provider 裸接口。
4. Schedule/Trigger CRUD 接受可选 `delivery_target_id`，保存 Session UUID；启用/创建时校验，Run 注册时再次校验并冻结 route。
5. 前端 Schedule 表单加载飞书群目录并提供“仅保留在 Clawith / 发送到飞书群”选择；Trigger 管理 API 和 Agent Tool schema 支持同一字段。
6. 运行测试、Ruff、前端测试/build、迁移检查、Architecture Guard；再以窄范围方式部署 3010。
7. 3010 分别验证部署标记、容器、迁移、目录、浏览器主动群发、自动化群发、Run/Tool/ChannelDelivery/Provider 回执和群内实收。

## Post-Design Constitution Check

全部通过。设计没有新增执行状态机、外部发送 Tool 或 Provider 直连入口；唯一新增持久字段是自动化对稳定 Session 目标的引用，实际投递继续由 Runtime/ChannelDelivery 所有。

## Complexity Tracking

无宪法例外。
