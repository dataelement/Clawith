# Clawith 群聊 v1 测试用例文档

> 更新依据：飞书测试用例模板 `KMoTdTIYyoIQMPxLTTKcPI4XnTd` revision 11、当前 checkout 的 `docs/group-chat/prd.md` 与 `docs/group-chat/technical-design.md`，以及实现基线 `feature/unified-chat-single-agent-runtime-group-chat`（tip `0ef0f8d4`）。

## 目录

| 模块编号 | 模块名称 | 用例数量 |
|-|-|-:|
| M01 | 范围与数据模型 | 8 |
| M02 | 群 CRUD 与生命周期 | 8 |
| M03 | 成员、邀请与权限 | 8 |
| M04 | Session 与 Primary | 8 |
| M05 | 消息与 Structured Mention | 8 |
| M06 | ACK 与用户可见闭环 | 8 |
| M07 | Multi-Agent Planning | 8 |
| M08 | Mention Lane 与 A2A Cycle | 8 |
| M09 | 群上下文与成员工具 | 8 |
| M10 | Announcement、Memory 与 Workspace | 8 |
| M11 | Session Compact 与 Topic | 8 |
| M12 | 未读、回调、删除与审计 | 8 |
| M13 | API、安全、并发、性能与发布 | 8 |
| **合计** |  | **104** |

## M01 - 范围与数据模型

### TC-M01-001: groups 模型符合群领域契约

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 加载实现基线的 SQLAlchemy metadata；不连接生产数据库。 |
| **测试步骤** | 1. 检查 `groups` 的主键、`tenant_id`、`created_by_participant_id`、名称、介绍、时间戳与 `deleted_at`。<br>2. 检查群名约束及公告、workspace 相关列。 |
| **预期结果** | 字段、外键、可空性与索引符合设计；群名无唯一约束；表内不存在公告正文、workspace key 或 memory 正文列。 |

### TC-M01-002: group_members 统一引用 Participant

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 已准备 human 与 agent 两类 `participants` metadata。 |
| **测试步骤** | 1. 检查 `group_members` 的 `group_id`、`participant_id`、`role`、`joined_at`、`removed_at`、`session_read_state`。<br>2. 检查 `(group_id, participant_id)` 唯一约束。 |
| **预期结果** | 成员表不冗余 `user_id`/`agent_id`；同一 participant 在同一群只有一条 membership；移出状态由 `removed_at` 表达。 |

### TC-M01-003: 群领域迁移可从空 Schema 正确建表

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 使用隔离 PostgreSQL schema，当前版本位于迁移声明的前置 revision。 |
| **测试步骤** | 1. 执行群领域 migration upgrade。<br>2. 检查 `groups`、`group_members`、外键、检查约束与索引。<br>3. 在事务回滚前插入一组合法群和成员。 |
| **预期结果** | 两张表及全部约束按顺序创建；合法数据可写入；迁移 revision 链唯一且指向 reviewed static head。 |

### TC-M01-004: 群领域迁移拒绝不兼容既有表

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 隔离 schema 中预建列类型、约束或索引与目标契约冲突的同名表。 |
| **测试步骤** | 1. 执行 upgrade。<br>2. 记录失败前发生的 DDL/DML。<br>3. 检查原表和数据。 |
| **预期结果** | 迁移在破坏性写入前 fail closed；原表和数据不被改写；错误能定位不兼容对象，不静默将其视为已迁移。 |

### TC-M01-005: 群 Session 与消息复用统一聊天表

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 创建活跃群 G1、群 Session S1、发送者 Participant P1。 |
| **测试步骤** | 1. 写入 `chat_sessions(session_type=group, group_id=G1)`。<br>2. 写入 `chat_messages(conversation_id=S1, participant_id=P1)`。<br>3. 搜索群专用 session/message 表。 |
| **预期结果** | 群 Session 的 `agent_id`、`user_id` 为空；群消息通过统一会话归属；不存在群专用聊天表，旧 `is_group/group_name` 不作为新逻辑事实源。 |

### TC-M01-006: Participant 并发创建只保留一个身份

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 两个并发事务同时为同一 U1 或 A1 解析 Participant，外层事务还包含群写入。 |
| **测试步骤** | 1. 同时调用 participant ensure helper。<br>2. 让其中一方触发唯一冲突。<br>3. 继续各自外层事务并读取结果。 |
| **预期结果** | 只生成一条 Participant；冲突仅回滚 savepoint；两方复用赢家 ID；无关群写入不被整体回滚。 |

### TC-M01-007: 外部渠道群会话不伪装成原生 Group

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 准备一个原生群 G1/S1，以及一个来自真实外部渠道 sandbox 的群会话 EXT-S1。 |
| **测试步骤** | 1. 分别接收两类群消息。<br>2. 检查 unified chat session、`group_id`、`source_channel`、`external_conv_id` 与成员校验。<br>3. 让外部渠道回复第一次投递失败后重试。 |
| **预期结果** | 原生群使用 `session_type=group, group_id=G1`；外部群使用 unified chat session 和外部会话标识且 `group_id=null`，不能借用原生群成员权限；外部入口提交 ACK/Run 后执行，Provider 失败只重试 delivery outbox，不重跑 Graph。 |

### TC-M01-008: 表与文件的存储边界不混淆

| 项目 | 内容 |
|-|-|
| **优先级** | P1 |
| **前置条件** | G1 已有公告、workspace 文件、A1 群 memory 和 Session Context。 |
| **测试步骤** | 1. 检查当前文件、文件索引和 revision history。<br>2. 检查 `groups/G1/system/announcement.md`、`groups/G1/workspace`、`groups/G1/agents/A1/memory/memory.md`。<br>3. 检查 `session_context_states`。 |
| **预期结果** | 当前公告、workspace、memory 的权威内容位于固定 group-scoped 路径；数据库可在 `workspace_file_revisions.before_content/after_content` 保存受权限与清理约束的修订正文，但业务表不另存第二份当前正文或动态 storage key；结构化摘要写入 `session_context_states`。 |

---

## M02 - 群 CRUD 与生命周期

### TC-M02-001: 人类创建群并自动成为 manager

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | U-MGR 是 T-A 活跃人类用户并有 Participant；使用唯一幂等请求。 |
| **测试步骤** | 1. U-MGR 创建 G1，填写名称与介绍。<br>2. 在提交前检查 group、membership、audit staging。<br>3. 提交后查询群详情和成员。 |
| **预期结果** | group、manager membership 与审计在同一事务成功；创建者引用正确 Participant；不自动创建群 Session。 |

### TC-M02-002: 同名群允许创建并通过 ID 区分

| 项目 | 内容 |
|-|-|
| **优先级** | P1 |
| **前置条件** | T-A 已存在名称为“发布协作”的 G1。 |
| **测试步骤** | 1. 再创建同名 G2。<br>2. 查询群列表与两个详情。<br>3. 分别在 G1/G2 创建成员或 Session 后清理测试群。 |
| **预期结果** | 两群均创建成功且 ID 不同；后续数据严格按 group_id 隔离；名称不被当作权限或路由标识。 |

### TC-M02-003: 群列表与详情按成员和租户隔离

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | U-A 是 G1 成员但不是 G2 成员；U-X/G-X 属 T-B；另有已删除 G-DEL。 |
| **测试步骤** | 1. U-A 查询列表并访问 G1、G2、G-X、G-DEL。<br>2. U-X 访问 G1。 |
| **预期结果** | U-A 仅见 G1；非成员、跨租户与已删除群返回稳定 403/404；响应不泄露不可见群的名称、成员或内容。 |

### TC-M02-004: 人类成员可修改名称和介绍

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | U-A 是 G1 当前 human member。 |
| **测试步骤** | 1. PATCH 新名称与介绍。<br>2. 再 PATCH `description=""`。<br>3. 读取详情并恢复原值。 |
| **预期结果** | 两次修改成功并更新 `updated_at`；显式空字符串被保存而非当作未传字段；写入有审计记录。 |

### TC-M02-005: Agent 不能修改群基本信息

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | A1 是 G1 active Agent member。 |
| **测试步骤** | 1. 以 A1 身份尝试修改名称、介绍。<br>2. 尝试调用公告以外的底层 update service 绕过 API。 |
| **预期结果** | API 与 service 均拒绝；群元信息、`updated_at` 和成功审计不变；Agent 不能因是群成员而获得人类管理权。 |

