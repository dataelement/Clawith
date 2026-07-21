# 通讯录与经验库合并后的回归待修复清单

## 状态

- 记录日期：2026-07-15
- 基线分支：`feature/unified-chat-directory-pr760-regression`
- 记录基线：`c2e6ddae`
- 当前状态：已确认、未修复
- 本轮边界：这里只记录问题，不在部署 3010 前顺带修改功能行为

## DIR-01：通讯录自定义人员查询存在 SQLAlchemy auto-correlation 异常

### 已知影响

- `get_custom_directory_human_candidates` 候选人查询可能返回 500。
- `agent_directory._custom_human_authorized_condition` 的同类子查询会影响 `member_type=human` 和精确人员目标查询。

### 已观察错误

`InvalidRequestError: returned no FROM clauses due to auto-correlation`

### 后续验收

- 候选人员接口在有、无自定义人员配置时都能稳定返回。
- 精确人员目标授权和人员目录列表使用同一套 tenant-scoped 条件。
- 增加真实 SQLAlchemy 查询回归，不只验证 mock 调用。

## DIR-02：通讯录路由测试与实际前缀契约不一致

### 已知影响

`test_agent_directory_router_exposes_custom_maintenance_routes` 期望 `/custom/humans` 等相对路径，实际 Router 暴露的是 `/agents/{agent_id}/directory/custom/humans` 等完整路径。

### 后续验收

- 先确认产品 API 契约以完整路径还是子 Router 相对路径为准。
- 测试和 OpenAPI 路由保持一致，避免只为通过测试改变线上路径。

## EXP-01：普通成员可以通过 `view=all` 枚举无权查看的经验

当前列表查询可能暴露 draft、retired 或指定范围外的经验元数据。后续需要把可见性限制下沉到分页前的数据库查询，并覆盖普通成员、创建者、审核人和管理员角色。

## EXP-02：经验详情直查绕过可见性判断

按 ID 获取经验时当前只校验 tenant，未复用列表的 visibility contract。后续需要统一列表、详情和引用展开的授权入口。

## EXP-03：指定部门校验未限定 tenant

部门存在性检查可能接受其他 tenant 的部门 ID。后续需要使用 `tenant_id + department_id` 联合条件，并覆盖跨租户拒绝测试。

## EXP-04：沉淀经验和兼容草稿入口未校验 Agent 访问权

distill 与兼容 draft 路径没有完整执行调用 Agent 的访问检查。后续需要统一草稿创建入口的 Agent ownership/visibility 校验。

## EXP-05：经验标签过滤发生在分页之后

当前 tag 过滤会造成页面条数不足、总数不准或符合条件的数据落到后续页面。后续需要把 tag 条件放入分页 SQL，并验证 total、page size 和排序稳定性。

## TOOLS-01：`agent_tools.py` 保留既有静态检查债务

本次合并修复只补回冲突中丢失的导入和 `_canonicalize_llm_tool`，没有清理该文件的历史 Ruff 问题。当前 `ruff check --select F app/services/agent_tools.py` 仍报告既有未使用导入、无占位符 f-string，以及 `_cached_users` 未定义等问题。

后续应先区分纯静态清理和真实运行时缺陷；涉及行为的 `_cached_users` 问题单独修复并增加回归，避免一次性机械修改超大文件。

## TEST-01：4 个 Workspace 测试依赖本机 Redis，尚未纳入无外部依赖基线

当前本机 `localhost:6379` 未运行 Redis，以下测试因连接失败而未完成验证：

- `test_flush_temp_workspace_only_writes_changed_files`
- `test_flush_temp_workspace_fails_on_conflict`
- `test_move_workspace_path_fails_when_source_changes`
- `test_delete_workspace_directory_uses_prefix_existence`

后续回归时应启动隔离 Redis 或使用仓库规定的集成测试环境重新执行，不能把连接失败记成功能失败，也不能将这 4 项标记为已通过。

## 修复顺序建议

1. 先修 EXP-01 至 EXP-04 的授权和租户隔离问题。
2. 再修 DIR-01，恢复通讯录真实查询可用性。
3. 修 EXP-05 和 DIR-02，稳定分页与接口契约。
4. 在 Redis 集成环境补跑 TEST-01。
5. 将 TOOLS-01 拆成静态清理和运行时 bug 两批处理。
