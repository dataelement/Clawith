# 群聊前端实时与接口契约

状态：接口与前端集成已实现，待部署环境 E2E 验证
关联：`technical-design.md` 第 11 章、`backend/app/api/groups.py`、`backend/app/api/group_websocket.py`、`frontend/src/hooks/useGroupRealtime.ts`

> 本文描述当前集成基线，不代表浏览器 E2E 已通过。所有实时恢复、双身份邀请和二进制文件场景仍须在部署环境留存实际执行证据。

## 1. 消息传输主链路

群聊统一采用以下数据链路：

- **REST 写入**：所有人类消息继续通过 `POST /api/groups/{group_id}/sessions/{session_id}/messages` 提交，WebSocket 不承担上行写入。
- **REST 历史**：通过同一 session 的 messages GET 接口读取最近一页或使用 `before` 向旧消息翻页。
- **Group WebSocket 推送**：通过一条 group-scoped WebSocket 接收整个群的 `message.created` 事件。
- **`after` 正向补拉**：首次连接、重连和轮询恢复时，从当前 session 最后确认的 cursor 继续补齐新消息。
- **4 秒轮询临时兜底**：仅在 WebSocket 未就绪或断开期间运行，不作为常态传输方案。

页面继续按 `message.id` 合并消息。WebSocket 的 best-effort 提示、REST 返回和重连补拉可以重复到达，但不能产生重复气泡；消息完整性由 REST 补拉保证，而不是由 WebSocket 投递保证。

## 2. 消息历史与 `after` cursor

接口：

```text
GET /api/groups/{group_id}/sessions/{session_id}/messages
GET /api/groups/{group_id}/sessions/{session_id}/messages?before=<cursor>&limit=<n>
GET /api/groups/{group_id}/sessions/{session_id}/messages?after=<cursor>&limit=<n>
```

契约如下：

- cursor 使用统一 Message Position：`<created_at ISO 8601>|<message UUID>`。
- `before` 返回比 cursor 更旧的消息，响应仍按 `(created_at, id)` 升序展示。
- `after` 返回比 cursor 更新的消息，按 `(created_at, id)` 升序。
- `before` 与 `after` 互斥，同时传入返回 400。
- 都不传时返回最近一页。
- `after` 补拉采用“返回条数等于 `limit` 时继续请求下一页”的约定，直到短页或空页；不设置客户端总页数上限。

前端为每个 session 分开维护 **REST-confirmed cursor** 与 **WS-observed cursor**。只有从 REST-confirmed cursor 开始的完整补拉成功结束后，才能推进下一次 `after` 的起点；WebSocket 事件只能合并到界面、更新 observed 水位并触发从 REST-confirmed cursor 开始的补拉，绝不能直接成为 `after` 起点，否则乱序或漏投事件会跳过尚未取回的消息。

REST-confirmed cursor、WS-observed cursor、in-flight request、AbortController 和 generation 均按 `group_id + session_id` 隔离。切换 session 或 group 时必须取消失效请求，旧响应不得写入新会话。

## 3. Group WebSocket

端点：

```text
WS /ws/group/{group_id}?token=<JWT>
```

### 3.1 鉴权与订阅范围

- JWT 使用 `token` query parameter。
- 服务端校验 active user、tenant 与当前群的 active membership。
- 一条连接订阅整个群，而不是单个 session；事件携带 `session_id`。
- 群内任一 session 出现消息时，前端都刷新 session 列表，使所有 session 的 `unread_count` 保持最新；只有当前 active session 的消息进入当前消息列表。

### 3.2 Ready 与消息事件

服务端完成鉴权和 presence 注册后发送：

```json
{
  "type": "connected",
  "group_id": "<uuid>"
}
```

`connected` 是连接可用边界。前端收到它以后，先对 active session 执行一次 `after` catch-up，再进入纯实时状态；建连后 10 秒内没有收到该事件时，前端进入 REST 轮询、关闭当前连接并继续后台重连。

v1 消息事件：

```json
{
  "type": "message.created",
  "session_id": "<uuid>",
  "message": { "cursor": "<created_at>|<message_id>" }
}
```

`message` 与 REST 的 `GroupMessageOut` shape 一致，并包含 cursor。所有公开群消息使用同一种事件，包括人类消息、ACK、Agent 最终回复、callback 和安全的 system message；Agent 中间思考和工具过程不作为群消息推送。

### 3.3 断线恢复

非终止性断线后，前端立即执行以下动作：

1. 启动 4 秒轮询，不等待第一次间隔到期。
2. 每次轮询刷新整个群的 sessions，更新所有 session 的未读数。
3. 对 active session 使用 `after` cursor 补拉消息。
4. 同时在后台按指数退避恢复 WebSocket，退避上限为 30 秒且不会因固定失败次数永久停止。
5. 新连接收到 `connected` 后再次 catch-up；同步成功后停止轮询，恢复 WebSocket 常态链路。