### TC-M02-006: 只有 manager 可以解散群

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 含 U-MGR(manager)、U-A(member)、A1；已保存完整 fixture 快照。 |
| **测试步骤** | 1. U-A、A1、U-OUT 分别发起 DELETE。<br>2. 确认群未变后由 U-MGR 发起 DELETE。 |
| **预期结果** | 非 manager 请求全部拒绝且无副作用；manager 请求写 `groups.deleted_at`；解散不被“至少保留一个 manager”规则阻塞。 |

### TC-M02-007: 解散后用户侧立即不可见

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 含 S1/S2、消息、公告、workspace、memory、未读与 Context。 |
| **测试步骤** | 1. manager 解散 G1。<br>2. 立即查询群列表、详情、成员、Session、消息、未读和文件 API。<br>3. 尝试原直链访问。 |
| **预期结果** | 所有用户侧入口立即不可见且不可恢复；后台清理是否完成不影响该状态；不可通过直链或文件 API读取残留正文。 |

### TC-M02-008: PRD 硬删除语义按异步实现完成闭环

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 已 `deleted_at`；部分消息/文件仍待清理；异步 hard-delete worker 已实现并可检查 DB/storage。当前 ref 尚无全量硬删证明时，最终清理断言登记 `BLOCKED/待实现`。 |
| **测试步骤** | 1. 运行清理至完成。<br>2. 再运行一次模拟重试。<br>3. 检查当前文件、`workspace_file_revisions.before_content/after_content`、消息正文、Context 与最小审计元数据。 |
| **预期结果** | 能力完备时，群消息正文、当前公告/workspace/memory、group-scoped revision history 正文和 Session Context 最终被异步硬删；重复清理幂等；membership/audit 只保留设计允许的最小元数据且不能用于用户恢复；不得从 `deleted_at` 推断当前 ref 已完成全量清理。 |

---

## M03 - 成员、邀请与权限

### TC-M03-001: manager 邀请可见 Company Agent

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | U-MGR 是 G1 manager；A1 是同租户、可见、可用 Company Agent。当前 ref 仅支持提交 `participant_id`，邀请候选查询接口尚未实现。 |
| **测试步骤** | 1. 通过当前已实现接口提交 A1 的 `participant_id`。<br>2. 查询成员并在验证后移出 A1。<br>3. 候选列表能力完成后再执行候选搜索，并在此之前将该步骤登记 `BLOCKED/待实现`。 |
| **预期结果** | 已实现链路把 A1 作为 agent Participant 入群，角色默认为 member，成员表不复制 Agent 配置且邀请有审计；候选列表步骤在对应接口落地前不得报告通过。 |

### TC-M03-002: 普通人类成员可邀请但不能移出成员

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | U-A 是 G1 普通成员；U-B 是可见且已绑定平台账号的候选。 |
| **测试步骤** | 1. U-A 邀请 U-B。<br>2. U-A 尝试移出 U-B。<br>3. U-MGR 移出 U-B。 |
| **预期结果** | 普通成员邀请成功；普通成员移出返回 403 且 membership 不变；manager 移出成功并写 `removed_at`。 |

### TC-M03-003: Private Agent 不可被邀请

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | A-PRIVATE 与邀请人在 T-A，且邀请人是其创建者；当前 ref 没有邀请候选接口。 |
| **测试步骤** | 1. 直接提交 A-PRIVATE 的 `participant_id`。<br>2. 检查成员与审计。<br>3. 候选接口实现后搜索 A-PRIVATE；此前将候选步骤登记 `BLOCKED/待实现`。 |
| **预期结果** | 当前直接邀请链路 fail closed，创建者身份也不能绕过，且无 membership 或成功审计；未来候选接口不得返回 Private Agent，但不能把当前缺失接口记为已验证。 |

### TC-M03-004: 第三方成员只有绑定平台账号后可入群

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 第三方组织通讯录同步与账号绑定能力已部署；EXT-BIND 已映射平台 Participant，EXT-RAW 仅有外部记录。当前 ref 若未接入该能力，本用例登记 `BLOCKED/待实现`，不得记为自动化通过。 |
| **测试步骤** | 1. 同步通讯录并查询邀请候选。<br>2. 邀请 EXT-BIND。<br>3. 直接提交 EXT-RAW 外部 ID。<br>4. 移出 EXT-BIND 后再次同步并检查候选、membership 与访问权。 |
| **预期结果** | 两者可按来源出现在候选信息中，但只有 EXT-BIND 能解析为 Participant 并按普通 human 入群；EXT-RAW 入群前拒绝且不建占位成员；移出后同步不擅自恢复 membership 或访问权。 |

### TC-M03-005: Agent 不能邀请或移出成员

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | A1 是 G1 active Agent member；U-B 是候选。 |
| **测试步骤** | 1. 以 A1 身份调用 members POST/DELETE。<br>2. 尝试通过群工具间接执行成员管理。 |
| **预期结果** | API、service 和工具层均拒绝；群成员查询工具不暴露管理方法；没有成员状态变化。 |

### TC-M03-006: 移出后再次邀请复用原 membership

| 项目 | 内容 |
|-|-|
| **优先级** | P1 |
| **前置条件** | U-B 曾加入 G1，当前 membership 有 `removed_at`；记录其 id 与 `joined_at`。 |
| **测试步骤** | 1. 在没有结构化 mention 的情况下再次邀请 U-B。<br>2. 比较新旧 membership，并检查其他 Agent Run 与群 memory。<br>3. 验证后再次移出并重复检查。 |
| **预期结果** | 复用同一 membership id，清空 `removed_at` 并更新 `joined_at`；唯一约束不冲突；审计可区分移出与重新邀请；成员变化本身不唤醒未被 @ 的 Agent，也不自动更新其群 memory。 |

### TC-M03-007: 活跃群至少保留一个 manager

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 仅 U-MGR 一名 manager，另有普通成员。 |
| **测试步骤** | 1. 尝试移出或降级 U-MGR。<br>2. 确认失败后由 U-MGR 解散群。 |
| **预期结果** | 成员变更不能造成活跃群零 manager；v1 无管理权转让入口；解散只校验当前 manager，不被末位 manager 约束阻塞。 |

### TC-M03-008: 跨租户、非成员和已移出成员完全隔离

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | U-X/A-X 属 T-B；U-OUT 非 G1 成员；P-REMOVED 已移出。 |
| **测试步骤** | 1. 三类主体访问详情、Session、消息、workspace、memory API。<br>2. 在结构化 mention 中引用三者并混入合法 A1。 |
| **预期结果** | 数据 API 全部 fail closed；非法 mention 不触发 Run；合法 A1 仍可处理；响应不泄露跨租户或历史成员信息。 |

---

## M04 - Session 与 Primary

### TC-M04-001: 新群不自动创建 Session

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | U-MGR 可创建新的 G1；数据库中不存在该群历史 Session。 |
| **测试步骤** | 1. 创建群。<br>2. 查询群 Session 列表和 `chat_sessions`。<br>3. 删除测试群完成清理。 |
| **预期结果** | 列表和数据库均无该群 Session，群无 primary；后端不隐式创建默认 Session。 |

### TC-M04-002: 人类创建首个 Session 并自动成为 primary

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 活跃且无 Session；U-A 是 active human member。 |
| **测试步骤** | 1. U-A 创建 S1。<br>2. 读取 S1 与 Session 列表。<br>3. 检查统一聊天字段。 |
| **预期结果** | S1 为 `session_type=group`、`group_id=G1`、`is_primary=true`，`agent_id/user_id=null`；创建者 Participant 正确。 |

### TC-M04-003: Agent 不能创建或重命名群 Session

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | A1 是 G1 active member；S1 已存在。 |
| **测试步骤** | 1. A1 尝试创建 S2。<br>2. A1 尝试修改 S1 标题。 |
| **预期结果** | 两个操作均拒绝且无 DB/审计成功变更；Agent 被 @ 或后台回写也不能借机自动创建 Session。 |

### TC-M04-004: 同群最多一个未删除 primary

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 已有 primary S1；准备两个并发创建请求。 |
| **测试步骤** | 1. 创建 S2、S3。<br>2. 尝试直接将 S2 也设为 primary。<br>3. 检查 partial unique index。 |
| **预期结果** | S2/S3 默认非 primary；数据库阻止第二个未删除 primary；并发冲突回滚本事务而不覆盖 S1。 |

### TC-M04-005: 临时标题可由首条消息生成且人类可重命名

