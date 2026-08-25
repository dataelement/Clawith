# Tasks: 飞书群主动消息

**Input**: `specs/003-feishu-group-proactive/` 设计工件

## Phase 1: Setup

- [x] T001 确认分支、迁移头、脏工作区边界和现有飞书/Runtime测试基线，记录于 `specs/003-feishu-group-proactive/tasks.md`

## Phase 2: Foundational

- [x] T002 [P] 先为飞书群目标范围、route 构造和越权拒绝增加测试 `backend/tests/test_feishu_group_targets.py`
- [x] T003 [P] 先为目录群条目和 Tool 合同增加回归测试 `backend/tests/test_query_directory_tool.py`、`backend/tests/test_human_send_tools.py`、`backend/tests/test_builtin_tool_contracts.py`
- [x] T004 实现共享飞书群目标解析服务 `backend/app/services/feishu_group_targets.py`

## Phase 3: User Story 1 - Agent 主动群发

- [x] T005 [US1] 扩展目录查询返回当前 Agent 可联系飞书群 `backend/app/services/agent_directory.py`
- [x] T006 [US1] 扩展现有 `query_directory`/`send_channel_message` schema 与 handler `backend/app/services/builtin_tool_definitions.py`、`backend/app/services/agent_tools.py`
- [x] T007 [US1] 运行目录、Tool、Provider scoped pytest 与 Ruff

## Phase 4: User Story 2 - 浏览器 Direct Chat 群发

- [x] T008 [US2] 增加普通 Runtime Tool 到飞书群的 typed outcome 回归 `backend/tests/test_human_send_tools.py`
- [x] T009 [US2] 验证浏览器 Direct Chat 无需飞书入站上下文即可使用群目标 `backend/tests/test_human_send_tools.py`

## Phase 5: User Story 3 - Schedule 群投递

- [x] T010 [P] [US3] 增加 Schedule Runtime 兼容与冻结目标回归 `backend/tests/test_schedule_runtime_intake.py`、`backend/tests/test_heartbeat_runtime.py`
- [x] T011 [US3] 增加 Schedule 可选目标模型与迁移 `backend/app/models/schedule.py`、`backend/alembic/versions/*_schedule_trigger_feishu_group_target.py`
- [x] T012 [US3] 接入 Schedule CRUD 与 Runtime intake `backend/app/api/schedules.py`、`backend/app/services/heartbeat_runtime.py`、`backend/app/services/scheduler.py`
- [x] T013 [US3] 扩展前端 Schedule API 类型合同 `frontend/src/services/api.ts`

## Phase 6: User Story 4 - Trigger 群投递

- [x] T014 [P] [US4] 运行 Trigger API/Tool/Runtime scoped 回归 `backend/tests/test_trigger_runtime_intake.py`、相关 Trigger Tool 测试
- [x] T015 [US4] 增加 Trigger 可选目标模型/API/Tool 合同 `backend/app/models/trigger.py`、`backend/app/api/triggers.py`、`backend/app/services/builtin_tool_definitions.py`、`backend/app/services/agent_tools.py`
- [x] T016 [US4] 在 Trigger Runtime 注册时校验并冻结飞书群 route `backend/app/services/trigger_runtime/intake.py`

## Phase 7: User Story 5 - 兼容回归

- [x] T017 [US5] 验证飞书私聊、飞书群回复和其他 Provider `target_member_id` 行为不变 `backend/tests/test_human_send_tools.py`、`backend/tests/test_agent_runtime_delivery.py`

## Phase 8: Polish, Merge and Deploy

- [x] T018 运行 scoped/full backend tests、Ruff、frontend tests/build、Alembic heads、Architecture Guard
- [x] T019 审查最终 diff、提交 Lore commit 并合并到本地 `v1.11.4`
- [x] T020 重新发现并备份 3010 部署目标，部署合并后的 `v1.11.4` 并验证容器/迁移/源码 marker
- [ ] T021 在 3010 验证浏览器主动飞书群发与自动化群投递，核对 Run、Tool、ChannelDelivery、Provider 回执和群内消息

## Dependencies

- T001 → T002/T003 → T004 → T005/T006 → T007
- T007 → T008/T009
- T004 → T010 → T011/T012 → T013
- T004 → T014 → T015/T016
- T009/T13/T16 → T017 → T018 → T019 → T020 → T021

## Implementation Strategy

先完成并验证现有 Tool 的飞书群目标，再接 Schedule/Trigger；外部部署只使用合并后的 `v1.11.4` 候选，真实 Provider 验证与本地测试分开报告。
