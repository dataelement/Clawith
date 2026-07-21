# Clawith 统一聊天模型重构方案

本文单独记录原生群聊对现有聊天数据模型的重构方案，是统一聊天 Schema、强制迁移和 Single / Group 后端改造的开发基线。

本文锁定聊天表相关决策。群 workspace 暂时维持群级共享，不在本文讨论 session 级文件隔离。

## 1. 已确认方向

1. 不新增 `group_sessions`。
2. 不新增 `group_messages`。
3. 重构现有 `chat_sessions` 和 `chat_messages`，作为普通聊天、原生群聊、外部渠道聊天、A2A 和 trigger 的统一消息底座。
4. `groups` 和 `group_members` 继续保留，负责群元信息、成员关系、权限和生命周期。
5. 普通聊天和群聊复用统一的消息协议、分页、排序、未读结果和实时通信基础能力。
6. Phase 1 暂不新增通用 `chat_session_members` 表。
7. Phase 1 暂不把普通聊天和群聊的未读状态迁移到新的统一 read state 表。

目标关系：

```text
groups
  ├── group_members
  └── chat_sessions
        └── chat_messages
```

## 2. `chat_sessions`

### 2.1 目标字段

| 字段 | 说明 |
|-|-|
| `id` | Session 主键。 |
| `tenant_id` | 所属租户，用于统一列表和权限过滤。 |
| `session_type` | `direct` / `group` / `a2a` / `trigger`。 |
| `group_id` | 原生群 session 指向 `groups.id`，其他类型为空。 |
| `agent_id` | direct、外部渠道和 A2A 使用；原生群允许为空。 |
| `user_id` | direct 使用；group、A2A 和 trigger 允许为空。 |
| `created_by_participant_id` | 创建 session 的参与者；系统创建时可以为空。 |
| `title` | Session 标题。 |
| `source_channel` | `web` / `feishu` / `slack` 等，只表达来源渠道。 |
| `external_conv_id` | 第三方渠道的会话 ID。 |
| `peer_agent_id` | 兼容现有 A2A。 |
| `is_primary` | 在所属 direct 或 group scope 内是否为默认 session。 |
| `deleted_at` | 软删除时间。 |
| `created_at` | 创建时间。 |
| `updated_at` | 元信息更新时间。 |
| `last_message_at` | 最近公开消息时间。 |

### 2.2 类型约束

#### direct

```text
session_type = direct
group_id IS NULL
agent_id IS NOT NULL
user_id IS NOT NULL
```

#### group

原生群聊：

```text
session_type = group
group_id IS NOT NULL
agent_id IS NULL
user_id IS NULL
```

尚未映射成 Clawith 原生群的外部渠道群：

```text
session_type = group
group_id IS NULL
external_conv_id IS NOT NULL
```

#### a2a

```text
session_type = a2a
group_id IS NULL
agent_id IS NOT NULL
peer_agent_id IS NOT NULL
```

#### trigger

`trigger` session 不作为普通用户聊天出现在默认会话列表中。

### 2.3 现有字段处理

1. `is_group` 降级为兼容字段，新业务逻辑统一使用 `session_type`。
2. 原生群名称统一读取 `groups.name`，不写入 `chat_sessions.group_name`。
3. `group_name` 在兼容期只服务尚未映射为原生群的外部渠道群。
4. `source_channel` 只表达消息渠道，不再承担 direct、group、A2A 等会话类型判断。
5. 现有 `chat_sessions.participant_id` 不再承担含义不明确的“session 对端”职责，完成迁移后删除；`chat_messages.participant_id` 继续表示实际发送者。
6. `peer_agent_id` 暂时保留，后续再决定是否通过通用参与者关系替代。

## 3. `chat_messages`

### 3.1 目标字段

| 字段 | 说明 |
|-|-|
| `id` | 消息主键。 |
| `conversation_id` | 保存 `str(chat_sessions.id)`，继续作为消息所属 session 的关联字段。 |
| `participant_id` | 实际发送者，可以是人或 Agent；系统消息可以为空。 |
| `role` | `user` / `assistant` / `system` / `tool_call`。 |
| `content` | 消息正文。 |
| `mentions` | 当前消息解析出的结构化 mention 列表。 |
| `thinking` | 兼容现有模型思考内容，但不对群成员公开。 |
| `created_at` | 创建时间。 |

`chat_messages` 不保存冗余 `group_id`。消息所属群统一通过以下关系确定：

```text
chat_messages.conversation_id = str(chat_sessions.id)
-> chat_sessions.group_id
```

Phase 1 不新增 UUID `session_id`，也不在群聊开发中同时治理现有 `conversation_id` 的历史设计。后续如需建立真实外键，再作为独立的聊天模型治理任务处理。

