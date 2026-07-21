# 群聊前端实时链路：落地契约与回归记录

状态：2026-07-16 已闭环（`after` 正向游标、Group WebSocket、断线回补和降级轮询均已落地）
关联：`technical-design.md` 第 11 章 API 草案、`backend/app/api/groups.py`

## 背景

群聊前端的数据链路确定为：

- **REST 发消息、拉历史** —— 复用已实现的 `/api/groups/...`。
- **WebSocket 实时推送** —— 群内新消息即时到达。
- **Cursor 断线补拉** —— 重连后按最后已知位置补齐断线期间的消息。
- **轮询只作临时兜底** —— WS 不可用时降级，不作为长期方案。

后端和前端现已按这套链路闭环：公开消息先提交数据库，再尽力推送；推送丢失时以前端持有的最后 cursor 正向补拉。WebSocket 健康时不轮询，只有建连失败或断线期间才启用 4 秒降级轮询。

---

## 已闭环 1：`after` 游标正向补拉

### 已落地行为

`GET /api/groups/{group_id}/sessions/{session_id}/messages` 同时支持互斥的 `before` 和 `after`：

```python
statement = (
    select(ChatMessage)
    .where(ChatMessage.conversation_id == str(session_id))
    .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
    .limit(limit)
)
if before is not None:
    statement = statement.where(
        tuple_(ChatMessage.created_at, ChatMessage.id) < tuple_(before[0], before[1])
    )
return list(reversed(result.scalars().all()))
```

`before` 继续用于向后加载历史；`after` 取严格晚于最后已知 `(created_at, id)` 的消息并按升序返回。

### 固定契约

给该接口增加互斥的 `after` 参数，语义与 `before` 对称：

```
GET /api/groups/{group_id}/sessions/{session_id}/messages?after=<cursor>&limit=<n>
```

- `after`：游标格式与现有 cursor 一致，`<created_at ISO 8601>|<message UUID>`，表示**第一个被排除的位置**（不含该条本身）。
- 返回**比该位置更新**的消息，按 `(created_at, id)` 升序，最多 `limit` 条。
- `before` 和 `after` 同时传时返回 400。
- 都不传时维持现有行为（返回最近 `limit` 条）。

实现上是把比较符和排序方向翻一下：

```python
if after is not None:
    statement = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == str(session_id))
        .where(tuple_(ChatMessage.created_at, ChatMessage.id) > tuple_(after[0], after[1]))
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        .limit(limit)
    )
    return list(result.scalars().all())   # 已是升序，不再 reversed
```

排序严格沿用技术设计里强制的 Message Position `(created_at, id)`，与 `before`、未读水位、Compact watermark 保持同一套顺序。

### 补充：前端如何判断是否追平

`after` 一次最多返回 `limit` 条。前端需要知道「还有没有更多」，否则无法安全地停止补拉。两种方案任选：

- **A（推荐，零成本）**：约定「返回条数 == limit 即可能还有更多」，前端拿最后一条的 cursor 继续拉，直到返回条数 < limit。无需改响应结构。
- **B**：响应改为带 envelope 的 `{ messages: [...], has_more: bool }`。更明确，但会改动现有响应结构，也影响 `before` 那条路径。

前端按 **A** 实现，除非后端倾向 B。

---

## 已闭环 2：Group WebSocket 实时推送

### 已落地端点

```
WS /ws/group/{group_id}?token=<JWT>
```

- 认证沿用现有 WS 风格：JWT 走 `token` query param。
- 鉴权：连接时校验该用户是当前群内 `removed_at IS NULL` 的成员；不是则拒绝。
- 服务端每 30 秒重新校验群和成员资格；查询失败使用 `4002`，群已删除或成员被移出使用 `4003` 主动关闭。前端把这两个 code 视为不再自动重连。

### 订阅范围：一个群一条连接，不是一个 session 一条

推送范围是**整个群**，事件里带 `session_id` 区分。

理由：群内多个 session 的未读红点需要实时更新。如果按 session 订阅，用户只能收到当前打开那个 session 的消息，其余 session 的未读还得靠轮询，等于实时链路没做完整。一个群一条连接，前端在群内切 session 不需要重连。

### 事件契约

v1 只需要一种事件：

```json
{
  "type": "message.created",
  "session_id": "<uuid>",
  "message": { /* 与 REST 的 GroupMessageOut 完全一致 */ }
}
```

`message` 直接复用 `GroupMessageOut`（`groups.py:113`）的 shape，**包括其中的 `cursor` 字段**。这一点很关键：前端收到推送即可用同一个 cursor 更新水位，与 `after` 补拉天然对齐，不需要两套位置语义。

