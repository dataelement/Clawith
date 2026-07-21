# Clawith 群聊终版技术设计

> 状态：开发基线（终版）

本文基于线上 PRD `Clawith 群聊 v1 PRD v3`（revision 755）生成，用于承接 PRD 中标记为“放到技术文档中”的建模、上下文、执行和存储细节。

PRD 是产品规则来源；本文只定义技术实现边界。PRD 中仍保留删除线的历史内容，技术设计默认以未删除的当前正文为准。聊天 session/message schema 以 `chat-model-refactor.md` 为规范来源；Runtime、Session Context 和压缩机制以 `docs/single-agent-runtime/` 终版方案为公共底座。

## 1. 设计原则

1. 群聊首先是消息系统，可以触发 Agent 执行，但群消息不等同于 Agent 执行日志。
2. 表存业务对象、关系、状态和生命周期；文件存正文、产物和可由 ID 推导的内容。
3. 群是原生业务对象，使用 `groups` 和 `group_members` 表达群领域；会话和消息统一复用 `chat_sessions` 与 `chat_messages`。
4. 人和 Agent 统一通过 `participants` 表进入群成员、消息发送者、创建者等关系。
5. 群公告、群 workspace、群 memory 都按固定路径由 `group_id` 推导，不在业务表里保存正文或 storage key。
6. 群 session 隔离消息上下文，不隔离群 workspace。
7. Agent 被 @ 后的群内回复写回当前群 session；Agent 内部过程、工具日志、trace 不写成普通群消息。

原生群聊的中心是：

```text
groups
  ├── group_members
  └── chat_sessions (session_type = group)
        └── chat_messages
```

## 2. 数据模型

### 2.1 groups

`groups` 表表示一个长期群聊对象。

| 字段 | 说明 |
|-|-|
| `id` | 群 ID，主键。 |
| `tenant_id` | 所属租户，指向 `tenants.id`。 |
| `name` | 群名称。 |
| `description` | 群介绍。 |
| `created_by_participant_id` | 创建群的参与者，指向 `participants.id`。 |
| `deleted_at` | 解散或删除时间。 |
| `created_at` | 创建时间。 |
| `updated_at` | 更新时间。 |

建表规则：

1. `groups` 只保存群元信息、租户归属、创建者和生命周期状态。
2. v1 产品入口只允许人类成员创建群，但 schema 使用 `created_by_participant_id`，为后续 Agent 主动创建群预留空间。
3. 群名不做唯一约束，同名群通过 `id` 区分。
4. 群公告正文不进表。
5. 群 workspace key 不进表。
6. 群 memory 正文不进表。

固定路径：

```text
groups/{group_id}/system/announcement.md
groups/{group_id}/workspace
groups/{group_id}/agents/{agent_id}/memory/memory.md
```

### 2.2 group_members

`group_members` 表表示“某个 participant 在某个 group 里的成员身份”。

| 字段 | 说明 |
|-|-|
| `id` | 成员关系 ID。 |
| `group_id` | 所属群。 |
| `participant_id` | 指向 `participants.id`，可以是人或 Agent。 |
| `role` | `manager` / `member`。 |
| `joined_at` | 加入时间。 |
| `removed_at` | 移出时间。 |
| `session_read_state` | JSON，记录该成员在各群 session 的已读位置。 |

规则：

1. 所有群成员关系存在同一张 `group_members` 表，用 `group_id` 区分不同群。
2. 不直接存 `user_id` 或 `agent_id`，通过 `participants.type + participants.ref_id` 找到真实用户或 Agent。
3. 保留独立的 `id` 作为成员关系 ID，不使用 `(group_id, participant_id)` 作为复合主键。
4. 建唯一约束 `unique(group_id, participant_id)`，防止同一参与者重复加入同一个群。
5. `group_members` 不保存 `status` 字段，使用 `removed_at IS NULL` 表示当前仍在群内。
6. 移出成员时不删除对应 `group_members` 记录，写入 `removed_at`。
7. 再次邀请时复用原 membership 记录，更新 `joined_at`，并清空 `removed_at`。
8. `removed_at` 只表达当前 membership 是否已被移出；完整移出历史仍以审计日志为准。
9. 邀请来源不放在成员表。谁邀请了谁、从什么入口邀请，进入审计日志。
10. `joined_at` 记录当前这次成员关系的加入时间。
11. 建群人默认是 `manager`。
12. 群内至少保留一个 `manager`。
13. v1 不提供将其他成员设为 `manager`、取消 `manager` 或转让群管理的产品入口。
14. `member` 不能移出成员。
15. “至少一个 manager”约束只适用于群继续存在时的成员变更操作，不适用于解散群。
16. 解散群只校验操作者是当前群的 `manager`；校验通过后进入群删除流程，不再检查删除后是否仍有 manager。
17. 用户账号或 Agent 自身是否可用由主体自身状态判断，不进入 `group_members`。
18. v1 不单独建立 read state 表，群 session 未读状态先保存在 `session_read_state` JSON 中。
19. 邀请候选可以来自用户、Agent 或第三方组织成员；执行入群时必须解析为有效 `participant_id`，只有已解析为 `participants` 记录的对象才能写入 `group_members`。

保留独立 `id` 的原因是：API 操作、审计记录以及后续成员级设置可以稳定引用这条 membership 关系。

`session_read_state` 示例：

```json
{
  "chat_session_id": {
    "last_read_message_id": "chat_message_id",
    "last_read_at": "2026-07-03T10:30:00Z"
  }
}
```

该 JSON 只用于 v1 简化实现：当前用户查看群 session 列表、进入 session 标记已读、计算自己的未读。后续如果需要复杂通知推送、全员未读统计、按 session 查询未读成员或更强并发控制，再拆成独立 read state 表。

### 2.3 统一 `chat_sessions`

群 session 不建立专用表，而是使用统一聊天模型中的 `chat_sessions`：

```text
session_type = group
group_id = groups.id
agent_id = null
user_id = null
created_by_participant_id = 当前创建者
```

规则：

1. 一个群可以关联多个 `chat_sessions`。
2. 每个群的第一个 session 自动成为 primary session；同群最多一个未删除 primary session。
3. 部分唯一约束为 `unique(group_id) where session_type = 'group' and is_primary = true and deleted_at is null`。
4. `group_id`、`session_type` 和租户归属由服务端写入并校验。
5. 群 session 不属于单个 Agent 或用户，因此 `agent_id`、`user_id` 为空。
6. 删除、标题、primary 和 `last_message_at` 均复用 `chat_sessions` 的统一字段与列表协议。
7. `source_channel` 只表达来源渠道；原生群使用 `web`，外部渠道群使用对应渠道并通过 `external_conv_id` 关联。
8. `is_group`、`group_name` 等旧字段仅用于迁移兼容，新逻辑以 `session_type + group_id` 为准。
9. 完整字段、约束和迁移规则以 `chat-model-refactor.md` 为准，本文不维护第二份聊天 schema。

### 2.4 统一 `chat_messages`

群公开消息写入统一 `chat_messages`：