| 项目 | 内容 |
|-|-|
| **优先级** | P1 |
| **前置条件** | U-A 创建未提供标题的 S2。 |
| **测试步骤** | 1. 验证临时标题。<br>2. 写入第一条公开消息并触发标题生成。<br>3. U-A 手动重命名，再写第二条消息。 |
| **预期结果** | 首条消息只在标题仍为临时值时生成标题；手动标题后续不被自动覆盖；标题修改不改变消息归属。 |

### TC-M04-006: manager 删除非 primary Session

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 有 primary S1 和非 primary S2；U-MGR 是 manager。 |
| **测试步骤** | 1. 普通成员尝试删除 S2。<br>2. manager 删除 S2。<br>3. 查询列表、消息与上下文。 |
| **预期结果** | 普通成员被拒；manager 写 `S2.deleted_at`；S2 从默认查询和上下文排除；S1 仍是 primary；群文件与成员不受影响。 |

### TC-M04-007: 删除 primary 按固定顺序选举替代项

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 有 S1(primary)、S2/S3；为 S2/S3 设置可控 `last_message_at/created_at/id`。 |
| **测试步骤** | 1. manager 删除 S1。<br>2. 并发读取 Session 列表。<br>3. 对照排序 `last_message_at DESC NULLS LAST, created_at DESC, id DESC`。 |
| **预期结果** | 在锁定 groups 行的同一事务中 soft delete S1 并选出唯一 replacement；结果严格符合排序；调用方无需指定替代 ID。 |

### TC-M04-008: 删除最后 Session 允许无 primary 并按 Run 类型处理

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 仅 S1；S1 绑定 foreground/orchestration/delegated Run，另有独立 Task/Trigger background Run。 |
| **测试步骤** | 1. manager 删除 S1。<br>2. 检查 primary 与 cancel Commands。<br>3. 让 background Run 完成后再由人类创建 S2。 |
| **预期结果** | 群暂时无 primary；前台根及派生协作 Run 收到幂等 cancel，后台 Run 继续且无可用 primary 时记录交付失败；S2 成为新 primary。 |

---

## M05 - 消息与 Structured Mention

### TC-M05-001: 只有结构化 Mention Token 才触发 Agent

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1/S1 活跃；A1 是 active Agent member；客户端可分别发送纯文本和携带 Participant ID 的结构化 mention token。 |
| **测试步骤** | 1. 发送正文仅包含 `@A1名称` 的纯文本消息。<br>2. 发送正文相同但附带 A1 Participant ID 的结构化 mention。<br>3. 检查消息、Run Registry 与 start Command。 |
| **预期结果** | 两条消息均公开可见；纯文本不做名称猜测且不创建 Run；只有合法结构化 token 创建 A1 的执行链路。 |

### TC-M05-002: Mention 只解析当前群活跃成员且局部失败不阻塞有效目标

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | A1 为 G1 active member；A2 已移出；A3 属于其他群；另准备不存在的 Participant ID。 |
| **测试步骤** | 1. 同一消息依次 mention A2、A1、A3 和不存在 ID。<br>2. 提交消息并读取解析结果、Run 与审计日志。 |
| **预期结果** | 原消息正常落库；仅 A1 被解析并触发；已移出、跨群和不存在目标不触发且不泄露身份详情；无整条消息回滚。 |

### TC-M05-003: Mention 人类成员不会触发 Agent Runtime

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | U-B 与 A1 都是 G1 active members，且各自 Participant ID 可用于 token。 |
| **测试步骤** | 1. 发送仅 mention U-B 的消息。<br>2. 再发送同时 mention U-B 与 A1 的消息。<br>3. 检查 Run Registry、Commands 和公开消息。 |
| **预期结果** | 人类 token 保留在公开消息中但从 Agent 调度目标排除；第一条不创建 Run；第二条只为 A1 创建执行链路。 |

### TC-M05-004: 同一 Agent 的重复 Mention 按客户端首现顺序去重

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | A1、A2 均为 G1 active Agent members；客户端构造 `A2、A1、A2、A1` 的结构化 token 序列。 |
| **测试步骤** | 1. 发送该消息。<br>2. 读取标准化后的 Agent 目标列表。<br>3. 检查 Planning 输入与子 Run 数量。 |
| **预期结果** | 目标去重为 `[A2,A1]` 且保留首现顺序；每个 Agent 最多一个目标；不会因重复 token 产生重复子 Run 或 ACK。 |

### TC-M05-005: 单 Agent Mention 在一个事务中写入消息与启动事实

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1/S1/A1 均有效；可在事务提交前注入 Run Registry 或 start Command 写入失败。 |
| **测试步骤** | 1. 正常发送单 mention 消息并记录事务边界。<br>2. 分别注入 Run Registry、start Command 写失败后重试相同请求。<br>3. 检查消息、Run、Command 数量。 |
| **预期结果** | 正常路径在同一事务写公开消息、一个目标 Run 与一个 start Command；任一关键写失败时全部回滚；幂等重试不产生孤儿或重复事实。 |

### TC-M05-006: 多 Agent Mention 在一个事务中创建唯一 Planning Root

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1/S1 有至少两个有效 Agent members；可模拟 Planning Run 或 start Command 写失败。 |
| **测试步骤** | 1. 发送同时 mention A1、A2 的消息。<br>2. 检查公开消息、根 Run、目标快照与 Commands。<br>3. 注入失败并以相同幂等键重试。 |
| **预期结果** | 同一事务只创建一个 Planning Root 和一个 start Command，不直接创建两个独立根 Run；目标快照为去重后的 A1/A2；失败无残留，重试仍唯一。 |

### TC-M05-007: 历史消息展示和重放不会再次触发 Mention

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | S1 已有一条曾触发 A1 且执行完成的 mention 消息；记录原 Run/Command 数量。 |
| **测试步骤** | 1. 分页读取历史消息。<br>2. 刷新页面并重新订阅 websocket。<br>3. 执行历史回放或上下文重建，再次统计 Run/Command。 |
| **预期结果** | 历史消息按原 token 展示，但所有读取、重放和上下文构建均无调度副作用；Run/Command 数量不变。 |

### TC-M05-008: Agent 最终回复与 Callback 的 Structured Mention 继续形成可靠链路

| 项目 | 内容 |
|-|-|
| **优先级** | P0；BLOCKED / 待实现 |
| **前置条件** | G1/S1 有人类 U1 与 active Agent A1/A2；A1 的最终回复和一个 background Callback 分别包含指向 U1、A2 的稳定 Participant token。当前 ref 的两类交付均固定写 `mentions=[]`，尚无后续解析/调度链。 |
| **测试步骤** | 1. 交付 A1 最终回复。<br>2. 交付 background Callback。<br>3. 检查公开消息、mention token、U1 未读/提醒和 A2 的 Run/Command。<br>4. 重放两次 delivery。 |
| **预期结果** | 能力落地后，两类消息均保留稳定 Participant ID；@U1 只形成公开提醒/未读而不唤醒 Runtime；@A2 在消息、Run 与 start Command 同一可靠事务中创建一次后续执行；重放不重复消息、提醒或 Run。当前固定空 mentions 的实现只能登记阻塞，不能用普通文本 `@name` 冒充通过。 |

---

## M06 - ACK 与用户可见闭环

### TC-M06-001: ACK 只在 Run 与 start Command 提交后发送

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1/S1 单 mention A1；可暂停事务提交并观察 Runtime 消费与消息列表。 |
| **测试步骤** | 1. 在 Run Registry/start Command 尚未提交时读取消息。<br>2. 提交事务但暂停 Graph 执行。<br>3. 再次读取消息与 Command 状态。 |
| **预期结果** | 提交前没有 ACK；提交成功后即使 Graph 未开始也出现一条 ACK；不存在“用户看到 ACK 但没有可恢复 Run/Command”的窗口。 |

### TC-M06-002: ACK 是普通公开群消息且无专用动画协议

| 项目 | 内容 |
|-|-|
| **优先级** | P1 |
| **前置条件** | 准备一条单 Agent mention 和一条多 Agent 计划，其中一个 child ready、另一个因依赖尚未启动；可读取 REST/websocket 消息载荷。 |
| **测试步骤** | 1. 触发两类执行。<br>2. 检查 Planning Root、已创建 child Run、依赖阻塞 step 的 ACK 作者与数量。<br>3. 用普通消息列表读取 ACK。 |
| **预期结果** | 单 Agent Run 与真正创建/接受的业务 child Run 各由对应 Agent 写一条普通公开 ACK；Planning Root 本身、尚未创建的依赖 step 和 blocked descendant 不伪造 ACK；无需专用 message type、事件或前端动画协议。 |

