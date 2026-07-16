# 3010 群聊交互修复定向回归报告

## 结论

- 回归日期：2026-07-16
- 环境：`http://192.168.106.118:3010`
- 结果：本轮 5 个受影响场景全部通过
- 部署代码：后端行为基线 `74d09949`，前端最终制品 `68a72eca`
- 当前分支：`feature/unified-chat-directory-pr760-regression`

本轮只回归本次修复直接影响的群聊交互边界，不重复执行单 Agent 长任务、完整群 Planning/Handoff/A2A、并发或压力测试。单 Agent 的模型循环、工具执行、压缩、重试、异步轮询与 Thread/Run 语义均未被这些提交修改。

## 修复与提交

| 提交 | 内容 |
|-|-|
| `74d09949` | 空标题 Session、Workspace canonical version token、全局 Agent 删除的历史保留、精确 Group Run 取消、群路由基础门禁 |
| `db77888c` | 本次挂载重新取得权威 Group 列表前，不允许 React Query 旧缓存授权子请求 |
| `68a72eca` | Group realtime catch-up 与侧栏未读汇总只使用权威 Group scope，不再使用未验证 URL ID |

## 自动化验证

### 前端

- `npm test`：32 passed，0 failed。
- `npm run build`：TypeScript 与 Vite production build 通过。
- 最终运行容器已确认加载 `assets/index-gelcnCey.js`，不是部署前旧制品 `assets/index-D1QZsOHv.js`。

### 后端

以下受影响测试文件共 30 项通过：

- `backend/tests/test_group_api.py`
- `backend/tests/test_group_file_service.py`
- `backend/tests/test_agent_delete_api.py`
- `backend/tests/test_agent_runtime_adapter.py`

目标 Python 文件 Ruff 与 compileall 检查通过。

## 浏览器 E2E 结果

| 场景 | 结果 | 关键证据 |
|-|-|-|
| 新建 Group Session 空标题 | PASS | 确认按钮允许空值；后端生成默认标题 `Session 07-16 13:23`。创建 Group 的名称校验未放宽。 |
| 未授权 Group URL | PASS | 组织管理员访问不可见 Group URL 后返回 `/groups`；网络记录只有 `/api/groups` 等公共页面请求，没有该 Group 的 Sessions、Members、Messages 或 realtime catch-up 请求；403 为 0，控制台错误为 0。 |
| Group Workspace 删除 | PASS | 列表返回的 token 可直接用于条件删除 `group-e2e-7319.md`；删除成功，文件从列表消失，控制台错误为 0。 |
| 全局 Agent 删除 | PASS | `E2E Regression 20260716` 从 Agent 侧栏和活跃 Group 成员中消失；删除前消息 `AGENT-HISTORY-BEFORE-DELETE-7319` 与发送者显示仍可读取。 |
| Group Run 显式取消 | PASS | Run `38b07294-d823-444a-9e29-d40c25661207` 显示“停止运行”；scoped cancel 返回 `200`、`status=cancelling`，后续 Run State 为 `cancelled`、`can_cancel=false`；页面显示“任务已取消”，没有继续生成完成回复。 |

## 回归中发现并关闭的问题

第一次部署后的未授权路由仍出现一次 Messages 403。请求追踪证明普通 history effect 已被门禁，遗漏来自 `useGroupRealtime` 直接使用 URL `groupId/sessionId` 执行首次 catch-up。最终修复将 realtime 与侧栏未读查询统一置于权威 Group 列表门禁之后，重新部署后子请求为 0。

部署验证还发现 `docker compose up --force-recreate --no-build frontend` 复用了旧镜像 ID。最终先确认新镜像内部包含 `index-gelcnCey.js`，再删除旧 frontend service container 并由 Compose 重新创建；当前容器镜像和实际 HTTP 静态制品已一致。

## 未重复执行

- 单 Agent Workspace-Bench 或其他长程复杂任务：本轮改动不触及其运行链路。
- DeepSeek `reasoning_content`、Skill 强制预载与进一步 Prompt 调优：已明确延后。
- 完整群 Planning、公开 Handoff、私下 A2A、并发和压力测试：本轮没有修改这些机制。
- 已产生工具副作用后的取消补偿：本轮只验证前端精确 Run 取消闭环，不把它扩大为副作用回滚验证。

## 环境清理

- 已删除空标题/取消回归临时 Session。
- 已删除两个本轮 E2E 临时 Group。
- 已删除测试 Agent `E2E Regression 20260716`。
- 保留环境原有的 Meeseeks、Morty 和既有业务 Group。