```text
conversation_id = str(chat_sessions.id)
participant_id = 实际发送者
role = user | assistant | system
content = 公开消息正文
mentions = 结构化 mention 列表
```

规则：

1. 群消息只保存群成员可见的公开消息；Agent 中间过程、工具日志和 trace 不写成普通聊天消息。
2. `participant_id` 表达真实发送者，`role` 表达进入模型时的角色，两者不能互相替代。
3. `chat_messages` 不冗余保存 `group_id`；通过 `conversation_id -> chat_sessions.group_id` 确定所属群并执行权限过滤。
4. 写入前必须校验目标 `chat_sessions.session_type = group`、所属群未删除、发送者仍是群成员。
5. `mentions` 保存稳定 `participant_id`、participant 类型和展示名，用于展示、提醒、Agent 唤醒和上下文身份引用。
6. Agent 最终公开回复使用其 participant ID 和 `role = assistant`；未公开 A2A 结论不进入群消息。
7. Phase 1 继续使用 `conversation_id`，不在群聊改造中同时引入新的消息外键。
8. v1 不新增 `message_seq`；统一按 `(created_at, id)` 表达 Session 内 Message Position，分页、未读、Compact watermark、最近消息和 @ 队列不得各自定义顺序。

### 2.5 复用统一 Agent Runtime

群聊不新增 `group_agent_runs` 或 `group_agent_run_events`，每次 Agent 被 @ 后复用统一 `agent_runs`、`agent_run_commands`、`agent_run_events`、LangGraph Thread 和 Tool Execution Ledger。LangGraph checkpoint 是执行生命周期唯一事实源；群聊只读取统一产品投影。

规则：

1. Agent 被 @ 后创建或排队一个统一 `agent_run`，`session_id` 指向当前群 `chat_sessions.id`，`agent_id` 指向本次被唤醒 Agent。
2. 群聊数据结构只保存用户可见的公开消息和群相关元数据。
3. Agent 执行完成后，只有它决定公开发送到群里的最终回复写入 `chat_messages`。
4. 有用的中间产物通过群 workspace 文件持久化，不通过群消息表或群执行表保存。
5. 多个被 @ Agent 各自拥有独立 Run / Thread，但读取同一个版本化群 Session Context Snapshot。
6. 排队、取消、重试、interrupt/resume、Run Compact 和副作用工具幂等均复用统一 Runtime，不在 group 模块复制 tool loop。
7. 群聊业务层不新增 group-scoped run 或 log 表；群 API 只查询统一 Runtime 的授权投影。

### 2.6 v1 不新增群审计表

v1 不新增 `group_audit_logs` 或其他 group-scoped 审计表。

规则：

1. 群创建、群信息修改、成员邀请、成员移除、session 创建、session 删除、解散群等操作，复用现有 `audit_logs`。
2. `audit_logs.details` 中记录 `group_id`、`chat_session_id`、`group_member_id`、目标 `participant_id` 等群上下文。
3. 群成员关系、群 session、群消息等业务当前状态仍写入对应业务表。
4. 操作来源、操作者、变更原因和审计上下文不进入 `groups` / `group_members` / `chat_sessions` / `chat_messages` 主表。
5. 如果后续有独立群审计查询、合规留存或权限隔离要求，再单独设计群审计表。

## 3. 文件和固定路径

### 3.1 群公告

群公告是固定路径文件：

```text
groups/{group_id}/system/announcement.md
```

规则：

1. `groups` 表不保存 `announcement_md`。
2. Agent 在群内被 @ 时，context builder 按固定路径读取群公告并注入。
3. 群公告全文可以很长，但注入上下文时有长度上限。
4. 超出注入上限的部分不自动进入本轮上下文。
5. Agent 需要更多公告内容时，通过群公告读取工具按需读取。

### 3.2 群 workspace

群 workspace 是固定路径目录：

```text
groups/{group_id}/workspace
```

规则：

1. `groups` 表不保存 `workspace_key`。
2. 群 workspace 属于 group，不属于 group session。
3. 同一个群下的多个 session 共享同一个群 workspace。
4. 群 workspace 作为新增的 group scope 复用现有 workspace revision / lock 能力，不迁移现有 Agent workspace 数据。

workspace scope 规则：

| 字段 | 说明 |
|-|-|
| `scope_type` | `agent` / `group`。 |
| `scope_id` | `agent_id` 或 `group_id`。 |
| `path` | scope 内的文件路径。 |
| `session_id` | 触发文件变更的 `chat_sessions.id`。 |

新增 group scope 后，`workspace_file_revisions` 和 `workspace_edit_locks` 支持两类 scope：

1. 现有 Agent workspace 继续使用 agent scope，历史数据、路径、API 和锁语义保持不变。
2. 新增群 workspace 使用 group scope，按 `scope_type = group`、`scope_id = group_id`、`path` 记录版本和编辑锁。
3. 新增 group scope 不要求迁移现有 Agent workspace 数据。

### 3.3 群 memory

群 memory 是 Agent 针对某个群维护的固定路径文件：

```text
groups/{group_id}/agents/{agent_id}/memory/memory.md
```

规则：

1. v1 不建 `group_agent_memories` 正文表。
2. 群 memory 正文以文件为唯一真实来源。
3. 产品语义上，群 memory 属于群 workspace；技术实现上，它是 group scope 下的系统文件，不作为普通 workspace 文件直接管理。
4. Agent 在群内被 @ 时，加载该 Agent 对应这个群的 memory。
5. Agent 在非当前群上下文中被唤醒时，不加载当前群 memory。
6. Agent 在其他群中被唤醒时，不加载当前群 memory。
7. Agent 可以读取群内其他 Agent 针对该群的 memory。
8. Agent 只能写自己的群 memory，不能修改其他 Agent 的群 memory。
9. 人类用户可以读、写、删除群内所有 Agent 的群 memory。
10. Agent 自身 memory 不会自动从群 memory 中学习内容。

### 3.4 文件 API 边界

群公告、群 memory 和群 workspace 继续文件化；session summary 统一保存在 `session_context_states`。对前端暴露的 API 仍保持 group 语义边界。

规则：

1. 前端和业务层不直接把 `groups/{group_id}/...` 物理路径当作业务 contract。
2. 群公告和群 memory 走 group-scoped wrapper API 并映射到固定路径；session summary wrapper API 查询 `session_context_states`。
3. 普通群 workspace 文件走 group workspace 文件 API，底层复用现有 storage、revision、lock 能力。
4. group API 负责校验群成员、群管理、Agent memory 读写权限等群语义。
5. 底层 file/storage API 只负责文件读写、版本、锁、下载，不承载群成员权限。
6. 固定系统文件不通过普通 workspace path 随意写入，避免绕过业务权限和注入规则。

API 形态：