### TC-M06-003: ACK 使用稳定 delivery key 保证幂等

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 已创建 Run R1；让 delivery worker 在提交前后各发生一次超时重试。 |
| **测试步骤** | 1. 连续两次处理 R1 的 ACK 交付。<br>2. 让相同消息从重复 Command 再处理一次。<br>3. 查询消息、delivery receipt 与事件。 |
| **预期结果** | 稳定键为 `run:{R1}:ack`；最终只有一条 ACK 和一份已存 receipt；重复处理返回同一结果，不追加消息。 |

### TC-M06-004: completed Run 交付一条最终公开回复

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | R1 已有 ACK，Graph 将以 completed 和可公开结果终止。 |
| **测试步骤** | 1. 推进 R1 到 completed。<br>2. 重放 terminal/delivery Command。<br>3. 查询 S1 消息及交付状态。 |
| **预期结果** | ACK 后出现一条 Agent 最终回复；终态、公开消息、delivery status/event 同步闭合；重放不产生第二条最终回复。 |

### TC-M06-005: waiting_user 可见提问而内部等待不重复 ACK

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 分别准备会进入 `waiting_user`、`waiting_external`、`waiting_agent` 的 Run。 |
| **测试步骤** | 1. 推进三类 Run 到等待态。<br>2. 观察公开消息与 Run 状态。<br>3. 恢复并再次进入同类等待态。 |
| **预期结果** | `waiting_user` 写一条清晰问题供人类继续；后两类是非终态且不向群里重复 ACK/内部状态；恢复过程保持同一 Run 身份。 |

### TC-M06-006: failed 与 cancelled 都形成安全的用户可见闭环

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 准备一个包含内部异常/敏感字符串的失败 Run，以及一个由用户或 Session 删除取消的 Run。 |
| **测试步骤** | 1. 分别推进到 failed、cancelled。<br>2. 读取公开群消息。<br>3. 检查日志与 API 是否泄露内部栈、模型提示或凭据。 |
| **预期结果** | 两种终态都只交付一条可理解的公开结果；失败内容经过脱敏，取消原因不暴露内部实现；不留下只有 ACK 无终态解释的悬挂体验。 |

### TC-M06-007: 最终消息、交付状态和事件在同一事务提交

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | R1 到达可交付终态；可分别在 ChatMessage、delivery status、delivery event 写入点注入异常。 |
| **测试步骤** | 1. 逐点注入失败并执行交付。<br>2. 检查事务回滚后的三类数据。<br>3. 清除故障后重试。 |
| **预期结果** | 三者要么全部提交，要么全部回滚；重试后只出现一套闭合记录；不会出现“消息已见但状态未交付”或相反的分裂事实。 |

### TC-M06-008: 前台原 Session 删除或 Agent 被移出时禁止静默回退

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 创建两个 foreground group Runs：R1 的原 Session 被删除；R2 的目标 Agent 在终态前被移出群。群内另有可用 primary。 |
| **测试步骤** | 1. 分别完成 R1/R2 并触发交付。<br>2. 检查原 Session、primary 和其他 Session。<br>3. 查看 delivery outcome。 |
| **预期结果** | 两者都 fail closed 且不向 primary/其他 Session 回退；群内不出现越权或错上下文回复；交付失败被持久化并可诊断。 |

---

## M07 - Multi-Agent Planning

### TC-M07-001: 单 Mention 旁路 Planning，多 Mention 进入唯一规划根

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1/S1 有 A1、A2 两个 active Agent members。 |
| **测试步骤** | 1. 发送仅 mention A1 的消息。<br>2. 发送同时 mention A1/A2 的消息。<br>3. 比较 Run kind、父子关系和 Commands。 |
| **预期结果** | 单目标直接创建 A1 Run，不调用规划模型；多目标只创建一个 Planning Root，后续子 Run 由计划调度，入口没有两个并列根。 |

### TC-M07-002: Planning 固定使用平台规划模型且禁用工具

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 配置 `MULTI_AGENT_PLANNING_MODEL_ID`，同时让业务 Agent 使用不同模型；另准备缺失配置场景。 |
| **测试步骤** | 1. 触发多 Agent Planning 并读取 Run 快照、模型请求和 tool schema。<br>2. 修改全局配置后恢复同一 Run。<br>3. 删除规划模型配置后新建请求。 |
| **预期结果** | Run 固定使用创建时 pin 的平台规划模型，不能调用工具，也不回退业务 Agent/Compact 模型；缺失配置时公开返回脱敏失败且不创建 child Runs。 |

### TC-M07-003: 规划结果支持 parallel、sequential 与 dependency 三种策略

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 可向规划解析器分别注入三种合法结构化计划。 |
| **测试步骤** | 1. 提交 parallel 计划。<br>2. 提交 sequential 计划。<br>3. 提交带显式 `depends_on` DAG 的 dependency 计划。<br>4. 检查标准化结果。 |
| **预期结果** | 三种策略均被接受并转换为明确 step ID、`agent_id`、任务和依赖；Participant ID 只用于候选快照与群身份校验；parallel 无依赖，sequential 形成顺序依赖，dependency 保留合法 DAG。 |

### TC-M07-004: 不安全或不完整计划在创建子 Run 前被拒绝

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 构造重复 step ID、未知 Agent、目标集合外 Agent、缺失任务、自依赖、环依赖和引用不存在 step 的计划。 |
| **测试步骤** | 1. 逐一提交异常计划。<br>2. 检查验证错误、修复轮次与 child Run 表。<br>3. 查询公开消息。 |
| **预期结果** | 所有异常在任何 child Run 创建前失败或进入受限修复；不会扩大到用户未 mention 的 Agent；最终失败仅交付脱敏系统消息。 |

### TC-M07-005: 用户显式给出的协作顺序优先于模型重排

| 项目 | 内容 |
|-|-|
| **优先级** | P1；LLM-E2E / Manual |
| **前置条件** | 真实规划模型环境中，消息明确要求“A1 完成后再由 A2 审核”并同时 mention A1/A2；当前实现依赖 prompt 与结构校验，没有确定性规则可单独证明该语义。 |
| **测试步骤** | 1. 触发 Planning。<br>2. 读取结构化计划及调度顺序。<br>3. 在 A1 未完成时轮询 ready steps。 |
| **预期结果** | 真实模型生成的标准化计划保留 A2 对 A1 的依赖，且 A1 未终态前 A2 不启动；结果作为 LLM 语义验收留存原始请求/计划证据，不能用 deterministic validator 单测替代或声称其必然保证用户意图。 |

### TC-M07-006: 无效计划最多修复两次后确定失败

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 规划模型连续返回三次无效结构；可记录模型调用与 Run 状态。 |
| **测试步骤** | 1. 提交多 mention 请求。<br>2. 让初次输出及两次修复均失败。<br>3. 检查调用次数、child Runs 和最终消息。 |
| **预期结果** | 初次验证失败后最多两轮修复，第三次无效即 Planning Root failed；没有 child Run；用户只看到一次脱敏失败闭环。 |

### TC-M07-007: Scheduler 只创建 ready step 且重复调度幂等

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 计划包含 S1 无依赖、S2 依赖 S1、S3 无依赖；准备重复 scheduler Command。 |
| **测试步骤** | 1. 首次调度计划。<br>2. 在 S1 未完成时重放 Command。<br>3. 完成 S1 并并发触发两次调度。 |
| **预期结果** | 首轮只为 S1/S3 各创建一个 child Run；重放不重复；S1 完成后只创建一个 S2 child Run；Planning Root 与 child 关系稳定。 |

### TC-M07-008: 失败依赖只阻塞后代而独立步骤继续并汇总

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 计划中 S2 依赖 S1，S3 独立；让 S1 failed、S3 completed。 |
| **测试步骤** | 1. 推进 S1/S3 到指定终态。<br>2. 重复发送 child-terminal 通知。<br>3. 检查 S2、Planning Root 恢复次数及公开结果。 |
| **预期结果** | S2 被标记为依赖阻塞且不创建 Run/ACK；S3 正常完成并仅有自己的 ACK/终态；每个 child 终态最多恢复根一次；Planning 最终失败/汇总由 system 身份只写一条公开消息，不由未启动 Agent 冒充。 |

