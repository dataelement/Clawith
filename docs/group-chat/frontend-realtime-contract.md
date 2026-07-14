# 群聊前端实时链路：接口补充需求

状态：待后端确认
关联：`technical-design.md` 第 11 章 API 草案、`backend/app/api/groups.py`

## 背景

群聊前端的数据链路确定为：

- **REST 发消息、拉历史** —— 复用已实现的 `/api/groups/...`。
- **WebSocket 实时推送** —— 群内新消息即时到达。
- **Cursor 断线补拉** —— 重连后按最后已知位置补齐断线期间的消息。
- **轮询只作临时兜底** —— WS 不可用时降级，不作为长期方案。

后端当前实现已经覆盖了 REST 发消息和历史分页，契约干净可用。但**实时推送和断线补拉这两环还接不上**，需要两处补充。

---

## 缺口 1：消息列表缺少 `after` 游标（正向补拉）

### 现状

`GET /api/groups/{group_id}/sessions/{session_id}/messages` 只支持 `before`（`backend/app/api/groups.py:788`）：

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

这是**向后翻页**：取比游标更旧的 `limit` 条，返回时按时间升序。适合「往上滚动加载历史」。

### 问题

断线补拉需要的是**反方向**：拿断线前最后一条消息的 cursor，取**比它更新**的消息。当前 API 做不到。

前端可以退而求其次——从头部反向翻页，一直翻到撞见已知 cursor 为止。但断线时间稍长就要连翻多页，且无法直接判断「是否已经追平」，逻辑绕且请求数不可控。

### 需求

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

## 缺口 2：群 WebSocket 端点不存在

### 现状

WebSocket 目前只有 `/ws/chat/{agent_id}`（`backend/app/api/websocket.py:168`），死绑单个 Agent，且在 session 查询里明确排除了群 session（`websocket.py:365`：`not ChatSession.is_group`）。

也就是说：**群消息目前没有任何推送通道**。别人发的消息、Agent 被 @ 后的唤醒确认、Agent 的最终回复、任务规划失败的系统消息，前端全都收不到。

### 需求：新增群推送端点

```
WS /ws/group/{group_id}?token=<JWT>
```

- 认证沿用现有 WS 风格：JWT 走 `token` query param。
- 鉴权：连接时校验该用户是当前群内 `removed_at IS NULL` 的成员；不是则拒绝。
- 群被删除、成员被移出时，服务端应主动关闭连接（可沿用现有 `4002`/`4003` 这类「不再重连」的 close code 约定，具体码值由后端定，前端按码值决定是否重连）。

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

## 缺口 3：邀请成员拿不到 `participant_id`（阻塞，优先级最高）

### 现状

`POST /api/groups/{group_id}/members` 只接受 `participant_id`（`groups.py:59`）：

```python
class InviteGroupMemberIn(BaseModel):
    participant_id: uuid.UUID
```

但**没有任何接口对外暴露 participant_id**。前端手上只有 `agent_id`（来自 `GET /agents/`）和 `user_id`（来自 `GET /users/`），无法转换成 `participant_id`。

更麻烦的是，Agent 的 participant 记录是**懒创建**的（`services/participant_identity.py:121` `get_or_create_agent_participant`）——一个从未参与过会话的 Agent，数据库里根本还没有对应的 participants 行。即使前端能查，也查不到。

**结论：邀请成员这条链路目前是断的，前端无法实现。** 这是唯一一个真正阻塞前端的缺口，优先级高于前两项。

### 需求（推荐方案：邀请接口改收业务 ID）

让 `POST /api/groups/{group_id}/members` 额外接受 `(participant_type, ref_id)`，服务端内部调 `get_or_create_participant` 解析（懒创建正好在这里发生）：

```python
class InviteGroupMemberIn(BaseModel):
    # 二选一
    participant_id: uuid.UUID | None = None
    participant_type: Literal["user", "agent"] | None = None
    ref_id: uuid.UUID | None = None
```