```text
GET /groups/{group_id}/announcement
PUT /groups/{group_id}/announcement

GET /groups/{group_id}/agents/{agent_id}/memory
PUT /groups/{group_id}/agents/{agent_id}/memory
DELETE /groups/{group_id}/agents/{agent_id}/memory

GET /groups/{group_id}/sessions/{session_id}/summary

GET    /groups/{group_id}/workspace?path=...
GET    /groups/{group_id}/workspace/file?path=...
PUT    /groups/{group_id}/workspace/file?path=...
DELETE /groups/{group_id}/workspace/file?path=...
```

## 4. 唤醒、任务规划和 A2A

### 4.1 @ 唤醒

发送群消息的处理流程：

1. 保存用户群消息。
2. 解析当前消息中的 @。
3. 过滤出当前群内 `removed_at IS NULL` 且 Agent 自身可用的 Agent 成员。
4. 如果只 @ 一个 Agent，直接触发该 Agent 执行。
5. 如果同时 @ 多个 Agent，先进入任务规划阶段。
6. Run Registry 与 `start` Command 已可靠提交后，以该 Agent 的群成员身份向当前群 session 写入一条普通群消息，确认已被唤醒。
7. 被唤醒 Agent 执行完成后，最终回复写回当前群 session。

唤醒确认不定义单独的事件、消息类型或执行状态，也不提供动画；它与其他 Agent 回复一样保存和展示为普通群消息。

公开 ACK 与终态闭环规则：

1. ACK 只能在对应 Run Registry 与 `start` Command 提交成功后异步写入；Runtime 输入尚未持久化时不得提前确认。
2. 已公开 ACK 的 Run 最终必须产生一种用户可见结果：`completed` 写最终回复；`waiting_user` 写明确问题或确认请求；`failed` 写脱敏失败消息；`cancelled` 写取消消息。
3. `waiting_external` 和 `waiting_agent` 不是终态，不重复发送 ACK；恢复后继续走同一 Run 的最终闭环。
4. 公开失败消息不得包含异常堆栈、原始工具参数、凭据、内部路径或调试日志，只保留安全错误分类和可执行的下一步建议。
5. Planning Run 在业务子 Run 创建前失败时，以系统身份写一条规划失败消息；已经 ACK 的子 Run 各自负责自己的终态消息。
6. 依赖前置失败而从未启动的步骤不发送伪造 Agent ACK，由 Planning Run 的最终协作失败消息统一说明。
7. 前台 Run 的群、原 Session 已删除或 Agent 已移出群时，不向 replacement primary 或其他 Session 改写消息；记录 `delivery_status = failed` 和稳定 `error_code`。独立后台 Run 的 primary fallback 按 4.4 执行。
8. ACK、每次 `waiting_user` 请求和 terminal 交付分别使用 `run:{run_id}:ack`、`run:{run_id}:waiting:{interrupt_id}`、`run:{run_id}:terminal:{lifecycle_status}` 作为幂等键。
9. ChatMessage、`delivery_status` 与对应 delivery event 必须在同一 PostgreSQL 事务写入；执行生命周期事件由 Runtime Projector 从 checkpoint 派生。

群消息持久化和首次 Run 派发必须满足以下一致性规则：

1. 不允许在群消息事务提交后，仅依赖内存任务或一次性 `start_run()` 调用创建 Run；否则进程退出会留下“消息存在但永远未唤醒”的状态。
2. 单 Agent mention：在同一个 PostgreSQL 事务中写入 `chat_messages`、目标 Run Registry 和 `start` Command。
3. 多 Agent mention：在同一个事务中写入 `chat_messages`、系统 Planning Run Registry 和 `start` Command；规划期间不持有消息事务。
4. Planning Graph 将计划保存进 checkpoint 后作为本轮编排根 Run，根据执行策略幂等创建当前依赖已经满足的子 Run Registry 与 `start` Command；仍有未完成子步骤时，其权威 `lifecycle_status` 在 checkpoint 中进入 `waiting_agent`。
5. 群 mention 使用 `source_type = chat`。单 Agent mention 使用 `source_execution_id = group_mention:{message_id}:agent:{agent_id}`；Planning Run 使用 `group_mention:{message_id}:plan`；规划产生的子 Run 使用 `group_mention:{message_id}:step:{step_id}`。
6. `agent_runs` 的部分唯一约束 `unique(source_type, source_execution_id) where source_execution_id is not null` 保证重复请求、Worker 重试和恢复不会创建重复 Run。
7. 数据库提交后 Command Worker 领取 pending Command。即时通知失败不回滚已经提交的消息、Run Registry 和 Command，由 Command 扫描及 reconciliation 继续调度。
8. reconciliation 必须检查已经生成计划但应运行的目标 Run 未齐全的 Planning Run，并使用同一幂等键补建当前依赖已经满足的目标 Run。

这里使用专用 `agent_run_commands` 作为 Runtime Command Inbox，可靠承载 start / resume / cancel；它不保存 Run 生命周期，也不扩展成通用业务 Outbox。只有未来出现跨数据库或跨消息系统的其他原子投递需求时，才另行引入 Transactional Outbox。

历史消息中的 @ 只是上下文文本，不会重新触发 Agent。

写入顺序：

1. 校验 `group_id` 和 `chat_session_id` 匹配，且群和 session 未删除。
2. 校验发送者是当前群成员，且 `removed_at IS NULL`。
3. 服务端解析消息中的 mention token，生成 `mentions`。
4. 写入 `chat_messages`，并更新 `chat_sessions.last_message_at`。
5. 更新除发送者外的人类成员未读状态。
6. 对解析出的 Agent mention 执行唤醒流程。

@ 解析规则：

1. 只以结构化 mention token 为准，不靠纯文本名字猜测成员。
2. mention 必须解析到当前群成员；不在群内、已移出、账号不可用的对象不触发。
3. @ 人类成员只用于展示提醒和未读中的 @ 标记，不触发 Agent。
4. @ Agent 才进入 Agent 唤醒。
5. 同一条消息重复 @ 同一个 Agent，只触发一次。
6. Agent 回复中的 @ 按同一套规则处理。

非法 mention 处理：

1. 已移出成员：不触发，前端可展示 mention 失效。
2. 不存在成员：不触发，保留原文。
3. 不可用 Agent：不触发。
4. 混合合法和非法 mention 时，合法对象继续处理。

### 4.2 多 Agent 任务规划

同一条消息同时 @ 多个 Agent 时，系统进入任务规划阶段。

任务规划 Agent：

1. 是系统内置 Agent。
2. 不作为普通群成员展示。
3. 默认不在群里发言。
4. 只生成轻量分工计划，不替代业务 Agent 工作。

Planning 模型规则：