---

## M08 - Mention Lane 与 A2A Cycle

### TC-M08-001: 同一 Agent 的群 Mention 使用唯一调度 Lane 和服务端位置排序

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 同租户多个群均包含 A1；准备多条 mention A1 的消息，其中至少两条 `created_at` 相同。 |
| **测试步骤** | 1. 在各群并发提交消息。<br>2. 读取每个 Run 的 scheduling lane key 和 origin Message Position。<br>3. 比较实际 claim 次序。 |
| **预期结果** | 所有群 mention A1 的执行归入 `group_mention:{tenant_id}:{agent_id}`；严格按服务端 `(created_at,id)` 排序，客户端到达顺序不能改写全序。 |

### TC-M08-002: 并发 Start 只领取最早且当前空闲的 Mention Lane

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 同一 lane 已有按位置排序的 R1、R2、R3 start Commands；启动两个 worker 并发 claim。 |
| **测试步骤** | 1. 同时执行 claim。<br>2. 检查 Commands、Run 状态和 lane lease。<br>3. 在 R1 未释放前再次轮询。 |
| **预期结果** | 只有位置最早的 R1 被领取；R2/R3 保持 pending 且不越过 R1；并发 worker 不会同时占有同一 lane。 |

### TC-M08-003: 非终态等待继续占用 Mention Lane

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | R1、R2 属于 A1 同一 lane，R1 可进入 `waiting_user`、`waiting_external` 或 `waiting_agent`。 |
| **测试步骤** | 1. 分别让 R1 进入三种等待态。<br>2. 每次轮询 R2 的 start Command。<br>3. 恢复 R1 后检查身份和 lane。 |
| **预期结果** | 三种等待均不是 lane 终点；R2 始终不启动；R1 恢复时沿用原 Run 和 lane，不生成并行替代 Run。 |

### TC-M08-004: 终态通过 Durable 状态释放 Lane 并可由 Reconciliation 修复

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | R1 在 lane 中运行，R2 pending；可让 terminal 后的即时调度信号丢失。 |
| **测试步骤** | 1. 将 R1 推进到 completed/failed/cancelled 之一。<br>2. 丢弃一次通知并运行 reconciliation。<br>3. 检查 R2 claim 与 projection。 |
| **预期结果** | Durable checkpoint/Run 终态成为释放依据；即使通知丢失，reconciliation 也只启动一次 R2；不得依赖可漂移的 read projection 决定 lane 是否空闲。 |

### TC-M08-005: Planning Root 在 Child Run 创建前不占 Agent Lane

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 一个多 mention 请求进入耗时 Planning，同时另一个单 mention 请求指向 A1。 |
| **测试步骤** | 1. 暂停 Planning 模型输出。<br>2. 提交单 mention A1。<br>3. 恢复 Planning，并观察其 A1 child Run 入队时点。 |
| **预期结果** | Planning Root 自身没有 A1 lane key，不阻塞单 mention；只有计划落定并创建 A1 child Run 后，该 child 才按自己的 Message Position 参与 lane。 |

### TC-M08-006: 非群 Mention 入口不错误加入 Mention Lane

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 分别准备 Direct Chat、Task、Trigger、Heartbeat 与普通 A2A Run，目标均为 A1。 |
| **测试步骤** | 1. 从五种入口创建 Run。<br>2. 检查注册时的 orchestration 与 scheduling 字段。<br>3. 与同时间 group mention A1 并发执行。 |
| **预期结果** | 五种入口都不生成 `group_mention` lane，也不被该 lane 串行化；只有由群 mention 计划产生的 child Run 进入对应 lane。 |

### TC-M08-007: A2A Cycle 只统计委托有向边并在第五次重复前拒绝

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 可构造数据库 parent chain；准备 A→B→C、A→B→A→B 以及使某有向边累计达到 5 次的链。 |
| **测试步骤** | 1. 逐一申请下一次普通 A2A 委托。<br>2. 检查有向边累计值。<br>3. 将 human/planning ancestor 插入链后重试。 |
| **预期结果** | A→B→C 不被误判；只累计 Agent delegation 边，human/planning ancestor 不算边；候选调用会让任一有向边总次数达到 5 时即拒绝且不创建子 Run。 |

### TC-M08-008: A2A Origin、租户和父链异常全部 fail closed

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 构造 origin Agent 与 parent 不匹配、断链、跨租户 parent、超深链和 visited node 重复场景。 |
| **测试步骤** | 1. 对每种异常申请委托。<br>2. 检查数据库读范围和错误。<br>3. 确认 Run、Command、公开消息是否新增。 |
| **预期结果** | 校验只信数据库 parent chain；任何身份不一致、断链、跨租户或遍历上限异常都拒绝新委托，不泄露链内容，也不产生半成品 Run/Command。 |

---

## M09 - 群上下文与成员工具

### TC-M09-001: 构建上下文前校验群、Session、发送者和目标 Agent

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 分别准备群已删除、Session 已删除或错群、发送者已移出、目标 Agent 已移出或身份不匹配的 checkpoint。 |
| **测试步骤** | 1. 逐一调用 group context builder。<br>2. 检查文件读取、工具装配与模型调用。<br>3. 查看错误信息。 |
| **预期结果** | 所有权威状态均在任何敏感读取前校验；异常一律 fail closed；不装配群工具、不调用模型、不泄露其他群或 Agent 内容。 |

### TC-M09-002: 群上下文按固定顺序稳定装配

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1/S1/A1 的规则、当前任务、Agent/群/Session 元数据、公告、摘要、最近消息、A1 memory、workspace 索引、Planning 信息与工具均有内容。 |
| **测试步骤** | 1. 构建两次等价 checkpoint 的上下文。<br>2. 记录各区块顺序和来源标识。<br>3. 比较序列化结果。 |
| **预期结果** | 顺序固定为规则→当前任务→Agent→群→Session→公告→摘要→最近 20 条→当前 Agent memory→workspace→Planning→工具；重复构建稳定。 |

### TC-M09-003: Recent Window 只含当前 Session 最近 20 条公开消息

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | S1 有 25 条公开消息和内部事件；同群 S2 及其他群各有消息。 |
| **测试步骤** | 1. 为 S1 构建上下文。<br>2. 读取 recent window 的消息 ID、顺序和内容类别。<br>3. 对照数据库全量消息。 |
| **预期结果** | 只选择 S1 最新 20 条公开消息并按最旧到最新排列；内部日志、S2 和其他群内容均不进入窗口。 |

### TC-M09-004: 当前触发消息在预算截断时仍被保留并标注来源

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 公告、摘要、memory、workspace 和 recent window 合计超过模型有效预算；当前 mention 消息位于窗口末端。 |
| **测试步骤** | 1. 构建上下文并触发预算裁剪。<br>2. 检查当前消息、被截断区块和来源说明。<br>3. 让模型请求回显可见源。 |
| **预期结果** | 当前触发消息绝不被裁掉；其他区块按设计上限截断并带来源/截断提示；不会把裁剪后的片段伪装成完整事实。 |

### TC-M09-005: 仅当前 Agent Memory 自动注入

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | A1、A2 均在 G1 且各有 memory；当前 Run 目标为 A1。 |
| **测试步骤** | 1. 构建 A1 上下文。<br>2. 搜索 A1/A2 memory 内容和路径。<br>3. 通过受权 memory 工具显式读取 A2。 |
| **预期结果** | 自动上下文只包含 A1 自己的 memory；A2 不被自动注入；A1 可按群权限显式读取 A2，但不能写 A2 memory。 |

### TC-M09-006: Workspace 默认只注入索引和短片段

| 项目 | 内容 |
|-|-|
| **优先级** | P1 |
| **前置条件** | G1 workspace 含多层目录、大文件、短文件和敏感测试标记；A1 有读取工具。 |
| **测试步骤** | 1. 构建 A1 上下文。<br>2. 检查 workspace 区块的目录、元数据和片段长度。<br>3. 通过工具显式读取一个文件。 |
| **预期结果** | 默认只注入受预算约束的索引/短片段，不无条件加载全空间或大文件；完整内容只在显式工具读取且权限校验通过后返回。 |