### 3.1.1 Message Position

v1 不新增 `message_seq`。同一 session 内统一使用 `(created_at, id)` 作为权威 Message Position，并按 `created_at ASC, id ASC` 排序；UUID `id` 只作为同时间戳下的稳定第二排序键，不单独表达先后。

以下能力必须复用同一 Message Position 语义：

1. 消息列表和 Cursor 分页，Cursor 同时编码 `created_at + id`。
2. 最近消息窗口和“最近 20 条”选择。
3. 群未读位置；`last_read_message_id` 需要解析到对应消息的 `(created_at, id)`。
4. Session Compact watermark；`covered_through_message_id` 需要解析到对应消息的 `(created_at, id)`。
5. 同一 Agent 收到多条 @ 后的排队顺序。

禁止按 UUID 大小、只按 `created_at`，或由不同入口自行定义消息顺序。

### 3.1.2 Primary Session 生命周期

direct 和 group 使用同一套 primary 语义：primary 只是在所属会话 scope 内没有明确 `session_id` 时的默认路由，不是不可删除的系统 Session。

会话 scope 定义为：

```text
direct: (tenant_id, agent_id, user_id)
group:  group_id
```

统一规则：

1. 每个 scope 最多一个未删除 primary，但允许一个都没有。
2. 删除非 primary session 时只执行 soft delete。
3. 删除 primary session 时，在同一事务内 soft delete 当前 primary，并从该 scope 剩余未删除 Session 中自动选举 replacement。
4. replacement 固定按 `last_message_at DESC NULLS LAST, created_at DESC, id DESC` 选择，不能由不同入口各自决定。
5. 没有剩余 Session 时允许 scope 处于无 primary 状态；后续创建的第一个 Session 自动成为 primary。
6. primary 删除、replacement 选举和新 Session 的首次 primary 设置必须持有 scope 级事务锁。group 锁定 `groups` 行；direct 使用由 `(tenant_id, agent_id, user_id)` 生成的 PostgreSQL transaction advisory lock。
7. partial unique index 是最终并发保护；发生唯一冲突时回滚事务并重新读取当前 primary，不覆盖其他事务的选择。

无 primary 时是否允许系统创建 Session 由 session type 的产品权限决定，不改变上述生命周期：direct 可以在用户进入会话或 Agent 合法主动触达时由系统创建；group v1 仍只允许人类群成员创建，异步回写不得自动创建群 Session。

### 3.2 发送者与角色

`participant_id` 表达真实发送者，`role` 表达消息进入模型时的角色，两者不能互相替代。

新的群消息必须把 `participant_id` 写为真实发送者。现有普通聊天历史消息不在 Phase 1 中做全量身份修复。

Agent 公开回复：

```text
participant_id = Agent 对应的 participant_id
role = assistant
```

人类消息：

```text
participant_id = 用户对应的 participant_id
role = user
```

系统消息：

```text
participant_id = null
role = system
```

Agent 的 thinking、工具日志、trace 和未公开的 A2A 过程不作为普通群消息展示。

### 3.3 mentions

`mentions` 保存解析后的稳定身份，不根据纯文本名字猜测成员。

示例：

```json
[
  {
    "participant_id": "uuid",
    "participant_type": "agent",
    "display_name": "Research Agent"
  }
]
```

## 4. 参与者关系

产品和技术语义统一为：

1. 普通聊天是一个人类 participant 和一个 Agent participant 参与的会话。
2. 群聊是多个 participant 参与的会话。
3. A2A 是两个 Agent participant 参与的会话。

Phase 1 不新增通用 `chat_session_members`：

1. 普通聊天双方暂时继续通过 `user_id + agent_id` 表示。
2. 群成员继续通过 `group_members` 表示。
3. A2A 暂时继续通过 `agent_id + peer_agent_id` 表示。
4. 先完成 session 和 message 主模型统一，再单独评估成员关系的统一迁移。

## 5. 未读状态

Phase 1 保持现状：

1. 普通聊天继续使用 `chat_sessions.last_read_at_by_user`。
2. 群聊继续使用 `group_members.session_read_state`。
3. 前端统一消费后端计算出的 `unread_count`，不感知底层差异。

后续如需统一，再新增：

```text
chat_session_read_states
  session_id
  participant_id
  last_read_message_id
  last_read_at
```

## 6. API 和前端

普通聊天和群聊使用统一会话列表协议：

```text
GET /chat/sessions
```

统一支持：

1. 按 `last_message_at` 排序。
2. Cursor 分页。
3. 按 `session_type` 过滤。
4. 返回未读数、标题、头像和最近消息。
5. direct、group 和 A2A 使用统一消息分页结构。