**所有**写入群 session 的公开消息都走这一种事件，前端不需要分支处理：

- 人类成员发的消息
- Agent 被 @ 后的唤醒确认（ACK）
- Agent 的最终回复
- 任务触发 / 回调产生的群消息
- 任务规划失败的系统消息（`role = system`、`participant_id = null`）

这与 PRD「唤醒确认只使用普通群消息，不提供动画或独立状态」「Agent 中间过程不作为群消息」是一致的——群聊不需要 chunk / thinking / tool_call 这类流式事件。

### 投递语义

**至少一次（at-least-once）即可，不需要精确一次。**

前端按 `message.id` 去重，重复推送无害。这样后端不必为投递可靠性做额外保证——WS 推送尽力而为，真正的一致性由「重连后用 `after` 补拉」兜住。

### 可选事件（v1 可砍）

有了更好，没有也能跑（前端在对应操作后手动 refetch）：

- `session.created` / `session.deleted` / `session.updated`（标题、primary 变更）
- `member.joined` / `member.removed`
- `announcement.updated`

优先级远低于 `message.created`。

---

## 邀请成员的 `participant_id` 契约（2026-07-16 已闭环）

### 现状

`POST /api/groups/{group_id}/members` 只接受 `participant_id`（`groups.py:59`）：

```python
class InviteGroupMemberIn(BaseModel):
    participant_id: uuid.UUID
```

写接口继续只接受后端稳定身份 `participant_id`，不兼容
`(participant_type, ref_id)`，也不允许前端猜测或自行生成 Participant ID。

新增只读候选接口：

```text
GET /api/groups/{group_id}/member-candidates?participant_type=user|agent
```

它只对当前租户的 active human 群成员开放，并返回后端物化后的稳定字段：
`participant_id / participant_type / participant_ref_id / display_name / avatar_url`，
以及 Agent 的 `role_description` 或 User 的 `title`。

候选范围由后端统一决定：

- User 必须为同租户、active、已绑定平台账号，并排除当前 active 群成员；
- Agent 必须符合邀请人的现有可见性规则，同时为同租户、非 Private、未过期，且状态为 `creating / running / idle`；
- Participant 仍由后端通过并发安全的 `get_or_create_*_participant` 懒物化；
- `POST /members` 会重新执行目标有效性和 Agent 可见性检查，不能通过伪造或猜测 `participant_id` 绕过候选过滤；
- 被移出的成员可以重新成为候选，并由现有 membership 行恢复。

前端邀请 UI 只读取该候选接口，并原样提交：

```json
{"participant_id": "<backend participant UUID>"}
```

旧的 `{participant_type, ref_id}` 请求体已删除，不提供 fallback 或双协议兼容。
未绑定平台账号的第三方同步成员本版不进入可邀请候选，也不为其创建占位 Participant。

### Workspace / Memory 并发版本令牌

前端 FileBrowser adapter 现在保存从列表或打开文件时取得的 `version_token`，并把该
token 原样用于下一次 write/delete；保存前不再重新读取最新 token。成功写入后用响应
中的新 token 推进本地版本，冲突时保留原 token 和用户草稿，由后端 CAS 返回
`group_file_conflict`，不自动覆盖或盲目重试。

当前后端仍把 `expected_version_token = null` 解释为没有版本前置条件，因此两个客户端
同时创建同一路径文件时还不能表达 `require_absent`；该边界不影响已有文件的并发编辑
和删除保护，后续若要补齐需先扩展后端写入契约。

---

## 缺口 4：群 workspace 只支持文本，不能上传文件

### 现状

群 workspace 的四个接口全是文本读写：

```python
class GroupTextFileIn(BaseModel):
    content: str
    expected_version_token: str | None = None
```

`PUT /groups/{group_id}/workspace/file?path=...` 只接受 JSON body 里的字符串正文，**没有 multipart 上传接口**，也没有二进制文件下载接口。

### 问题

PRD 2.10 明确要求群 workspace 沉淀「用户在群中上传或明确分享到群里的文件和资料」。目前用户只能创建纯文本文件，无法上传 PDF、图片、表格等任何二进制资料。

前端已经接入了现有的 `FileBrowser` 组件（它原生支持 upload 和 download），但**上传能力只能关掉**，因为后端没有对应的口子。

### 需求

对齐 Agent workspace 已有的文件上传/下载能力，为 group scope 补上：

```
POST   /groups/{group_id}/workspace/upload?path=...   (multipart/form-data)
GET    /groups/{group_id}/workspace/download?path=...  (binary)
```