1. 使用独立配置 `MULTI_AGENT_PLANNING_MODEL_ID`，不复用任意群成员 Agent 的模型，也不复用 `MULTI_AGENT_COMPACT_MODEL_ID`。
2. Planning 模型只接收规划所需上下文并输出结构化计划，不挂载业务工具，不允许产生外部副作用。
3. 配置缺失、模型不存在、租户不可用或 Model Capability 校验失败时，Planning Run 直接失败，不创建业务子 Run。
4. 模型调用失败或结构化计划校验失败时可以按无副作用调用策略重试；结构修复最多两次，仍失败则 Planning Graph State 进入 `failed`。
5. 失败后在原 `chat_session_id` 写入一条显式、脱敏的系统消息：`participant_id = null`、`role = system`。消息说明任务规划未完成并提示用户重试或改单 Agent 处理，不展示模型配置、Provider 错误或内部堆栈。
6. 规划失败 ChatMessage、`delivery_status` 与 `delivery_failed / delivery_succeeded` 事件在同一交付事务写入，使用 `run:{planning_run_id}:terminal:failed` 作为幂等键，重复恢复不得重复提醒；生命周期 `run_failed` 仍由 Projector 从 terminal checkpoint 派生，不在交付事务双写。
7. 原群或原 Session 已删除时不改写到 replacement primary，只记录 `delivery_status = failed`。
8. Planning Run 使用 `run_kind = orchestration`、`agent_id = null`、`system_role = group_planning`，不创建或伪造业务 Agent、Participant 或 GroupMember。
9. 创建 Planning Run 时把 `MULTI_AGENT_PLANNING_MODEL_ID` 解析为真实 `llm_models.id` 并固化到 `agent_runs.model_id`；配置切换只影响新 Run，已有 Run 的恢复和结构修复继续使用固化模型。

轻量分工计划使用结构化输出：

```json
{
  "version": 1,
  "goal": "本次协作目标",
  "execution_strategy": "parallel | sequential | dependency",
  "steps": [
    {
      "step_id": "stable-step-id",
      "agent_id": "target-agent-id",
      "instruction": "该 Agent 的建议分工",
      "depends_on_step_ids": []
    }
  ]
}
```

计划校验规则：

1. `agent_id` 必须属于本次合法 mention 的 Agent 集合。
2. `step_id` 在计划内唯一且稳定；`depends_on_step_ids` 只能引用当前计划中的步骤。
3. 依赖图必须无环；存在环、未知步骤或非法 Agent 时规划失败，不启动业务 Agent Run。
4. 用户明确给出的分工、顺序和依赖优先于 Planning Agent 自主规划。
5. 用户没有明确要求且步骤之间没有结果依赖时，默认 `parallel`。
6. 全部步骤线性依赖时使用 `sequential`；部分步骤可以并发、部分步骤依赖前置结果时使用 `dependency`。
7. `parallel` 要求所有 `depends_on_step_ids` 为空；`sequential` 要求除第一步外每一步只依赖紧邻前一步；`dependency` 允许任意无环依赖图。
8. 调度器以 `depends_on_step_ids` 为权威依据；`execution_strategy` 只用于描述和一致性校验。两者不一致时拒绝该计划并重新规划，达到重试上限后将 Planning Run 标记为失败。

如果用户已经明确给出分工、顺序或协作方式，以用户规划为准。

执行规则：

1. 任务规划 Agent 的输出是内部结构，不作为群消息写入。
2. 规划结果不向前端展示，也不进入公开群上下文。
3. 规划结果的权威版本保存在 Planning Run checkpoint 中，产品侧只保留可重建 result projection，不创建独立 planning 业务表；Planning Agent 自身按统一 Runtime 创建根 Run Registry 与 start Command。
4. `parallel`：一次性创建全部步骤的子 Run。
5. `sequential`：只创建第一个步骤的子 Run；当前步骤完成后再创建下一个。
6. `dependency`：创建所有入度为零的步骤；每个子 Run 完成后重新计算并创建依赖已经全部完成的步骤。
7. 子 Run 写入 `parent_run_id = planning_run_id` 和同一 `root_run_id`；稳定 `source_execution_id` 使用 `group_mention:{message_id}:step:{step_id}`。
8. Planning Graph State 在仍有子步骤未完成时进入 `waiting_agent`；全部步骤到达终态后恢复并完成编排。
9. 每个 Agent 独立构造自己的群上下文包，并注入当前步骤信息及明确依赖步骤的结果摘要和产物引用。
10. Agent 最终公开回复分别写回当前 `chat_session_id`。
11. 某个 Agent 失败时，彼此无依赖的步骤继续执行；直接或间接依赖该失败步骤的后续步骤不启动，并由 Planning Run 记录失败原因。

同一个 Agent 同时被多条群消息 @ 时，按群消息写入顺序串行执行；该规则只约束群 mention 队列，不把该 Agent 的 Direct、Task、Trigger、Heartbeat 等所有 Run 全局限制为一个：

1. 顺序以统一 Message Position 为准，固定使用 `created_at ASC, id ASC`；禁止比较 UUID 大小或只按时间戳排序。
2. 如果该 Agent 当前没有正在处理的消息，系统立即调用该 Agent。
3. 如果该 Agent 正在处理上一条 @，新的 @ 等待上一条处理完成后再执行。
4. v1 不并发执行同一个 Agent 的多个 @ 请求。

可靠实现固定复用统一 Runtime 的 scheduling lane：业务 Run 写入 `scheduling_lane_key = group_mention:{tenant_id}:{agent_id}` 和触发消息 Message Position，只有同 lane 最早的未终态 Run 可以原子取得 `lane_held`。Run 在 waiting 时继续占有 lane，到 checkpoint terminal 后释放；进程崩溃时由 reconciliation 根据 checkpoint 修复。生命周期和终态判断不得读取 `projected_*`。Planning Run、Direct、Task、Trigger、Heartbeat 与普通 A2A 不写该 lane key，因此不会被群 mention 队列全局串行。字段、partial unique index 和恢复算法以 `../single-agent-runtime/technical-design.md` 5.6、12.4 节为准。

### 4.3 群内 A2A

根据线上 PRD v3，群内 A2A 的发起方式和普通 A2A 相同，消息和结论都不放到群内过程中。

技术含义：

1. 群内 Agent 如果需要另一个 Agent 协助，可以复用现有普通 A2A 能力。
2. 该 A2A 的过程消息不写入 `chat_messages`。
3. 该 A2A 的结论默认不写入当前群 session。
4. 未公开的 A2A 结论默认不更新 session summary、群 memory 或群 workspace。
5. 群消息流只保留发起 Agent 最终决定公开发送的群回复。
6. 如果发起 Agent 判断 A2A 结果对群内协作有价值，可以在自己的最终群回复中引用、总结或转述；只有这部分公开表达出来的内容才作为普通群消息进入当前 `chat_session_id`。
7. 如果发起 Agent 判断 A2A 结果没有价值，可以完全不在群里提及。

因此，原生群聊不把群内 A2A 过程持久化成群 `chat_sessions` 内的公开消息链。

Agent 自动派生使用统一循环保护：