以下 close code 表示认证、群状态或成员权限已经失效，不再自动重连：

- `1008`：policy violation
- `4001`：认证失败
- `4002`：服务端已确认群不存在
- `4003`：active membership 不成立

非业务性的数据库异常、Redis presence 注册异常和其他瞬时服务端故障使用 `1011`。前端必须把它当作可恢复断线：启动 REST 轮询并持续退避重连。`4002` 只用于真实的 `group_not_found`，不能用于临时初始化失败。

## 4. 双身份邀请契约

接口：

```text
POST /api/groups/{group_id}/members
```

请求支持两种互斥身份输入：

```json
{ "participant_id": "<participant uuid>" }
```

或：

```json
{
  "participant_type": "user | agent",
  "ref_id": "<user_id or agent_id>"
}
```

规则如下：

- 必须且只能提供一种身份；业务身份必须同时提供 `participant_type` 与 `ref_id`。
- `participant_id` 路径用于兼容既有调用；服务端仍回查其业务身份并执行同一权限策略。
- `(participant_type, ref_id)` 路径由服务端解析或懒创建 Participant，前端不需要提前取得 `participant_id`。
- 前端候选来自现有 user/agent 列表，并隐藏 Private、过期及非可运行状态的 Agent；这是 UX 预过滤，最终同租户、可见性、active/expiry 状态和 Private Agent 规则以后端为准。
- 服务端必须先校验调用者是 active human member，再解析目标身份，避免非成员利用 403/404 差异枚举同租户用户或 Agent。
- manager 移出成员提交后发布定向 `membership.revoked`，所有实例先冻结旧连接快照、再复核当前 membership generation，只关闭确属旧一代的 socket 并以 `4003` 结束；重新邀请后建立的新连接不会被迟到 revoke 误关。每个实际承载 socket 的实例还会在发送 `message.created` 前，以 `groups` 行共享锁批量复核本机候选 participant；邀请/移出使用同一行排他锁，因此迟到 Redis envelope 也不能把消息送给已提交移出的成员。Redis 撤员通知失败时仍由 heartbeat 复核兜底。

## 5. Group Workspace 二进制文件

现有 list/read/write/delete 文本能力继续保留，并增加二进制上传和下载：

```text
POST /api/groups/{group_id}/workspace/upload?path=<directory>
Content-Type: multipart/form-data

GET /api/groups/{group_id}/workspace/download?path=<file>&inline=<bool>
Authorization: Bearer <JWT>
```

为支持浏览器文件 URL，download 也可使用 `token` query parameter。服务端仍执行 active membership、路径规范化和 group scope 校验。

前端 `FileBrowser` 已接入 upload adapter 与 download URL，并启用上传。上传后必须能够在同一群的其他 session 中列出并下载原始二进制内容；文本文件的 optimistic version 行为不因新增二进制接口而改变。

单文件上传上限为 50 MiB，服务端在读取 multipart 内容前完成 active human membership 校验，并按 1 MiB 分块读取。所有 group workspace 写、上传和删除都先取得 `group_id + normalized path` 的 Redis mutation lock，并持有到数据库 commit/rollback 完成；rollback 按 LIFO 执行，先做带 version condition 的 upload compensation、再释放锁。revision、audit 或最终 commit 失败时，只恢复本次上传仍是当前版本的对象，不覆盖随后成功写入的新版本；Agent Runtime 工具也必须使用同一 callback-aware transaction 边界。进程在 storage 写成功且补偿尚未完成时硬退出仍是 v1 已知边界，正式消除需 durable storage outbox/staging finalize。

`modified_at` 当前不是统一格式：本地存储返回浮点秒字符串，S3 返回 provider datetime 的字符串表示。前端在后端统一为 ISO 8601 之前必须把它当作不透明展示值或兼容解析，不得假定所有环境都返回同一种时间格式。

## 6. 待执行的验证

当前代码已通过前端 TypeScript 与 Vite 生产构建，但以下项目仍为 **待验证**，不能登记为 E2E 通过：

1. 用 user 与 agent 业务身份完成邀请，并验证 legacy `participant_id` 与业务身份不会制造重复 Participant/membership。
2. 两个成员通过 REST 发消息，验证整个群的 WebSocket 推送及非 active session 未读刷新。
3. 断开 WebSocket，在 active session 制造超过一页的新消息，验证 4 秒轮询、无上限 `after` 补拉、按 ID 去重和恢复后的追平。
4. 验证 `1008/4001/4002/4003` 不重连；瞬时 DB/Redis 初始化故障返回 `1011`，并持续后台恢复。
5. 移出一个已连接成员，验证跨实例 socket 立即收到 revoke 并以 `4003` 关闭，关闭后不再收到任何群消息。
6. 上传 PDF、图片或表格，跨 session 下载并逐字节比对；验证非成员、路径穿越、超 50 MiB 和失效 token 均被拒绝，并注入 DB rollback 验证旧对象按版本安全恢复。