底层复用现有 storage / revision / lock 能力即可（技术设计 3.2 已经说明群 workspace 作为新增 group scope 复用这套机制）。接口一旦就绪，前端把 `FileBrowser` 的 `upload` / `downloadUrl` 两个能力打开即可，无需改动其他代码。

优先级低于缺口 3（邀请），但高于 WS——没有它，群 workspace 在产品上是残缺的。

### 附带：`modified_at` 的格式

workspace 列表和文本文件接口返回的 `modified_at` 是**浮点秒字符串**（如 `"1784010582.9989727"`），而群消息的 `created_at` 是 ISO 8601。前端已按浮点秒处理，但建议后端统一成 ISO 8601，避免每个消费方各自猜格式。

---

## 前端传输策略

前端实时层仍封装在单一 `useGroupRealtime` hook 内，上层页面不感知传输方式。正常状态只维持一条 Group 级 WebSocket；建连、重连和切换 session 时使用 `after` 补齐空窗。只有 socket 建连失败或断开期间才启动轮询；`onopen` 后立即清理轮询 timer，不再持续每 4 秒请求消息接口。

## 仍待后端产品补齐的点

按优先级：

1. **（PRD 未满足）** 群 workspace 的文件上传 / 下载接口。没有它，群 workspace 只能存纯文本。
2. `modified_at` 是否统一成 ISO 8601。

WebSocket 与 `after` 已按本文契约实现，不再属于待确认项。

## 2026-07-16 实现核对

| 契约 | 已落地事实 | 代码入口 | 回归证据 |
| --- | --- | --- | --- |
| `after` 与 `before` 互斥，正向结果升序 | API 按同一 `(created_at, id)` Message Position 处理两个方向，冲突返回 400；前端每页 50 条，满页继续追到少于 limit | `backend/app/api/groups.py`、`backend/app/services/group_message_service.py`、`frontend/src/services/groupApi.ts` | `test_list_messages_after_cursor_is_ascending_and_exclusive`、`test_list_messages_rejects_conflicting_cursors`、`groupApiContract.test.mjs` |
| 一个 Group 一条 WS，事件携带 session | `/ws/group/{group_id}` 使用 JWT，校验 active tenant membership，并每 30 秒复验；事件为完整 `message.created` | `backend/app/api/group_websocket.py`、`backend/app/services/group_realtime.py` | `test_group_realtime.py` |
| 推送只通知已提交事实 | 人类消息、规划失败消息、ACK、waiting/terminal 和 handoff 都在产品事务提交后发布；推送失败不回滚已提交消息，靠 cursor 补拉恢复 | `backend/app/api/groups.py`、`backend/app/services/agent_runtime/group_acknowledgement.py`、`backend/app/services/agent_runtime/checkpoint_side_effects.py` | `test_group_api.py`、`test_agent_runtime_checkpoint_side_effects.py`、`test_agent_runtime_group_scheduling.py` |
| 健康 WS 不持续轮询 | 前端断线期间才轮询，WebSocket `onopen` 清掉 timer 并立即 forward catch-up；事件和补拉都按 message ID 合并去重 | `frontend/src/hooks/useGroupRealtime.ts` | `groupRealtimeContract.test.mjs`、前端 production build |

## 附：2026-07-14 历史实测（已被 2026-07-16 实现取代）

在本地用该分支的后端（postgres + redis + backend，`alembic upgrade head` + `python -m app.scripts.setup_langgraph_checkpoints`）起了完整环境，逐条验证：

- 建群、建 session、发消息、拉历史、`before` 翻页、标记已读、群公告读写、群 workspace 目录与文本文件读写 —— **全部正常**。
- 消息列表返回**升序**，cursor 形如 `2026-07-14T06:16:04.577660+00:00|<uuid>`（**微秒精度**，客户端按毫秒比较会在同毫秒内错序）。
- `after=<cursor>` —— 当时被忽略；2026-07-16 已按上文正向游标契约修复。
- `POST /groups/{id}/members` 传 `(participant_type, ref_id)` —— **422 participant_id field required**。这是旧前端请求形状的历史实测；当前契约仍有意拒绝该形状，前端已改为先查候选 `participant_id` 再提交。
- `WS /ws/group/{id}` —— 当时 403；2026-07-16 已新增并完成鉴权、权限复验和回归。
- `GET /groups/{id}/agents/{agent_id}/memory` —— 当时为 **404 `Agent is not an active member of this group`**；邀请链路闭环后应以真实入群 Agent 重新回归。

另外发现一个与群聊无关、但会影响整个分支的问题：`GET /api/notifications/unread-count` **500**，报 `relation "notifications" does not exist` —— 分支上缺 notifications 表的迁移，全站通知栏都会报错。