1. 只检查 `run_kind = delegated` 的 Agent 自动 @Agent / A2A Run；人类 mention、Planning Run 和 Planning 生成的初始业务步骤不计入。
2. 每个自动派生 Run 都写入 `parent_run_id`、`root_run_id`、`origin_agent_id` 和目标 `agent_id`。
3. 创建新 delegated Run 前，沿当前 `parent_run_id` 祖先链收集有向边 `(origin_agent_id, agent_id)`，并加入本次候选边 `(current_agent_id, target_agent_id)`。
4. 当前链的循环次数定义为每条有向边在第一次出现后的重复次数总和，即 `sum(max(edge_count - 1, 0))`。
5. `A → B → C` 的循环次数为 0；`A → B → A → B` 中 `A → B` 第二次出现，循环次数为 1。
6. 候选 delegated Run 会使循环次数达到 `MAX_AGENT_CYCLE_COUNT = 5` 时，不创建该 Run，工具返回结构化 `agent_cycle_limit_reached`。
7. 当前 Agent 收到限制结果后继续完成自己的 Run，并通过公开终态消息说明自动协作已停止，需要用户决定是否继续。
8. 并行分支只检查各自的祖先链，不使用 root 级总派生次数，不限制没有重复边的正常 A→B→C→D 协作。

循环判定以数据库中的 Run 父链为准，不依赖进程内计数；服务重启、恢复或重复投递后结果必须一致。

### 4.4 任务触发和回调落点

人类成员在群 session 中为 Agent 创建触发器、回调任务或其他异步任务时，需要把群来源写入任务配置或 job metadata。

来源 metadata：

| 字段 | 说明 |
|-|-|
| `_origin_source` | 固定为 `group`。 |
| `_origin_group_id` | 任务创建时所在群。 |
| `_origin_chat_session_id` | 任务创建时所在群 session。 |
| `_origin_message_id` | 触发任务创建的群消息，可为空。 |
| `_origin_sender_participant_id` | 创建任务的人类成员。 |

回写规则：

1. 如果存在 `_origin_chat_session_id`，任务触发或完成后的公开群消息优先写回该群 session。
2. 回写前校验群、群 session、触发者和目标 Agent 仍然有效，且目标 Agent 仍在群内。
3. 独立后台 Run 正常完成后优先写回 `_origin_chat_session_id`；如果写入前已经确认原 Session 已删除、不存在或不可写，则解析同一 `group_id` 当前未删除 primary 并回退写入。
4. 如果只有 `_origin_group_id`、没有明确 session 指向，则直接写回该群当前未删除 primary。
5. 群没有可用 primary 时不自动创建 Session，保留后台业务结果并记录 `delivery_status = failed`。
6. fallback 只能发生在第一次写入前已经确定原目标不可用时。原目标写入请求结果为 unknown、可能已经成功时，必须先使用幂等键对账或重试原目标，禁止立即改投 primary 造成重复消息。
7. 实际交付的 `chat_session_id`、是否 fallback 和 fallback 原因写入 delivery event，便于审计和重试。
8. fallback primary 必须属于同一个群；不得跨 group 或改写到其他 direct scope。
9. 任务执行过程、工具日志和 trace 不写入 `chat_messages`。
10. 只有任务触发或完成后需要公开给群内成员看的最终消息，才写入 `chat_messages`。
11. 回写消息中如果包含结构化 mention token，按 4.1 的 @ 解析规则继续处理。

## 5. 群上下文构造

Agent 被 @ 后，不直接加载完整群聊记录。系统通过 group context builder 组装本轮上下文。

### 5.1 拼装入口

context builder 输入：

| 输入 | 说明 |
|-|-|
| `group_id` | 当前群。 |
| `chat_session_id` | 当前群 session，对应 `chat_sessions.id`。 |
| `trigger_message_id` | 当前触发消息。 |
| `sender_participant_id` | 当前发言人。 |
| `target_agent_participant_id` | 本次被唤醒的 Agent。 |
| `mention_targets` | 当前消息解析出的 @ 对象。 |
| `planning_hint` | 多 Agent 任务规划结果，可为空。 |

拼装前置校验：

1. 群未删除。
2. 群 session 未删除。
3. 当前发言人仍是群成员。
4. 被唤醒 Agent 仍是群成员。
5. 被唤醒 Agent 自身状态可用。

### 5.2 拼装顺序

最终输入按以下顺序拼装：

1. 群聊执行规则：只能基于当前群可见内容回答，不能假设看到其他群或未共享内容。
2. 当前任务：触发消息全文、发送者、本轮被 @ 对象、本轮实际唤醒 Agent。
3. 当前 Agent 在群内的身份：Agent ID、名称、角色描述、群内可用权限。
4. 当前群基础信息：群 ID、群名称、群介绍。
5. 当前 session 基础信息：session ID、session 名称、是否 primary。
6. 群公告：按注入上限截断。
7. session 历史摘要：读取 `session_context_states` 中该 `chat_session_id` 的当前版本。
8. 最近原始消息窗口：当前 session 最近 20 条公开群消息。
9. 当前 Agent 的群 memory：读取 `groups/{group_id}/agents/{agent_id}/memory/memory.md`。
10. 群 workspace 相关内容：文件索引、被显式引用文件、相关文件摘要或片段。
11. 多 Agent 任务规划提示：仅当本轮有规划结果时注入。
12. 可用工具和约束：群成员查询、群公告读取、群 workspace 文件读写等能力说明。

本轮群上下文包括：

1. 当前触发消息全文。
2. 本轮 @ 关系，包括发送者、被 @ 对象、本轮实际唤醒的 Agent。
3. 当前群基础信息：群 ID、群名称、群介绍。
4. 当前 session 基础信息：session ID、session 名称。
5. 当前发言人基本信息：成员 ID、姓名、职位、部门。
6. 被唤醒 Agent 基本信息：Agent ID、名称、角色描述。
7. 当前 session 最近 20 条消息。
8. 当前 session 的压缩摘要。
9. 群公告在注入上限内的内容。
10. 当前 Agent 在该群的群 memory。
11. 当前 Agent 对历史摘要的工作视角或已承担事项。
12. 群 workspace 中与本轮相关的文件索引、文件摘要或必要片段。

不默认注入：

1. 同群其他 session 的原始消息。
2. 其他群消息。
3. 未明确分享到群的 Agent workspace 文件。
4. 完整工具日志。
5. 完整 A2A 会话。
6. 全量群成员列表。

群成员信息通过工具按需查询。工具只返回当前群内成员，并支持按姓名、角色或能力检索。

### 5.3 token 预算和截断

每次 Agent 被唤醒时，Context Builder 使用该 Agent 当前实际执行模型的能力计算本轮输入预算；正常路径使用 primary model，安全 failover 后使用当前 fallback model：

```text
effective_runtime_budget =
  max_input_tokens
  - static_prompt_tokens
  - tool_schema_tokens
  - reserved_runtime_tokens
  - safety_margin_tokens
```

`max_input_tokens`、Provider 能力发现和 failover 重算规则复用统一 Runtime。群共享摘要的 Compact 触发阈值单独按群内有效 Agent 模型的最小预算计算，不能用某个大窗口 Agent 的预算代表整个群。

v1 在预算内使用固定优先级，不做复杂优化。

优先级：