### TC-M09-007: 成员不全量注入且成员工具返回活跃稳定身份

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 有多名 active humans/agents，并有已移出成员；存在同名显示名称。 |
| **测试步骤** | 1. 构建默认上下文并搜索完整 roster。<br>2. 调用成员查询工具。<br>3. 比较返回 ID、类型、状态和排序。 |
| **预期结果** | 默认上下文不塞入完整成员清单；工具只返回当前群 active members，以稳定 Participant ID 区分同名者，不返回已移出或跨群身份。 |

### TC-M09-008: 群工具只对校验后的快照开放且目标身份必须精确匹配

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 准备合法 G1/A1 checkpoint，以及 group/target Agent 被篡改、缺少权威 snapshot、跨租户的变体。 |
| **测试步骤** | 1. 分别执行 Runtime 工具装配。<br>2. 尝试调用成员、workspace、memory 和 announcement 工具。<br>3. 检查副作用。 |
| **预期结果** | 只有合法且已冻结的群快照获得群工具；target Agent Participant 身份必须与 Run 精确一致；异常变体无工具和写副作用。 |

---

## M10 - Announcement、Memory 与 Workspace

### TC-M10-001: Announcement 使用固定路径且只允许人类成员编辑

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | U-A 与 A1 均为 G1 active members；文件服务可观察 `groups/G1/system/announcement.md`。 |
| **测试步骤** | 1. U-A 创建并更新公告。<br>2. A1 尝试通过工具写公告。<br>3. 非成员和跨租户用户尝试读写。<br>4. 检查当前文件、文件索引与 revision history。 |
| **预期结果** | 当前公告的权威内容位于固定文件路径；active human 可编辑，Agent、非成员和跨租户主体被拒；数据库可保存受控的 revision history 正文，但不另建第二份当前公告正文或动态 storage key。 |

### TC-M10-002: Announcement 按 Run 快照读取并受上下文上限约束

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 有超长公告；R1 已冻结旧 revision，随后人类更新为新 revision，再创建 R2。 |
| **测试步骤** | 1. 分别构建 R1/R2 上下文。<br>2. 检查公告 revision、注入长度和截断提示。<br>3. 通过受权工具读取未注入的剩余内容。 |
| **预期结果** | R1 保持旧快照，R2 读取最新公告；默认上下文按上限截断并明确提示，不承诺无条件全文注入；剩余内容可通过权限工具按需读取。 |

### TC-M10-003: Agent 可读群内 Peer Memory 但只能写自己的 Memory

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | A1、A2 为 G1 active Agent members；各自 memory 位于 `groups/G1/agents/{agent_id}/memory/memory.md`；U-A 为 active human。 |
| **测试步骤** | 1. A1 读 A1/A2 memory。<br>2. A1 写自己的 memory 并尝试写 A2。<br>3. U-A 读写两者。<br>4. 移出 A1 后重试。 |
| **预期结果** | A1 可读群内两者但只可写 A1；active human 可按产品权限管理群 memory；A1 被移出后全部拒绝；路径和审计始终绑定 G1。 |

### TC-M10-004: Group Workspace 跨 Session 共享并通过 Revision 防止静默覆盖

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 有 S1/S2；U-A、A1 分别从不同 Session 访问同一 workspace 文件；当前群 API 仅提供 list/read/write/delete 与 optimistic version。 |
| **测试步骤** | 1. S1 创建文件并记录 revision/version。<br>2. S2 读取并用当前 version 更新。<br>3. 两方用同一旧 version 并发写入。<br>4. 在其他群查找该文件。 |
| **预期结果** | workspace 以 group 为作用域，S1/S2 看到同一文件；并发写只有一方成功，另一方收到 stale-version 冲突而不覆盖赢家；其他群不可见；Session 删除不删除共享文件。 |

### TC-M10-005: Workspace 已实现操作拒绝路径穿越与过期写入

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 workspace 有合法文件及 revision R1；准备 `../`、绝对路径、编码穿越和 stale revision 请求。当前 ref 未提供群 workspace move、symlink 或 edit-lock API。 |
| **测试步骤** | 1. 通过 list/read/write/delete 逐一提交逃逸路径。<br>2. 用 R1 更新已推进到 R2 的文件。<br>3. 检查群外文件、当前内容与 revision 数。 |
| **预期结果** | 所有逃逸路径在规范化后拒绝；stale write 返回冲突且不覆盖 R2；群外文件无读写，失败不产生新 revision；move、symlink 与 edit-lock 不得被本用例误报为现有能力。 |

### TC-M10-006: 人类跨空间复制必须 Preview、Confirm 并保留来源

| 项目 | 内容 |
|-|-|
| **优先级** | P1 |
| **前置条件** | 对应跨空间复制后端与前端能力已部署，U-A 对源内容和目标 G2 都有权限；当前 ref 尚无完整闭环时以 `Manual-Browser / BLOCKED-待实现` 执行，不声称已有自动化。 |
| **测试步骤** | 1. 在浏览器选择源文件/片段并请求复制预览。<br>2. 核对目标、范围、敏感提示和来源信息。<br>3. 未确认时退出，再确认一次。<br>4. 检查 G2 文件与审计。 |
| **预期结果** | 未确认不落地；确认后只复制预览范围，目标生成独立 revision 并记录源空间/源对象与操作者审计；Agent 不能替代人类完成显式确认。 |

### TC-M10-007: 跨空间复制拒绝整空间、无权限内容和自动同步

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 跨空间复制能力已部署；源空间含可分享文件、私有文件、他人消息和第三方渠道内容，目标为 G2。当前 ref 缺失能力时登记 `BLOCKED/待实现`。 |
| **测试步骤** | 1. 请求复制整个空间。<br>2. 分别选择四类内容复制。<br>3. 成功复制可分享文件后修改源文件。<br>4. 观察目标 revision。 |
| **预期结果** | 整空间复制和无授权、私有、他人消息、第三方渠道内容均拒绝；只允许用户有权分享的明确范围；源更新不会自动同步或联动目标版本。 |

### TC-M10-008: 群解散后的文件清理任务最终硬删且可幂等重试

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 有公告、workspace、各 Agent memory 和上下文文件；异步 hard-delete worker 已实现并启用。当前 ref 只证明立即不可见时，最终清理部分登记 `BLOCKED/待实现`，不得推断已完成。 |
| **测试步骤** | 1. manager 解散 G1 并立即尝试访问文件。<br>2. 运行清理 worker，分别在中途失败和成功后重放同一任务。<br>3. 检查对象存储、`workspace_file_revisions.before_content/after_content`、Context 与最小审计元数据。 |
| **预期结果** | 解散后用户侧立即不可见；worker 最终硬删当前公告/workspace/memory、group-scoped revision history 正文、Context 及相关文件，重试不误删其他群；仅按设计保留最小 membership/audit 元数据。 |

---

## M11 - Session Compact 与 Topic

### TC-M11-001: Group Session 只维护一份共享 Context State

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1/S1 有 A1、A2 两个 Agent；尚无 `session_context_states` 记录。 |
| **测试步骤** | 1. 由 A1 首次触发上下文写入。<br>2. 由 A2 读取并推进 compact。<br>3. 查询 S1 的全部 context state。 |
| **预期结果** | S1 只有一条共享记录且 `agent_id=null`；A1/A2 读取同一摘要和 watermark；不会为每个 Agent 分叉群 Session 摘要。 |

### TC-M11-002: Compact 始终保留最近 20 条公开消息

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | S1 有至少 45 条公开消息并穿插内部事件，已存在旧 summary。 |
| **测试步骤** | 1. 计算 compactable 消息集合。<br>2. 执行 compact。<br>3. 构建下一次上下文并核对 summary 与 recent window。 |
| **预期结果** | 只有最近 20 条之前的公开消息进入 compact batch；最新 20 条完整保留并按序注入；内部事件不被当作公开对话摘要输入。 |

### TC-M11-003: Watermark 使用 Message Identity 并能从缺失或异群位置重建

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 准备合法 watermark message ID、已删除/不存在 ID、属于 S2 或其他租户的 ID，以及相同时间戳消息。 |
| **测试步骤** | 1. 分别加载 S1 context state。<br>2. 解析 watermark 对应 `(created_at,id)`。<br>3. 执行 compact/rebuild 并读取新状态。 |
| **预期结果** | 合法 ID 解析为稳定 Message Position；缺失、异 Session 或异租户 watermark 不被盲信而触发安全重建；同时间戳仍由 id 确定边界。 |