产品可以提供“全部 / 单聊 / 群聊”等筛选，但不拆成两套底层列表协议。

群领域 API 可以继续提供：

```text
GET /groups/{group_id}/sessions
```

该接口表达群业务语义，底层仍然查询 `chat_sessions`。

## 7. 数据迁移方案

Phase 1 使用一次维护窗口内的强制 Alembic 迁移批次完成群领域表创建和现有聊天表扩展。批次可以由多个连续 revision 组成，以保持每个 revision 的职责单一；这不代表采用长期 Expand / Dual-write / Contract，也不在群聊开发中同时治理 `conversation_id`、历史 `participant_id` 或通用 read state。

当前分支 Alembic 只有一个 head：`add_title_to_agent_focus_items`。新增迁移从该 head 继续，并遵循 `backend/ALEMBIC_GUIDELINES.md` 的时间戳文件命名规则。

### 7.1 强制迁移与发布边界

统一聊天模型的 Schema 切换采用一次维护窗口内的强制迁移，不采用 Expand / Dual-write / Contract，也不允许旧后端与新 Schema、或新后端与旧 Schema 混合运行。

开发顺序是先完成并评审迁移文件，再实现依赖新 Schema 的 Single Chat 后端；生产环境只有在迁移和新 Single Chat 后端都已验证可发布后，才在同一个维护窗口真正执行迁移与应用切换。

实施顺序固定为：

1. 停止所有可能写入聊天和 Runtime 业务表的进程，包括 API、WebSocket、渠道 Consumer、Trigger、Heartbeat 和后台 Worker。
2. 执行迁移前检查并完成数据库备份，确认当前 Alembic head、历史数据回填条件和约束冲突数量。
3. 运行本章定义的强制迁移批次：先创建群领域表和 nullable 新字段，再完成历史回填，最后增加 `NOT NULL` / `CHECK` / 新索引并删除旧 primary 索引。
4. 在服务仍关闭的状态下执行迁移后校验，确认行数、外键、`session_type` 分类、primary session 唯一性和关键查询结果正确。
5. 在同一维护窗口发布只使用新 Schema 的后端，完成 Single Chat 后端冒烟后再开放写流量；Group Chat 后端能力可按后续开发阶段启用。
6. Single 与 Group 后端完成后先执行整体验证和旧循环清理，再统一更新前端；前端发布时间不改变数据库强制迁移边界。

强制切换前必须移除或严格关闭生产启动路径中的 `Base.metadata.create_all()`；生产 Schema 只能由经过评审的 Alembic revision 推进，不能由当前 ORM metadata 在应用启动时隐式补表或补列。

迁移或校验失败时必须保持服务关闭并回滚迁移或从备份恢复，不允许在半迁移状态重新开放流量。迁移完成后可以暂时保留 `is_group`、`group_name`、`participant_id`、`peer_agent_id` 等兼容字段，但新代码必须以 `session_type` 及本方案定义的新字段为准；这些兼容字段不构成双写 Schema。

### 7.2 创建群领域表

迁移批次的第一个 revision 先创建：

```text
groups
group_members
```

必须先创建 `groups`，后续 `chat_sessions.group_id` 才能建立外键。

### 7.3 扩展 `chat_sessions`

增加：

```text
tenant_id
session_type
group_id
created_by_participant_id
deleted_at
updated_at
```

同时执行：

1. `agent_id` 改为 nullable，允许原生群 session 不绑定单个 Agent。
2. `user_id` 改为 nullable，允许 group、A2A 和 trigger 复用同一张表。
3. `group_id` 指向 `groups.id` 并建立普通索引。
4. `tenant_id` 指向 `tenants.id` 并建立普通索引。
5. `created_by_participant_id` 指向 `participants.id`。
6. `session_type` 使用 `VARCHAR(20) + CHECK`，允许 `direct` / `group` / `a2a` / `trigger`。
7. 保留现有 `is_group`、`group_name`、`participant_id`、`peer_agent_id` 等兼容字段。

### 7.4 扩展 `chat_messages`

只增加：

```text
mentions JSONB NOT NULL DEFAULT '[]'
```

同时把 `agent_id` 和 `user_id` 改为 nullable，使原生群消息不需要伪造固定 Agent 或用户。

Phase 1 继续使用：

```text
conversation_id = str(chat_session.id)
participant_id = 当前消息的真实发送者
```

不新增 `session_id`，不新增 `sender_participant_id`，不对普通聊天历史消息做全量发送者修复。

### 7.5 历史 session 回填

`session_type` 按以下顺序回填：

```text
source_channel = agent   -> a2a
source_channel = trigger -> trigger
is_group = true          -> group
其他                      -> direct
```

必须先判断 `agent` 和 `trigger`，再判断 `is_group`。