1. 当前触发消息和 @ 关系必须保留。
2. 群/session 基础信息必须保留。
3. 被唤醒 Agent 基本信息必须保留。
4. 群公告按上限截断。
5. session 摘要按上限截断。
6. 最近消息从新到旧取，直到达到最近消息预算。
7. 群 memory 按上限截断。
8. workspace 文件只注入索引和命中的短片段。

截断规则：

1. 截断时保留内容来源说明，例如“群公告已截断”。
2. 被截断内容不自动继续读取。
3. Agent 需要更多内容时，必须调用对应读取工具。
4. 不因为 token 不足而删除当前触发消息。

### 5.4 最近消息窗口

最近消息窗口规则：

1. 只取当前 `chat_session_id` 下的公开群消息。
2. 默认取最近 20 条。
3. 不取已删除 session 的消息。
4. 不取 Agent 中间执行过程、工具日志或 trace。
5. 消息按创建时间升序放入模型上下文。
6. 每条消息带发送者展示名、participant 类型、创建时间和正文。
7. mention 以可读形式保留，同时保留稳定 participant ID 供工具使用。

### 5.5 群公告注入

群公告规则：

1. 每次 Agent 被 @ 时读取最新群公告。
2. 群公告修改后，不影响已经开始的 Agent 执行。
3. 群公告进入上下文时有长度上限。
4. Agent 不能编辑群公告。
5. Agent 如果需要完整公告，通过群公告读取工具按需读取。

### 5.6 群 memory 注入和更新

群 memory 注入规则：

1. 只自动注入当前被唤醒 Agent 在当前群的 memory。
2. 不自动注入其他 Agent 的群 memory。
3. Agent 可以通过工具读取同群其他 Agent 的群 memory。
4. Agent 只能写自己的群 memory。
5. 人类成员可以读写删除所有群 memory。

群 memory 更新规则：

1. Agent 不因为普通群消息或公告变化自动更新群 memory。
2. 只有 Agent 被 @ 并完成处理后，才可以判断是否需要更新自己的群 memory。
3. v1 中 memory 更新可以作为执行后的异步动作，不阻塞群消息回复。
4. 群 memory 写入必须走 group-scoped wrapper API，不能绕过权限直接写路径。

### 5.7 群 workspace 注入

workspace 注入规则：

1. 默认不注入完整文件内容。
2. 默认注入群 workspace 的轻量文件索引：路径、文件名、类型、更新时间、摘要。
3. 当前消息显式引用文件时，优先注入该文件摘要或短片段。
4. session 摘要中引用的文件可以作为候选注入。
5. Agent 需要完整文件时，通过群 workspace 文件读取工具按需读取。
6. Agent 产出的可复用文件写入群 workspace 后，应在最终群回复中说明文件路径或用途。

### 5.8 成员查询工具

群成员不默认全量注入。Agent 需要找人、确认身份或在回复中 @ 成员时，调用群成员查询工具。

工具返回：

1. `participant_id`。
2. 类型：人类成员或 Agent。
3. 展示名。
4. 角色：`manager` / `member`。
5. 对 Agent 返回 Agent 名称和角色描述。
6. 对人类成员返回可展示的部门、职位等基础信息。

工具限制：

1. 只返回当前群内 `removed_at IS NULL` 的成员。
2. 不返回其他群成员。
3. 不返回已删除群成员列表。
4. 不允许 Agent 凭历史文本中的名字构造 mention。

## 6. session 历史摘要和压缩

群 session 摘要复用统一 Runtime 的 `session_context_states`，不再维护群专用摘要文件或第二套 summary 表。`session_id` 指向 `chat_sessions.id`；群 session 的共享摘要不属于某个 Agent，因此 `agent_id` 为空，`tenant_id` 和 `group_id` 通过 `chat_sessions` 校验。

使用统一字段：

```text
summary
requirements
decisions
open_items
evidence_refs
workspace_refs
covered_through_message_id
version
created_at
updated_at
```

同一 `chat_session_id` 只有一个当前 Session Context。更新必须使用 `expected_version + expected_covered_through_message_id` compare-and-swap；并发压缩冲突时重新读取最新版本并重新合并。

`covered_through_message_id` 是 watermark 身份，不直接承担排序。读取待压缩消息时先解析该消息的 `(created_at, id)`，再选择 Message Position 更大的消息；原 watermark 消息不存在或不属于当前 session 时停止推进并进入重建，不得猜测覆盖范围。

上下文由历史摘要、待压缩区和最近消息组成。

1. 最近 20 条消息保留原文进入上下文。
2. 滑出最近 20 条的旧消息进入待压缩区。
3. 待压缩区达到系统设定 token 阈值后触发压缩。
4. 压缩后清空待压缩区，并更新历史摘要。
5. session 很短时可以不触发压缩。
6. session 很长时按批次压缩，不逐条压缩。
7. Context State 丢失或损坏时，可以从统一 `chat_messages` 重新生成。
8. `chat_sessions` 不保存 `summary` 或 `topic_state` 字段。
9. 不新增 `group_session_summaries` 或群专用摘要正文表。

topic 已从 PRD 正文移到技术文档。v1 把 topic 作为 Session Context 的内部摘要状态处理，不在 `chat_sessions` 主表里放 `topic_state`。

topic 规则：

1. topic 是当前群 session 摘要中的内部状态，不是独立业务对象。
2. topic 不作为权限边界，不隔离群 workspace，也不强制切分群 session。
3. v1 不提供复杂的 topic 切换、合并、拆分或用户侧管理入口。
4. topic 由上下文压缩流程维护，群内 Agent 只消费 topic 结果，不负责直接维护 topic 状态。
5. topic 状态可以包含当前目标、当前阶段、是否阶段结束、相关文件、相关成员或 Agent。
6. topic 更新跟随摘要压缩批次发生，不要求每条消息都实时更新。
7. 如果一个 session 中自然混入多个 topic，摘要中只保留对当前任务有帮助的 topic 状态和已结束 topic 的关键结论。
8. 当系统判断 topic 已完成或进入阶段结束状态时，可以生成可复用沉淀内容，但不会自动把完整历史或未公开的内部过程写入群 workspace。

建议摘要结构包含：

1. 当前目标。
2. 当前 topic 状态。
3. 已确认决策。
4. 未决问题。
5. 当前状态。
6. 相关文件。
7. 相关成员或 Agent。
8. 已过期或已废弃的结论。

后续如果需要跨 session 搜索摘要、按摘要状态排序筛选、用户可见压缩进度或更复杂的检索索引，再为 `session_context_states` 增加索引或只读投影，不建立另一份摘要事实源。

### 6.1 Compact 模型与预算

群聊属于多 Agent 场景，共享 Session Compact 必须显式配置独立 `compact_model_id`：

1. 不使用任一群成员 Agent 的当前模型临时执行共享压缩。
2. `compact_model_id` 由 `MULTI_AGENT_COMPACT_MODEL_ID` 配置解析，未配置时不得启动群共享上下文压缩任务。
3. 压缩触发预算按统一 `request_input_limit` 公式计算群内每个有效 Agent 模型的 `effective_runtime_budget`，再取其中最小值的 85%，保证共享 Session Context 能被任一群内 Agent 使用。
4. 独立 Compact 模型只负责生成摘要，不改变共享上下文的预算上限。
5. Compact 模型窗口不足以一次处理待压缩区时，按消息边界分批压缩；不得截断消息或跳过 watermark。
6. 每个具体 Agent 被唤醒后的 Run Compact 仍遵守统一单 Agent Runtime 规则，不修改群共享 Session Context。