### TC-M11-004: 并发 Compact 通过 Version 与 Watermark CAS 防止覆盖赢家

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 两个 worker 从相同 context version/watermark 读取 S1，并生成不同完成时序。 |
| **测试步骤** | 1. 让 W1 先提交新摘要。<br>2. 让 W2 用旧 expected version/watermark 提交。<br>3. 重试 W2 并检查 watermark 单调性。 |
| **预期结果** | W1 CAS 成功；W2 stale CAS 不覆盖赢家；重试基于最新状态继续；version 和 watermark 只前进不回退。 |

### TC-M11-005: Compact 固定使用平台 Compact Model 且缺失时不回退

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 配置 `MULTI_AGENT_COMPACT_MODEL_ID`，业务 Agent 使用其他模型；另准备 Compact 配置缺失或不可用场景。 |
| **测试步骤** | 1. 执行一次正常 compact 并记录模型与 tools。<br>2. 修改 Compact 模型配置后触发下一次独立 compact。<br>3. 删除配置后再触发 compact。 |
| **预期结果** | 每次 compact 都解析并只调用当时配置的 `MULTI_AGENT_COMPACT_MODEL_ID`，禁用业务副作用工具；实现不承诺跨 compact 工作 pin 旧配置；配置缺失/不可用时保留旧状态并失败可诊断，不回退业务 Agent 或 Planning 模型。 |

### TC-M11-006: Compact 阈值取最小有效预算的 85% 且不切断消息

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 群内包含 active/runnable Agent，也包含 removed/expired/disabled/Private、未配置 `primary_model_id`、以及引用缺失/禁用/跨租户模型的 Agent；准备消息数提前触发、85% token 硬阈值和多批长消息。独立 pending-token 提前阈值尚未实现。 |
| **测试步骤** | 1. 只按当前有效群 Agent 重算最小上下文预算。<br>2. 分别验证未配置 model ID 与非法 model 引用。<br>3. 触发消息数与 85% 检查并执行多批 compact。<br>4. 若验收独立 pending-token 提前触发则登记 `BLOCKED/待实现`。 |
| **预期结果** | removed/expired/disabled/Private 或未配置 `primary_model_id` 的 Agent 被排除并在集合变化后重算；已配置但引用缺失、禁用或跨租户模型时 fail closed 为 `session_compact_budget_unavailable`；当前只有消息数可提前触发、达到 85% token 必须触发，不能声称已有独立 pending-token 阈值；始终保留最近 20 条完整消息。 |

### TC-M11-007: Compact 失败保留旧 Context 且不阻塞新消息

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 已有可用旧 context；分别注入 provider 失败、自由文本替代结构化 commit tool 和持久化失败。 |
| **测试步骤** | 1. 异步执行各失败场景。<br>2. 同时发送和读取新消息。<br>3. 检查错误码、context version/watermark 与模型调用。 |
| **预期结果** | 对话读写不被 compact 阻塞；所有失败都保留旧 context 和 watermark；自由文本不能提交状态；provider failure 统一呈现 `session_compact_model_failed` 且不切换到其他模型；重试次数/分类若未形成契约，不在本用例中臆测。 |

### TC-M11-008: Compact 语义契约完整且 Topic 只作为内部摘要

| 项目 | 内容 |
|-|-|
| **优先级** | P1；LLM-E2E / Manual |
| **前置条件** | S1 含事实、决策、待办、未决问题、已废弃结论、同名 Participant、workspace path、冲突信息、内部工具过程，并在 S2 放置干扰内容。当前 validator 只校验六字段结构，不确定性校验这些语义。 |
| **测试步骤** | 1. 执行 S1 compact。<br>2. 读取结构化摘要、Participant/workspace 引用和冲突表达。<br>3. 搜索独立 topic 表、字段、CRUD API 与 UI。 |
| **预期结果** | 真实模型语义验收中，摘要区分事实、决策、待办、未决与废弃内容，保留稳定 Participant ID/workspace path，冲突标为未决，并排除 S2 与内部工具过程；当前结构 validator 通过不等于语义通过，需保留原始输入/输出人工评审。topic 仅作为内部摘要，不形成独立表/API/UI。 |

---

## M12 - 未读、回调、删除与审计

### TC-M12-001: 未读游标按成员和 Session 存入 JSON Read State

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | U-A 是 G1 active member；G1 有 S1/S2 和多条公开消息。 |
| **测试步骤** | 1. U-A 只读到 S1 中间位置。<br>2. 完整读取 S2。<br>3. 查询 `group_members.session_read_state` 与未读数。 |
| **预期结果** | 同一 membership 的 JSON 分别保存 S1/S2 watermark identity；两会话互不覆盖；未读按各自 Message Position 计算。 |

### TC-M12-002: 延迟或重复 Read Update 不能让 Watermark 回退

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | U-A 对 S1 先读取较新位置 P2；准备延迟到达的旧位置 P1 和两个并发 update。 |
| **测试步骤** | 1. 提交 P2。<br>2. 并发/延迟提交 P1 与重复 P2。<br>3. 读取 JSON state 和未读数。 |
| **预期结果** | 更新在 membership 行锁和位置比较下保持单调；最终仍是 P2；重复更新幂等，未读数不会因乱序请求突然增加。 |

### TC-M12-003: 未读包含 Agent 最终回复与 Callback 且排除发送者和内部事件

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | S1 有人类消息、Agent ACK/最终回复、后台 callback、内部 Runtime event、workspace/announcement 变更；U-A/U-B 水位相同。 |
| **测试步骤** | 1. 让 U-A 发送人类消息。<br>2. 生成其余各类事件。<br>3. 比较 U-A/U-B 未读增量。 |
| **预期结果** | 公开 ACK、Agent 最终回复和 callback 计入其他成员未读；发送者不为自己的消息增加未读；内部 event、workspace 与 announcement 变更不计入。 |

### TC-M12-004: Background Callback 保留 Origin Metadata 并优先回原 Session

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | Task/Trigger/Heartbeat 或普通 A2A background Run 从 G1/S1 发起并持久化 origin，S1 仍活跃。 |
| **测试步骤** | 1. 完成 background Run。<br>2. 检查 delivery target、公开消息、origin metadata 与 `source_channel`。<br>3. 重放 delivery。 |
| **预期结果** | 结果首先交付回 G1/S1，并携带可审计的 group/session/channel origin；重放幂等；不会因当前 primary 变化而改投其他 Session。 |

### TC-M12-005: 原 Session 不可用时 Background 只回退同群 Primary

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 两个 background group Runs 的 origin Session 已删除；R1 所在 G1 有 active primary，R2 所在 G2 无 primary；另有其他群可用 Session。 |
| **测试步骤** | 1. 分别完成 R1/R2。<br>2. 检查所有候选 Session 和 delivery outcome。<br>3. 重试交付。 |
| **预期结果** | R1 仅回退到同一 G1 的 active primary；R2 持久化 delivery failed，不跨群、跨 scope 或自动创建 Session；重试结果稳定。 |

### TC-M12-006: Origin Metadata 不合法或原目标结果 Unknown 时均禁止猜测 Fallback

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 变体 A：origin metadata 缺失、group/session 不匹配或租户不一致；变体 B：原目标已收到写请求但响应丢失，持久化 `original_target_outcome=unknown` 并有稳定幂等键。 |
| **测试步骤** | 1. 分别触发两类终态交付。<br>2. 观察同群 primary 和最近活跃 Session。<br>3. 对变体 B 使用原幂等键对账原目标，再检查 receipt 与公开消息。 |
| **预期结果** | metadata 非法时立即 fail closed；unknown 不被当作“原 Session 已确认不存在”，系统先对账原目标且在确认失败前不选择任何 fallback；两类场景都不向错误会话写消息并留下可诊断结果。 |

### TC-M12-007: 删除 Session 取消前台协作但不取消独立 Background Run

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | S1 绑定 foreground root、Planning orchestration、其 delegated descendants，以及独立 Task/Trigger background Runs。 |
| **测试步骤** | 1. manager 删除 S1。<br>2. 检查 cancel Commands 和各 Run 状态。<br>3. 重放删除请求并让 background Runs 完成。 |
| **预期结果** | foreground/orchestration 及从它们派生的 delegated Runs 收到幂等取消；独立 background Runs 继续；重复删除不生成重复取消或审计副作用。 |