`tenant_id` 从现有 session 的 Agent 回填：

```sql
UPDATE chat_sessions cs
SET tenant_id = a.tenant_id
FROM agents a
WHERE cs.agent_id = a.id
  AND cs.tenant_id IS NULL;
```

新建原生群 session 直接写入 `groups.tenant_id`。

由于历史 `agents.tenant_id` 和 `users.tenant_id` 都可能为空，迁移前必须统计 Agent tenant 缺失、User tenant 缺失以及双方 tenant 不一致的 direct session。只有来源唯一且一致时才允许回填；仍为空或存在冲突时迁移必须 fail fast，禁止写入任意默认 tenant。

`created_by_participant_id` 的历史数据只做能够可靠确定的回填：

1. direct 和普通外部渠道会话使用 `user_id` 对应的 user participant。
2. a2a 优先使用现有 `chat_sessions.participant_id`。
3. trigger 使用执行 Agent 对应的 agent participant。
4. 无法可靠确定的历史记录允许为空，不根据展示名猜测。

### 7.6 primary session 索引

删除依赖 `source_channel + is_group` 的旧 primary 唯一索引，建立两个新索引。

direct primary：

```text
unique(tenant_id, agent_id, user_id)
where session_type = direct
  and is_primary = true
  and deleted_at is null
```

原生 group primary：

```text
unique(group_id)
where session_type = group
  and group_id is not null
  and is_primary = true
  and deleted_at is null
```

### 7.7 新数据写入规则

新的普通聊天 session 必须写入 `tenant_id` 和 `session_type = direct`。

如果对应 direct scope 当前没有未删除 primary，新建的第一个 Session 同时写入 `is_primary = true`；否则写入 `false`。该判断遵循 3.1.2 的 scope 锁和唯一约束。

新的原生群 session 必须写入：

```text
tenant_id = groups.tenant_id
session_type = group
group_id = groups.id
agent_id = null
user_id = null
created_by_participant_id = 当前创建者
```

新的群消息必须写入：

```text
conversation_id = str(chat_session.id)
participant_id = 当前消息真实发送者
agent_id = null
user_id = null
mentions = 结构化 mention 列表
```

### 7.8 暂不处理

以下内容不属于群聊 Phase 1：

1. 把 `conversation_id` 迁移成 UUID 外键 `session_id`。
2. 把 `participant_id` 迁移成新字段 `sender_participant_id`。
3. 修复所有普通聊天历史消息的发送者身份。
4. 删除 `is_group`、`group_name` 等兼容字段。
5. 新增通用 `chat_session_members`。
6. 新增通用 `chat_session_read_states`。
7. 对全部渠道消息写入入口进行聊天模型清债。

这些内容后续作为独立的聊天模型治理任务处理，不阻塞原生群聊落地。

### 7.9 回滚边界

1. 尚未产生原生群数据时，可以回滚新增字段、索引和群领域表。
2. 已经产生原生群数据后，不允许直接 downgrade 删除 `groups`、`group_members` 或 `chat_sessions.group_id`。
3. 强制迁移完成并开放写流量后，不支持直接回滚到依赖旧 Schema 的应用版本；需要先关闭服务，并使用经过验证的前向修复或数据库恢复方案。

## 8. 当前讨论状态

已确认：

1. 复用并重构现有聊天表，不建立群聊专用 session/message 表。
2. 保留 `groups` 和 `group_members` 作为群领域模型。
3. workspace 暂不做 session 级上下文隔离。
4. Phase 1 暂不新增通用 session member 表和 read state 表。
5. Phase 1 使用一次维护窗口内的强制 Alembic 迁移批次完成群领域表创建、聊天表扩展和必要历史 session 回填；批次内允许多个连续 revision，不引入长期双写。
6. Phase 1 继续使用 `chat_messages.conversation_id` 和 `participant_id`，不引入双写迁移。
7. 历史消息模型清债作为后续独立任务，不阻塞原生群聊。
8. direct、group、A2A 和 trigger session 复用统一 `session_context_states`；group session 的共享 Context State 使用 `session_id = chat_sessions.id` 且 `agent_id = null`。
9. 群聊共享 Session Compact 使用显式配置的独立 Compact 模型，阈值按群内有效 Agent 模型的最小有效输入预算计算。
10. v1 不新增 `message_seq`，统一使用 `(created_at, id)` 作为 Message Position。
11. 统一聊天 Schema 在维护窗口内强制迁移，不做长期双写，也不允许新旧后端混跑；整体功能开发按迁移、Single Chat 后端、Group Chat 后端、后端整体验证与旧循环清理、前端的顺序分阶段推进。

后续讨论应继续在本文增量记录，并在全部关键问题收敛后再合并回总技术设计。