模型窗口统一通过 `ModelCapabilityResolver.max_input_tokens` 获取；Provider API、Registry、人工 override、缓存和未知模型处理规则复用 `docs/single-agent-runtime/technical-design.md`，群聊不维护第二套模型能力表。

压缩触发规则：

1. 写入新的公开群消息后，检查该 session 是否达到压缩阈值。
2. 消息数和待压缩区 token 数可以提前触发；硬阈值使用群内最小有效输入预算的 85%。
3. 压缩任务异步执行，不阻塞用户发送消息和 Agent 回复。
4. 每次只压缩 `compressed_through_message_id` 之后、最近窗口之前的消息。
5. 每次压缩都排除当时最近 20 条消息；消息以后滑出最近窗口后可以进入新的待压缩批次。
6. 压缩完成后原子更新 `session_context_states` 的结构化内容、watermark 和 version。
7. 压缩失败不影响群消息读写；失败进入后台日志，后续可重试。

摘要生成要求：

1. 摘要必须区分事实、决策、待办、未决问题和废弃结论。
2. 摘要中引用文件时保留群 workspace 路径。
3. 摘要中引用成员或 Agent 时保留 participant ID。
4. 摘要不得引入当前 session 之外的消息。
5. 摘要不得把 Agent 中间工具日志当作群共识。
6. 有冲突的信息必须标记为冲突或未决，不能直接合并成确定结论。

## 7. 群 session 生命周期逻辑

### 7.1 创建 session

创建规则：

1. 只有当前群内 `removed_at IS NULL` 的人类成员可以创建群 session。
2. Agent v1 不能创建群 session。
3. 创建群时不自动创建 session。
4. 当前群第一个 session 自动成为 primary session。
5. 后续创建的 session 默认 `is_primary = false`。
6. session 标题可以由用户提供；如果未提供，使用临时标题。
7. 第一条群消息写入后，如果仍是临时标题，可以用第一条消息生成标题。

### 7.2 删除 session

删除规则：

1. 只有群 `manager` 可以删除群 session。
2. 删除 session 写入 `chat_sessions.deleted_at`。
3. 删除后默认 session 列表、消息查询、上下文构造都过滤该 session。
4. 删除 session 不删除群、群成员、群公告、群 workspace 或群 memory。
5. 删除 session 后不提供用户侧恢复。
6. primary session 允许删除，不要求调用方指定 replacement。
7. 删除 primary 时锁定 `groups` 行，在同一事务内 soft delete 当前 primary，并从同群剩余未删除 Session 中自动选举 replacement。
8. replacement 固定按 `last_message_at DESC NULLS LAST, created_at DESC, id DESC` 选择并设置为 primary。
9. 没有剩余 Session 时允许群处于无 primary 状态；后续由人类成员创建的第一个 Session 自动成为 primary。
10. 数据库 partial unique index 保证同群最多一个未删除 primary；并发唯一冲突时回滚并重新读取，不覆盖另一事务已经完成的选举。

direct session 使用同一套删除、replacement 选举和“最多一个、允许没有”的 primary 生命周期，scope 为 `(tenant_id, agent_id, user_id)`。差异只在无 Session 时的创建权限：direct 可以由用户进入或 Agent 合法主动触达创建，group v1 不允许系统因异步回写自动创建。

Run 生命周期规则：

1. Session 删除时取消绑定该 Session 的 `foreground`、`orchestration`，以及由这些前台根 Run 派生的 `delegated` Run。
2. 为相关 Run 写入幂等 cancel Command；尚未建立 checkpoint 的 start Command 被拒绝，已有 Thread 由 Graph 在当前安全节点结束后进入 `cancelled`。
3. 已经开始的工具先记录真实结果，不能伪造回滚；取消请求生效后不得启动新的 Graph node 或 Tool Call。
4. Task、Trigger、Heartbeat 和 oneshot 等有独立业务来源的 `background` Run 不因 Session 删除而取消，继续按自己的 checkpoint 执行。
5. 后台 Run 不再读取被删除 Session 的最新 Context；结果交付按 4.4 回退同 scope primary。

## 8. 未读和提醒逻辑

未读状态按“人类成员 + 群 session”维度计算，v1 存在 `group_members.session_read_state`。

写入消息时：

1. 发送者自己的未读不增加。
2. 其他当前人类成员对该 session 产生未读。
3. Agent 最终公开回复计入人类成员未读。
4. 任务触发或回调产生的公开群消息计入未读。
5. Agent 中间过程、workspace 文件变化、群公告变化不直接计入未读。
6. 被 @ 的人类成员可以额外展示 @ 提醒。

标记已读时：

1. 用户进入群 session 并看到最新消息后，更新自己的 `session_read_state`。
2. 更新内容包含 `last_read_message_id` 和 `last_read_at`。
3. `last_read_message_id` 通过对应消息的 `(created_at, id)` 计算未读边界，不比较 UUID 大小。
4. 删除 session 后，该 session 未读不再展示。
5. 删除群后，该群所有未读不再展示。

并发更新规则：

1. v1 不在应用层读取整份 JSON 后无锁覆盖写回。
2. 标记已读事务先使用 `SELECT ... FOR UPDATE` 锁定当前 `group_members` 行，再读取和更新目标 `chat_session_id` 对应的 JSON key。
3. 新旧 `last_read_message_id` 都解析成该 Session 内的 `(created_at, id)`；只有新的 Message Position 更大时才允许推进，延迟请求不得把已读位置回退。
4. 目标消息不存在、不属于当前 Session、Session 已删除或成员已移出群时拒绝更新。
5. 同一成员在同一群的不同 Session 已读更新会短暂串行；v1 接受这一开销，不额外增加 read state 表或复杂 JSONB CAS。
6. Session 删除后遗留 JSON key 可以延迟清理，正常未读查询必须忽略已删除 Session。

未读查询时：

1. 只统计未删除群和未删除 session。
2. 只对当前群成员展示。
3. 已移出成员不再看到群未读。
4. v1 不做全员未读统计。

## 9. 权限和删除

### 9.1 权限

| 操作 | v1 权限 |
|-|-|
| 查看群 | 当前群内 `removed_at IS NULL` 的成员。 |
| 发送群消息 | 当前群内 `removed_at IS NULL` 的成员。 |
| 创建群 session | 当前群内 `removed_at IS NULL` 的人类成员。 |
| 修改群名称、介绍、公告 | 人类群成员。 |
| 邀请成员 | 人类群成员，候选范围仍受现有可见性限制。 |
| 移出成员 | 群管理。 |
| 解散群 | 群管理。 |
| 读取群 workspace | 当前群内 `removed_at IS NULL` 的成员和 Agent。 |
| 写入群 workspace | 当前群内 `removed_at IS NULL` 的成员和 Agent。 |
| 读写群 memory | 按 3.3 的群 memory 规则。 |