### TC-M12-008: 群管理审计复用 Audit Logs 且只保留必要元数据

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 可执行建群、改名、邀请、移出、公告更新、Session 删除、跨空间复制与群解散；测试内容含敏感字符串。未实现能力的步骤标记 `BLOCKED/待实现`。 |
| **测试步骤** | 1. 依次执行各管理动作和一次拒绝动作。<br>2. 查询 `audit_logs`、业务表及是否存在群专用 audit 表。<br>3. 检查 actor、target、tenant、结果与 payload。 |
| **预期结果** | 已实现动作复用统一 `audit_logs` 并记录最小可追踪元数据；无第二套群审计事实源；不保存完整消息/文件正文、模型提示或密钥；解散后仅保留设计允许的最小审计/成员元数据。 |

---

## M13 - API、安全、并发、性能与发布

### TC-M13-001: 群聊 REST 路由覆盖核心资源并返回稳定领域错误

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 启动实现基线 API；准备合法与非法请求，涵盖 `/groups`、members、sessions、messages、announcement、memory、summary、workspace。 |
| **测试步骤** | 1. 枚举设计中的 GET/POST/PATCH/PUT/DELETE 路由。<br>2. 对每类资源执行成功、未认证、未授权、不存在、冲突和校验失败请求。<br>3. 比较错误载荷。 |
| **预期结果** | 已实现路由与方法符合设计且核心消息入口能保存消息并解析 mention；领域错误使用稳定 code/status/schema，不向客户端暴露 ORM、堆栈或内部路径。 |

### TC-M13-002: Tenant、Group、Session 与 Participant 组合校验全部 fail closed

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | T-A/T-B 各有群、Session、Participant 与文件；准备合法 ID 的跨作用域拼接请求。 |
| **测试步骤** | 1. 交叉替换 tenant、group、session、member/agent/message ID。<br>2. 请求详情、发消息、读 memory/workspace、删除资源。<br>3. 检查响应差异和副作用。 |
| **预期结果** | 服务端逐层校验归属而不只验证 ID 存在；所有错配均拒绝且不泄露对象是否真实存在；无跨租户读取、写入、Run 或审计成功记录。 |

### TC-M13-003: 具备稳定幂等标识的消息与 Runtime 交付保持 Exactly-Once 效果

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 可固定 message ID、Runtime Command ID、ACK/final delivery key 与 channel outbox key；群创建、邀请、删除 Session 的 HTTP 输入当前没有通用 idempotency key。 |
| **测试步骤** | 1. 在响应丢失前后重放 mention 消息、Command、ACK、terminal delivery 与 scheduler Command。<br>2. 并发提交同一稳定标识。<br>3. 统计 Run、Command、公开消息与 receipts；另记录普通 CRUD 客户端的重试限制。 |
| **预期结果** | 有稳定标识的每个逻辑操作只形成一套事实和一次用户可见结果，不产生重复 Run、ACK、最终回复或 receipt；普通群 CRUD 在接口补充幂等契约前不得被声称 exactly-once，客户端也不得对未知结果盲目自动重放。 |

### TC-M13-004: 关键并发不变量在竞争下仍由数据库守住

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 准备同群 primary 选举、同 Agent lane claim、Participant ensure、read watermark 和 context CAS 的并发测试屏障。 |
| **测试步骤** | 1. 对每个场景启动至少两个同步竞争事务。<br>2. 重复多轮并随机化提交顺序。<br>3. 检查最终约束与失败事务范围。 |
| **预期结果** | 最多一个 active primary、一个 lane owner 和一个 Participant；watermark 不回退，stale context 不覆盖赢家；冲突局部回滚且无死锁后遗留或半成品。 |

### TC-M13-005: 消息分页与高基数关键查询使用稳定位置和目标索引

| 项目 | 内容 |
|-|-|
| **优先级** | P1 |
| **前置条件** | 生成高基数消息、Run、read state 与 mention lane 数据；数据库统计信息已更新。群/Session 列表当前没有已定义的分页参数或数值 SLO。 |
| **测试步骤** | 1. 用消息 cursor 分页，并在页间插入同时间戳消息。<br>2. 检查消息、未读、lane claim 与最近 20 条查询的执行计划。<br>3. 记录可复现的吞吐/延迟基线。 |
| **预期结果** | 消息 cursor 使用稳定 `(created_at,id)`，页间无重复或漏项；关键查询命中设计索引且无无界扫描；性能数据作为基线记录，不虚构未定义的群/Session 分页契约或发布 SLO。 |

### TC-M13-006: 文件输入、Prompt 内容和失败输出不突破安全边界

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 准备路径穿越、超大输入、恶意 prompt injection、伪造 mention、HTML/Markdown 注入以及包含 token/密钥/堆栈的工具错误；当前实现 ref 没有群聊前端变更。 |
| **测试步骤** | 1. 从消息、announcement、memory、workspace 和模型工具入口提交载荷。<br>2. 触发 planning/runtime/delivery 失败并检查 API、日志和审计。<br>3. 前端实现后用 Manual-Browser 检查 HTML/Markdown 渲染；在此之前登记 `BLOCKED/待实现`。 |
| **预期结果** | 后端权限与路径校验不被 prompt 绕过，结构化 mention 不接受文本伪造，密钥、内部 prompt、绝对路径和堆栈不出现在 API/日志公开面；前端安全渲染必须单独通过浏览器验收，当前 ref 不得以 API 测试代替或报告通过。 |

### TC-M13-007: Unified Chat 迁移按维护窗口整批切换且禁止双写混跑

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 使用生产等价备份的隔离环境；已准备迁移、回滚/恢复手册和所有 writer 清单。 |
| **测试步骤** | 1. 停止 Web、worker、scheduler 等全部旧 writer。<br>2. 执行 schema audit、backfill 与约束迁移。<br>3. 验证后仅启动新版本 writer。<br>4. 模拟兼容审计失败。 |
| **预期结果** | 迁移期间没有旧新版本双写；tenant/identity 审计失败时在破坏性写入前停止；成功后只有统一模型写路径；恢复步骤可回到一致快照而非混合 schema。 |

### TC-M13-008: 群聊用户主链路执行 Manual-Browser 验收

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 对应群聊前端已实现并部署到测试环境；实现 ref 本身无前端变更，因此在仅部署该 ref 时本用例必须登记 `BLOCKED/待实现`，不得声称自动化覆盖。 |
| **测试步骤** | 1. 以 Manual-Browser 创建群、邀请成员、创建/切换 Session。<br>2. 发送单/多 mention 并观察 ACK、等待与终态。<br>3. 验证未读、公告、workspace、删除和权限错误。<br>4. 刷新重进并核对历史。 |
| **预期结果** | 在前端能力就绪后，主链路与 API 状态一致、刷新可恢复、无专用 ACK 动画依赖，权限和错误可理解；当前 ref 缺前端时结果只能是明确阻塞而非通过。 |

---

## 附录 - Commit 与现有自动化入口

> 下表只用于执行定位。“现有入口”表示实现 ref 中已有相关 pytest 文件，不代表本模块 8 条用例均已自动化覆盖；待实现和浏览器边界仍按正文前置条件执行。

| 用例模块 | 主要实现演进 | 现有自动化入口 |
|-|-|-|
| M01-M04 | `f6e3c12a..77b9eb4d`（基础 Schema / Unified Chat）、`b5a7080d..cc2a871d`（Group Chat） | `test_group_schema.py`、`test_unified_chat_schema.py`、`test_participant_identity.py`、`test_group_api.py`、`test_group_chat_service.py`、`test_chat_sessions_api.py` |
| M05-M07 | `b5a7080d..cc2a871d`（消息、Mention、Planning） | `test_group_message_service.py`、`test_agent_runtime_group_scheduling.py`、`test_agent_runtime_planning.py`、`test_agent_runtime_planning_scheduler.py` |
| M08-M09 | `e4e69c3e..3c3f58a4`（Durable Runtime）、`b5a7080d..cc2a871d`（群调度与上下文） | `test_agent_runtime_persistence.py`、`test_agent_runtime_cycle_guard.py`、`test_agent_runtime_group_context_builder.py`、`test_agent_runtime_group_tools.py` |
| M10-M12 | `4b5b8bd3..63bfb760`（Context / Compact）、`1ef6dbf7..0ef0f8d4`（Delivery / Outbox） | `test_group_file_service.py`、`test_session_context_service.py`、`test_agent_runtime_session_context_compactor.py`、`test_agent_runtime_delivery.py` |
| M13 | `5f67c169..6bd635bb`（入口硬切换）及上述完整实现范围 | 上述后端 pytest；前端主链路为 `Manual-Browser`，当前 ref 无前端变更 |