两者都不传或都传时返回 400。保留 `participant_id` 路径，兼容已有测试。

这样前端的选人 UI 直接用现成的 `GET /agents/` 和 `GET /users/` 构建候选列表，按 `(type, ref_id)` 发起邀请，**不需要后端新增任何查询接口**，改动面最小。

### 备选方案

新增 `GET /api/groups/{group_id}/member-candidates?q=`，返回可邀请对象及其 `participant_id`（内部按需创建 participants 行）。语义更贴 PRD 的「候选范围是邀请人可见的人和 Agent」，但后端要多写一个带可见性过滤的查询接口，且为了返回 ID 而提前创建 participant 行，副作用不干净。

**倾向推荐方案。** 若后端选备选方案，前端照样能接，告知即可。

### 附带确认

PRD 规定「Private Agent 不允许被邀请进群」「未绑定平台账号的第三方同步成员暂时不允许入群」。这两条过滤是在邀请接口内部校验（前端把不合法的候选也传上来，由后端拒绝），还是需要前端在候选列表里就过滤掉？前端倾向：**后端校验为准**，前端在列表里做友好提示即可，避免两边各写一套规则。

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

## 前端这边的兜底

在上述两项到位之前，前端把实时层封装成单一 hook，内部走**轮询**降级（拉 session 列表的 `unread_count` + 拉当前 session 的新消息）。上层组件对传输方式无感知。

`after` 和 `/ws/group/{group_id}` 就绪后，只需替换该 hook 的内部实现，不影响任何页面代码。

因此这两项**不阻塞前端开工**，但会直接影响群聊的实际体感（@ 完 Agent 后回复要等一个轮询周期才出现），建议尽早补上。

## 需要后端确认的点

按优先级：

1. **（阻塞）** 邀请成员接口能否接受 `(participant_type, ref_id)`？不解决的话，前端的邀请流程无法实现，群里除建群人外加不进任何人和 Agent，@ 唤醒和群 memory 都无从验证。
2. **（PRD 未满足）** 群 workspace 的文件上传 / 下载接口。没有它，群 workspace 只能存纯文本。
3. `/ws/group/{group_id}` 的路径、认证方式、close code 约定是否照上述执行？权限失效（群解散、成员被移出）时用哪个 close code——前端需要据此区分「可重连」与「不再重连」。
4. `after` 参数是否按上述语义实现？「是否还有更多」用方案 A（条数 == limit）还是 B（`has_more`）？
5. Private Agent / 未绑定第三方成员的入群过滤，由后端在邀请接口内校验，还是要前端在候选列表里预先过滤？
6. `modified_at` 是否统一成 ISO 8601？

3、4 不阻塞前端开工（前端先用轮询兜底），但直接影响群聊体感。1 是硬阻塞，2 是产品残缺。

## 附：以上结论均已在真实后端上实测

在本地用该分支的后端（postgres + redis + backend，`alembic upgrade head` + `python -m app.scripts.setup_langgraph_checkpoints`）起了完整环境，逐条验证：

- 建群、建 session、发消息、拉历史、`before` 翻页、标记已读、群公告读写、群 workspace 目录与文本文件读写 —— **全部正常**。
- 消息列表返回**升序**，cursor 形如 `2026-07-14T06:16:04.577660+00:00|<uuid>`（**微秒精度**，客户端按毫秒比较会在同毫秒内错序）。
- `after=<cursor>` —— **被忽略**，返回最新一页。
- `POST /groups/{id}/members` 传 `(participant_type, ref_id)` —— **422 participant_id field required**。
- `WS /ws/group/{id}` —— **403**（端点不存在）。
- `GET /groups/{id}/agents/{agent_id}/memory` —— **404 `Agent is not an active member of this group`**（因为加不进 Agent，见缺口 3）。

另外发现一个与群聊无关、但会影响整个分支的问题：`GET /api/notifications/unread-count` **500**，报 `relation "notifications" does not exist` —— 分支上缺 notifications 表的迁移，全站通知栏都会报错。