### 9.2 删除

PRD v3 中写明：群管理可以解散群，解散后群内 session、群 workspace 文件等都会被硬删除。

技术设计先按以下方式落地：

1. 删除入口先校验操作者是当前群 `manager`。
2. 校验通过后写入 `groups.deleted_at`，用户侧群列表、群详情、群 session、群消息、群 workspace、群 memory 立即不可见。
3. 群删除的硬删除对象是用户可见内容、消息正文和文件正文；成员关系与审计日志只保留最小元数据，用于排障、审计或合规，不提供用户侧恢复能力。
4. `group_members` 记录保留，用于审计当时的成员关系；默认业务查询通过 `groups.deleted_at IS NULL` 过滤。
5. 群 `chat_sessions` 写入 `deleted_at`；默认查询同时过滤已删除群和已删除 session。
6. 对应 `chat_messages` 后台异步硬删。
7. 群公告文件、群 workspace 文件、群 memory 文件和 `session_context_states` 后台异步清理。
8. 清理任务必须幂等：重复执行不会报错，部分文件已不存在也视为可继续。
9. 清理失败不影响用户侧“群已删除”状态，但需要进入后台日志或告警，后续可重试。
10. `audit_logs` 保留必要操作记录，用于排障、审计或合规。
11. `audit_logs` 不保存完整群消息正文或完整文件内容，也不提供用户侧恢复能力。
12. v1 不新增 `group_deletion_jobs`。如果后续需要用户可见删除进度、失败重试列表或合规删除证明，再单独设计删除任务表。
13. 删除群不是成员移除操作，不触发“至少保留一个 manager”的校验冲突。

## 10. 第三方渠道边界

第三方群暂时不映射为 Clawith 原生群。

1. Clawith 原生群消息暂时不会自动同步到第三方会话或第三方群聊。
2. 如果 Agent 需要联系未绑定平台账号的第三方同步成员，显式调用渠道发送能力，例如 `send_channel_message`，目标是具体成员。
3. 外部渠道 adapter 后续如要接入原生 group model，应单独设计映射层，不扩展现有外部渠道会话模型承载原生群。

## 11. API 草案

```text
POST   /groups
GET    /groups
GET    /groups/{group_id}
PATCH  /groups/{group_id}
DELETE /groups/{group_id}

GET    /groups/{group_id}/members
POST   /groups/{group_id}/members
PATCH  /groups/{group_id}/members/{member_id}
DELETE /groups/{group_id}/members/{member_id}

GET    /groups/{group_id}/sessions
POST   /groups/{group_id}/sessions
PATCH  /groups/{group_id}/sessions/{session_id}
DELETE /groups/{group_id}/sessions/{session_id}

GET    /groups/{group_id}/sessions/{session_id}/messages
POST   /groups/{group_id}/sessions/{session_id}/messages

GET    /groups/{group_id}/announcement
PUT    /groups/{group_id}/announcement

GET    /groups/{group_id}/agents/{agent_id}/memory
PUT    /groups/{group_id}/agents/{agent_id}/memory
DELETE /groups/{group_id}/agents/{agent_id}/memory

GET    /groups/{group_id}/sessions/{session_id}/summary

GET    /groups/{group_id}/workspace?path=...
GET    /groups/{group_id}/workspace/file?path=...
PUT    /groups/{group_id}/workspace/file?path=...
DELETE /groups/{group_id}/workspace/file?path=...
```

`POST /groups/{group_id}/sessions/{session_id}/messages` 是核心入口：保存群消息、解析 @、触发任务规划或 Agent 执行，并返回已保存的群消息。

## 12. Group Chat 后端首期范围

### 12.1 实施与发布顺序

整体开发按“编写统一聊天表迁移 → Single Chat 后端 → Group Chat 后端 → 后端整体验证 → 前端统一更新”分阶段实施。生产环境不能在新 Single Chat 后端就绪前提前执行迁移；迁移和新 Single Chat 后端必须在同一个维护窗口切换。该切换不是长期双写阶段：窗口内必须停止 API、WebSocket、渠道 Consumer、Trigger、Heartbeat 和后台 Worker 等全部写入方，完成迁移与校验后只启动使用新 Schema 的后端，不允许新旧后端混跑。

强制迁移的字段顺序、历史回填、约束、索引、失败恢复和开放流量条件以 `chat-model-refactor.md` 第 7 章为准。Group Chat 功能可以在 Single Chat 后端稳定后再启用，但不能因此继续运行依赖旧 Schema 的后端。

### 12.2 功能范围

Group Chat 后端最小闭环：

1. 新增 `groups`、`group_members`，并按 `chat-model-refactor.md` 扩展统一 `chat_sessions`、`chat_messages`。
2. 当前消息 @ 解析和 Agent 成员过滤。
3. 多 Agent @ 的轻量任务规划。
4. Agent 最终公开回复写回群 session。
5. 群公告固定路径读取和注入。
6. 群 workspace 固定路径读写。
7. 群 memory 固定路径初始化、读取和写入。
8. group context builder：当前消息、@ 关系、群/session 信息、公告、摘要、最近 20 条消息、群 memory、workspace 索引。
9. 统一 `session_context_states` 摘要和异步压缩触发。
10. 群 session 创建、删除、primary 兜底逻辑。
11. 群解散后的用户侧不可见和后台清理。
12. 基于 `group_members.session_read_state` 的 v1 未读状态。
13. 群 session/message 全程写入统一 `chat_sessions` / `chat_messages`，代码和迁移中不存在新建群专用聊天表的路径。
14. 群 Session Context 写入 `session_context_states(agent_id = null)`，并通过 version + watermark 防止并发覆盖。
15. 群共享 Compact 缺少独立 `compact_model_id` 时 fail closed；配置后按群内 Agent 最小有效输入预算触发并支持分批压缩。

Group Chat 后端首期明确不做：

1. 群名唯一约束。
2. 群公告、群 workspace key、群 memory 正文入表。
3. 群内 A2A 过程写入群消息。
4. topic 独立表或 `chat_sessions.topic_state`。
5. 摘要独立表。
6. 外部渠道群和原生群双向绑定。
7. Agent 主动邀请新成员。
8. Agent 主动创建群 session。
9. 群专用 Agent run 表和 run event 表。
10. 群级独立审计表或独立 log 表。

## 13. 开发就绪结论

聊天数据模型、Session Context、Runtime、Planning、同 Agent mention 串行和 Compact 边界已经收敛，没有需要产品讨论才能开始编码的开放架构项。部署前必须为 `MULTI_AGENT_PLANNING_MODEL_ID` 和 `MULTI_AGENT_COMPACT_MODEL_ID` 分别配置具体模型并通过 Model Capability Resolver 与结构化输出校验；任一配置失败都按正文规则显式失败，不静默复用业务 Agent 模型。
