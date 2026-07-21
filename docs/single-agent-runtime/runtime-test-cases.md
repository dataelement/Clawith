# Clawith 单 Agent Runtime 测试用例

> 更新依据：工作区三份 PRD / 设计文档；当前 checkout 为 `feature/unified-chat-single-agent-runtime-group-chat@0ef0f8d4`（相对 upstream/main 共 102 commits），并把其直接后续生产启动修复 `c959dffe` 纳入发布验收。PRD 灰度表述与已移除 legacy loop 的实现冲突时按 fail-closed 执行；Directory 与 Toolathlon benchmark 不纳入。本文按以下结构组织：模块目录 + 编号用例 + 优先级 / 前置条件 / 测试步骤 / 预期结果。

## 目录

| 模块编号 | 模块名称 | 用例数量 |
|---|---|---:|
| M01 | 范围与唯一 Runtime | 8 |
| M02 | 统一聊天模型强制迁移 | 8 |
| M03 | Runtime / Checkpoint Schema 与模型能力 | 8 |
| M04 | Command、Worker 与 Thread Lock | 8 |
| M05 | Graph 路由、模型契约与 Verify | 8 |
| M06 | Wait、Resume、Cancel 与重启恢复 | 8 |
| M07 | 工具幂等账本与 Tool Exchange | 8 |
| M08 | Runtime Context 构造与隔离 | 8 |
| M09 | Session Compact | 8 |
| M10 | Run Compact、Token Budget 与 Failover | 8 |
| M11 | Projector、Events 与 Reconciliation | 8 |
| M12 | Origin Session、交付与 Outbox | 8 |
| M13 | Web、Task、Trigger、Schedule 与 Heartbeat | 8 |
| M14 | A2A 与 OpenClaw | 8 |
| M15 | 全部外部渠道 | 8 |
| M16 | Security、Deploy 与 Observability | 8 |
| **合计** | **16 个模块** | **128** |

## M01 - 范围与唯一 Runtime

### TC-M01-001: 产品 Run 状态查询与 Checkpoint 内部实现解耦

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 准备 running、waiting、completed 但 delivery failed 三类 Run；公开 `get_run_state` / Run API 尚未落地时，本用例登记 `BLOCKED/产品缺口`。 |
| **测试步骤** | 1. 以有权限用户查询三类 Run。<br>2. 以跨租户用户查询。<br>3. 检查响应字段及数据来源。 |
| **预期结果** | 产品接口返回 goal、source、execution、wait、result、delivery 等稳定状态，并明确区分执行完成与交付失败；跨租户请求 fail closed；接口读取业务投影而不暴露 checkpoint blob、Graph 内部字段或密钥；当前 ref 缺公开 API 时不得报告通过。 |

### TC-M01-002: 所有生产入口只进入统一 Runtime Adapter

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 可读取目标 ref 的 Web、Task、Trigger、Schedule、Heartbeat、A2A 和渠道入口代码。 |
| **测试步骤** | 1. 逐类触发一个新执行请求。<br>2. 跟踪入口落库和调用链。<br>3. 搜索入口是否直接调用多轮模型 / 工具循环。 |
| **预期结果** | 每个入口先持久化 Run Registry 与 Runtime Command，再由 Worker 推进 Graph；入口代码没有独立 tool loop，也不绕过 Adapter 直接推进 checkpoint。 |

### TC-M01-003: LangGraph Checkpoint 是唯一执行状态源

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 准备一个 checkpoint 为 waiting_user、但 agent_runs.projected_execution_status 被人工改成 completed 的 Run。 |
| **测试步骤** | 1. 提交合法 resume Command。<br>2. 观察 Worker 的恢复依据和 Graph 下一节点。<br>3. 查询完成后的投影。 |
| **预期结果** | Worker 从 checkpoint 的 waiting / interrupt 恢复，不采信 completed 投影；Graph 推进后 Projector 再用新 checkpoint 修正查询投影。 |

### TC-M01-004: 新 Run 开关未命中时 fail closed

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Runtime 新 Run 全局开关关闭，Agent allowlist 与 source type 均不命中；准备可观察的模型和工具桩。 |
| **测试步骤** | 1. 分别从 Web、Task、Trigger 和 A2A 发起新执行。<br>2. 检查返回 / occurrence 状态、数据库写入和桩调用次数。 |
| **预期结果** | 各入口给出明确拒绝或失败结果；模型和工具桩调用均为 0；不会进入已删除的 legacy loop，也不会产生半创建的可执行 Run。 |

### TC-M01-005: 已有 LangGraph Run 不受新 Run 开关影响

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 已有 runtime_type=langgraph、固定 graph_name / graph_version 且处于 waiting 的 Run；随后关闭全部新 Run 开关。 |
| **测试步骤** | 1. 用正确 run_id 和 correlation_id 提交 resume。<br>2. 让 Worker 消费 Command。<br>3. 读取新 checkpoint。 |
| **预期结果** | Resume 被接受并使用该 Run 固化的 Graph 版本继续；新 Run 开关只控制 intake，不中断或改写既有 Thread。 |

### TC-M01-006: 历史 legacy Run 不被新 Worker 推进

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 构造 runtime_type=legacy 的历史 Run Registry，并准备 resume / cancel 输入。 |
| **测试步骤** | 1. 分别提交 resume 与 cancel。<br>2. 运行 Command Worker 一个领取周期。<br>3. 检查 checkpoint、Command 状态和旧执行桩。 |
| **预期结果** | Adapter 在写新 Command 前返回 runtime_v2_disabled / runtime_type_mismatch；Worker 不创建 LangGraph Thread、不转换 runtime_type，也不调用旧循环。 |

### TC-M01-007: Run、Thread 与 Graph 版本身份固定

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 创建一个 LangGraph Run，并在创建后修改全局默认 graph_version。 |
| **测试步骤** | 1. 比较 agent_run.id 与 runtime_thread_id。<br>2. 中断该 Run。<br>3. 修改默认版本后恢复。 |
| **预期结果** | runtime_thread_id 精确等于 run_id；恢复继续加载创建时固化的 graph_name / graph_version，不静默切到新版本。 |

### TC-M01-008: Adapter 边界拒绝跨 Run 上下文

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 存在同租户两个 Run A/B；构造 handle 指向 A、runtime context 却携带 B 的 run_id。 |
| **测试步骤** | 1. 调用 stream_run 或 Graph driver。<br>2. 观察数据库、checkpoint 和事件流。 |
| **预期结果** | 在任何 checkpoint 读取或 Graph 执行前拒绝请求；A/B 均不产生新 checkpoint、事件或外部副作用。 |

---

## M02 - 统一聊天模型强制迁移

### TC-M02-001: 新库一次性创建统一聊天 Schema

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 空 PostgreSQL 测试库，迁移版本落后于 202607131910_unify_chat_schema。 |
| **测试步骤** | 1. 执行 Alembic upgrade 到目标 revision。<br>2. 反射 chat_sessions、chat_messages 及关联索引 / 约束。<br>3. 用 Direct、外部群和平台群各插入一组合法数据。 |
| **预期结果** | 统一表、字段、FK、唯一键和索引与目标 metadata 完全一致；三类会话均能用统一 ChatSession / ChatMessage 表达。 |

### TC-M02-002: 迁移前先审计再有序回填

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 旧 Schema 含可迁移的 direct / group session、message、participant 和 mention 数据；打开 SQL 记录器。 |
| **测试步骤** | 1. 执行统一聊天迁移。<br>2. 按执行顺序收集审计、回填、约束创建语句。<br>3. 核对迁移后行数和关联。 |
| **预期结果** | tenant / identity 等审计全部先于任何回填；回填按依赖顺序完成；消息、会话、参与者和 mention 无丢失、无孤儿。 |

### TC-M02-003: 租户归属歧义时迁移零写入失败

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 旧数据中放入无法唯一确定 tenant 的 session 或 message，并记录所有 DML。 |
| **测试步骤** | 1. 执行迁移。<br>2. 捕获异常。<br>3. 比较迁移前后表内容及 schema revision。 |
| **预期结果** | 在首次 backfill 前因明确审计错误失败；没有数据被改写，revision 不前进，错误能定位异常记录。 |

### TC-M02-004: 部分升级的 Schema 继续收敛到最终形态

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 手工构造仅包含部分最终 message identity 字段、但缺其余列 / 索引 / 约束的中间 Schema。 |
| **测试步骤** | 1. 运行迁移。<br>2. 检查迁移是否错误短路。<br>3. 与目标 metadata 做逐项 diff。 |
| **预期结果** | 已存在的单个字段不会让迁移误判完成；缺失对象全部补齐，已有兼容数据保留，最终 diff 为空。 |

### TC-M02-005: 已是最终 Schema 时 check-first no-op

| 项目 | 内容 |
|---|---|
| **优先级** | P1 |
| **前置条件** | 数据库已处于目标统一聊天 revision，表内有业务数据；开启 DDL / DML 记录。 |
| **测试步骤** | 1. 再次执行迁移检查逻辑。<br>2. 比较 schema、数据校验和数据库日志。 |
| **预期结果** | 校验成功且不重复建表、回填或改写业务行；数据校验和保持不变。 |

### TC-M02-006: Message Position 统一按 created_at 与 id 排序

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 同一 session 创建相同 created_at、不同 UUID 的多条消息，并设置 watermark。 |
| **测试步骤** | 1. 查询消息列表和最近窗口。<br>2. 从 watermark 读取增量。<br>3. 重复执行以验证稳定性。 |
| **预期结果** | 所有路径均按 (created_at, id) 得到相同稳定顺序；不会按 UUID 大小单独判断先后，也不跳过同时间消息。 |

### TC-M02-007: 新会话语义存在时 downgrade fail closed

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 统一 Schema 中写入旧 Schema 无法表达的新 session 类型 / identity 语义。 |
| **测试步骤** | 1. 执行目标 migration downgrade。<br>2. 记录 DDL 执行顺序。<br>3. 检查数据和 revision。 |
| **预期结果** | Downgrade 在任何破坏性 DDL 前拒绝；数据和 revision 保持原状，并明确指出不可逆语义。 |

### TC-M02-008: 非空 mention 数据阻止破坏性 downgrade

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 统一聊天表中存在至少一条有效 mention 记录。 |
| **测试步骤** | 1. 执行 downgrade。<br>2. 检查 mention、message、session 行与约束。 |
| **预期结果** | Mention guard 明确拒绝降级；未删除任何 mention、消息或会话，且不留下半降级 Schema。 |

---

## M03 - Runtime / Checkpoint Schema 与模型能力

### TC-M03-001: 五张 Runtime 支撑表契约完整

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 空 PostgreSQL 测试库，准备 Runtime migration 与目标 SQLAlchemy metadata。 |
| **测试步骤** | 1. 执行 Runtime migration。<br>2. 反射 agent_runs、agent_run_commands、agent_run_events、session_context_states、agent_tool_executions。<br>3. 核对字段、check、FK、唯一键和索引。 |
| **预期结果** | 五表全部符合目标契约；agent_runs 仅含 Registry / 投影 / 交付事实，不出现可独立推进的第二套执行状态机字段。 |

### TC-M03-002: Runtime migration 可补缺但拒绝不兼容结构

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 分别准备缺少一张表 / 一个索引的兼容库，以及字段类型或约束冲突的不兼容库。 |
| **测试步骤** | 1. 对兼容库运行 migration。<br>2. 对不兼容库运行同一 migration。<br>3. 比较两库写入。 |
| **预期结果** | 兼容库只补齐缺失对象；不兼容库在修改业务数据前 fail closed，并报告精确冲突。 |

### TC-M03-003: Checkpoint 使用独立 Schema 与固定 Thread ID

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 配置 PostgreSQL 主 DSN，未单独配置 checkpoint DSN；准备 run UUID。 |
| **测试步骤** | 1. 解析 checkpoint URL。<br>2. 创建 saver 并检查 search_path。<br>3. 为 run 写入 / 读取 checkpoint。 |
| **预期结果** | 使用主 DSN 作为 fallback，但强制 search_path 指向 langgraph_checkpoint；thread_id 精确等于 run UUID；业务 public schema 不出现 checkpoint 表。 |

### TC-M03-004: c959dffe 规范化 asyncpg SSL 参数

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 分别准备 ssl=require / prefer / disable 等受支持 asyncpg 查询参数的 PostgreSQL DSN。 |
| **测试步骤** | 1. 逐个调用 checkpoint DSN 规范化。<br>2. 交给 psycopg 解析。<br>3. 核对其他 query option 与 search_path。 |
| **预期结果** | SSL 值被稳定转换为 psycopg 可接受的 sslmode；无重复或丢失其他参数；独立 checkpoint schema 仍被强制。 |

### TC-M03-005: 冲突 SSL 配置在启动前 fail closed

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | DSN 同时包含语义冲突的 asyncpg ssl 与 psycopg sslmode，且 ALLOW_MIGRATION_FAILURE=false。 |
| **测试步骤** | 1. 运行 checkpoint bootstrap。<br>2. 观察后端 entrypoint 是否继续启动 Runtime Worker。 |
| **预期结果** | Bootstrap 给出明确冲突错误并非零退出；不创建 / 修改 checkpoint 表，Runtime Worker 和 Web 服务均不进入可接收执行的状态。 |

### TC-M03-006: Checkpoint bootstrap 串行且先于 Worker

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 两个并发后端启动实例指向同一未初始化 PostgreSQL，setup 可注入一次失败。 |
| **测试步骤** | 1. 同时启动两个 setup。<br>2. 验证 advisory lock 竞争。<br>3. 注入 saver.setup 失败并再次启动。<br>4. 检查 entrypoint 顺序。 |
| **预期结果** | 任一时刻仅一个 setup 修改 schema；失败后锁被释放；entrypoint 始终在 Alembic 成功后执行 checkpoint setup，setup 成功后才启动 Worker。 |

### TC-M03-007: Checkpoint 加密序列化契约

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 准备合法 AES key、非法长度 key、允许的 Runtime dataclass，以及含不可序列化对象的 State。 |
| **测试步骤** | 1. 对合法 State 做加密 round-trip。<br>2. 尝试非法 key。<br>3. 尝试未 allowlist 类型。 |
| **预期结果** | 合法值无损恢复且 tuple 等类型保持；非法 key 和未知类型在写 checkpoint 前被拒绝；错误中不回显密钥。 |

### TC-M03-008: 模型输入能力与预算 fail closed

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 在现有 capability 缓存中准备仅独立 max_input、仅共享 context_window、两者都有、两者均未知四种模型能力；provider discovery/refresh 当前位于本模块范围外。 |
| **测试步骤** | 1. 用相同 requested_max_output_tokens 计算 request_input_limit。<br>2. 修改现有人工 override 并重新计算。<br>3. 尝试用未知能力模型启动新 Runtime。<br>4. discovery/refresh 能力落地后再验证刷新合并；此前登记 `BLOCKED/待实现`。 |
| **预期结果** | 当前已实现预算解析满足：独立输入上限不重复扣输出，共享窗口只扣本次输出，两者取较小值，能力未知时显式拒绝；不得把 provider 自动发现、刷新或“override 永不被刷新覆盖”误报为当前 ref 已实现。 |

---

## M04 - Command、Worker 与 Thread Lock

### TC-M04-001: 入口事务原子写 Run Registry 与 start Command

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 打开调用方数据库事务，准备一条合法用户消息或 TriggerExecution，并让 Worker 通知失败。 |
| **测试步骤** | 1. 在同一事务调用 intake。<br>2. 提交事务。<br>3. 检查 Registry、Command 和来源事实。<br>4. 运行 pending Command 扫描。 |
| **预期结果** | 三类事实同成同败；通知失败不丢任务；扫描可领取同一 pending start，且不会创建第二个 Run。 |

### TC-M04-002: Command 幂等键阻止重复输入

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 同一 run_id 准备两个 idempotency_key 相同的 start 或 resume 请求。 |
| **测试步骤** | 1. 并发提交两个请求。<br>2. 运行多个 Worker。<br>3. 检查 Command 行、checkpoint 和执行次数。 |
| **预期结果** | unique(run_id, idempotency_key) 只保留一个有效 Command；Graph 只应用一次，重复方返回同一稳定结果。 |

### TC-M04-003: Command 按顺序 claim 并持续续租

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 同一 Run 按时间创建 start、resume、cancel，并准备超过 60 秒的模型 stream。 |
| **测试步骤** | 1. 启动两个 Worker claim。<br>2. 观察 FOR UPDATE SKIP LOCKED 结果。<br>3. 让首个调用跨过 claim TTL。 |
| **预期结果** | 同一 Run 只按 created_at 顺序消费；后续 Command 保持 pending；续租任务独立运行，长 stream 期间 claim 不被错误抢占。 |

### TC-M04-004: Checkpoint 已提交但 Command 未 applied 时只对账

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 注入故障：Graph 已写含 command_id 的 checkpoint，Worker 在更新 Command 前退出。 |
| **测试步骤** | 1. 等待 claim 过期。<br>2. 由新 Worker 领取。<br>3. 记录 Graph invoke 次数和 Command 状态。 |
| **预期结果** | 新 Worker 从 checkpoint 识别 Command 已应用，只补写 applied / applied_checkpoint_id；Graph invoke 次数不增加。 |

### TC-M04-005: 双 Worker 只有 advisory lock 持有者推进 Thread

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 两个 Worker 同时领取同一 Run 可重领的 Command，并监控 checkpoint 读写。 |
| **测试步骤** | 1. 同时尝试获取 run_id 派生的 session-level advisory lock。<br>2. 让赢家推进一次 Graph。<br>3. 检查输家行为。 |
| **预期结果** | 仅赢家在同一专用连接持锁期间读取并推进 Graph；输家不读投影、不 invoke，释放 claim 供后续对账。 |

### TC-M04-006: Worker 崩溃后数据库连接释放 Thread Lock

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Worker A 已持锁并完成一个 checkpoint，随后模拟进程 / 连接异常退出。 |
| **测试步骤** | 1. 终止 Worker A 连接。<br>2. 启动 Worker B 领取过期 claim。<br>3. 从最新 checkpoint 继续。 |
| **预期结果** | PostgreSQL 自动释放锁；Worker B 能获得同一 lock key，从最新 checkpoint 续接，不从头重跑已完成节点。 |

### TC-M04-007: 无 checkpoint 的 resume 被确定性拒绝

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Run Registry 存在，但对应 Thread 从未成功写入首个 checkpoint；创建 resume Command。 |
| **测试步骤** | 1. 让 Worker 领取 resume。<br>2. 检查 Graph driver、Command 和投影。 |
| **预期结果** | Worker 不把 resume 自动转换成 start；Command 标记 rejected 并给出 missing_checkpoint，Graph 和投影不被伪造推进。 |

### TC-M04-008: Checkpoint 未确认 Command ID 时重试并在 Attempts 耗尽后要求对账

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 将 Command max attempts 设为 3；Graph invoke 返回但 checkpoint 缺少本次 command_id，另准备 checkpoint 的 run/tenant 身份不匹配场景。 |
| **测试步骤** | 1. 运行 Worker 并检查 applied 判定。<br>2. 让缺 command_id 场景重复领取至 attempt_count=3。<br>3. 再运行 claim/reconciliation 并检查告警。 |
| **预期结果** | Command 不被误标 applied；身份不匹配时不执行；阈值内缺 command_id 可释放 claim 重试且不重复确认；attempt_count 达上限后不再被普通 claim，必须进入 checkpoint reconciliation_required/告警路径而不是静默遗失或无限重跑。 |

---

## M05 - Graph 路由、模型契约与 Verify

### TC-M05-001: Graph 按固化配置编译并持久化生命周期

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 配置合法 graph_name / graph_version 与测试 checkpointer，准备最小 start Command。 |
| **测试步骤** | 1. 在进程 lifespan 构建 compiled graph。<br>2. 启动 Run 直至 terminal。<br>3. 读取 checkpoint history。 |
| **预期结果** | Graph 在 lifespan 构建并复用，不在每次请求重复 compile；生命周期变化均存在 checkpoint，terminal checkpoint 状态合法。 |

### TC-M05-002: Graph 只接受合法路由与生命周期组合

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 准备 tool_calls、wait、finish、error 四类模型意图，以及未知 route / 非法状态组合。 |
| **测试步骤** | 1. 对四类合法意图逐一执行 control route。<br>2. 输入未知 route。<br>3. 输入 completed 后继续 execute_tools 等非法组合。 |
| **预期结果** | 合法意图分别进入 execute_tools、interrupt、verify、handle_error；未知或非法组合在产生副作用前 fail closed。 |

### TC-M05-003: call_model 每个节点只做一次模型调用

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 模型桩先返回普通文本、后返回合法 finish；开启调用计数与节点 trace。 |
| **测试步骤** | 1. 推进第一个 call_model checkpoint。<br>2. 让 Graph 根据协议决定下一步。<br>3. 完成第二次模型调用。 |
| **预期结果** | 每次 call_model node 仅调用 provider 一次；多轮由 Graph 节点 / checkpoint 驱动，不在 LLM caller 内形成隐藏循环。 |

### TC-M05-004: 模型输出只允许 tool_calls、wait 或 finish

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 准备合法三类输出、自由文本、缺字段 JSON 和未知意图；工具账本为空。 |
| **测试步骤** | 1. 逐一送入模型输出解析器。<br>2. 对无副作用的非法输出执行允许次数内修复。<br>3. 超过修复上限。 |
| **预期结果** | 仅三类合法意图进入 Graph；非法结构只在无副作用时有界修复，超限后显式 failed / waiting，不无限提示模型。 |

### TC-M05-005: finish 必须经过 verify 才能完成

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 模型返回 finish，但存在未处理 tool error 或验收条件未满足。 |
| **测试步骤** | 1. 提交 finish。<br>2. 观察 checkpoint lifecycle 与 verify 输入。<br>3. 修复条件后再次验证。 |
| **预期结果** | 首次进入 verifying，不能直接 completed / deliver；verify 记录具体失败；条件满足后才进入 finalize 和 terminal completed。 |

### TC-M05-006: 当前 Deterministic Verify 与任务专用验收能力边界明确

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 使用 ref 默认 `DeterministicRuntimeVerifier`；分别准备空 finish、仍有 pending_tool_calls、非空 finish 且无 pending tools。任务专用 verifier 注册尚未形成生产闭环。 |
| **测试步骤** | 1. 依次验证三种候选。<br>2. 记录 verification result。<br>3. 将“工具证据→任务专用 verifier→入口验收条件”作为目标能力执行前置检查；未实现时登记 `BLOCKED/待实现`。 |
| **预期结果** | 当前默认 verifier 只确定性拒绝空 finish 与 pending tool calls，并允许非空且无 pending tools 的候选；不得声称已执行任务专用验收链。目标 verifier 注册完成后，模型自述才必须接受工具/任务证据的进一步约束。 |

### TC-M05-007: Finalize 只生成 checkpoint 状态与交付请求

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 一个已通过 verify 的 Run；对 agent_runs.projected_*、ChatMessage 和 delivery service 建立写入监控。 |
| **测试步骤** | 1. 单独执行 finalize。<br>2. 在 checkpoint 提交前注入退出。<br>3. 正常提交后执行 side-effect handler。 |
| **预期结果** | Finalize 本身只返回 terminal State、result summary、SessionContextDelta 和稳定 delivery request；checkpoint 未提交不交付，提交后才幂等投影 / 交付。 |

### TC-M05-008: Graph 拒绝跨范围 Context 与活动态终止

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 构造 run_id / tenant_id 不匹配的 RuntimeContext，以及 END 前 lifecycle_status=running 的 State。 |
| **测试步骤** | 1. 分别执行 Graph。<br>2. 检查 checkpoint、事件和外部桩。 |
| **预期结果** | 跨范围 Context 在数据库读取前被拒；Graph 不能以 active lifecycle 进入 END；两种异常均不产生交付或工具副作用。 |

---

## M06 - Wait、Resume、Cancel 与重启恢复

### TC-M06-001: waiting_user 生成可恢复 JSON interrupt

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 模型产生当前契约支持的 `waiting_type=user`、reason 与 question；模型不提供 correlation_id。 |
| **测试步骤** | 1. 推进统一 `wait` 节点并进入内部 `wait_for_resume`。<br>2. 读取最新 checkpoint snapshot / interrupts。<br>3. 查询产品投影。 |
| **预期结果** | Runtime 后端生成稳定 correlation_id，并将 waiting_type、reason、question 与 correlation 写入 interrupt；Run 身份由 checkpoint/thread 外层上下文确定，不要求 interrupt payload 重复 run_id；当前契约没有 expected_input_schema，不得虚构该字段。权威 lifecycle 为 waiting_user。 |

### TC-M06-002: waiting_agent 与 waiting_external 从真实 Callback、Webhook 或 Timer 精确恢复

| 项目 | 内容 |
|---|---|
| **优先级** | P0；真实入口 E2E |
| **前置条件** | 分别准备等待 Agent callback 和外部 webhook/timer 的 Run，并把 projected_waiting_type 改错；若部署仅有抽象 resume service、没有真实 webhook/timer adapter，则对应步骤登记 `BLOCKED/集成缺口`。 |
| **测试步骤** | 1. 从真实 callback、webhook 和到期 timer 入口发送匹配事件。<br>2. 让 Worker 读取 checkpoint。<br>3. 用错误 correlation 重放一次并观察路由。 |
| **预期结果** | 入口先持久化精确 resume Command；恢复类型和后端 correlation 只按 checkpoint interrupt 校验，错误投影不阻断、不放宽恢复；合法事件从原 Thread 继续，错误/重复事件不推进第二次。 |

### TC-M06-003: 用户 Resume 校验 actor、Run 与 correlation

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Session 内有一个 waiting_user Run；准备正确用户、其他租户用户、正确 / 错误 correlation_id。 |
| **测试步骤** | 1. 用错误 actor 提交。<br>2. 用错误 correlation 提交。<br>3. 用全部正确字段提交。 |
| **预期结果** | 前两次在 Graph 前拒绝且不泄露等待内容；正确输入与用户消息在同一事务写 resume Command，并恢复精确 Run。 |

### TC-M06-004: 重复 Resume 只推进一次

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 同一 waiting Run 准备两个相同 idempotency_key 的 resume callback。 |
| **测试步骤** | 1. 并发提交 callback。<br>2. 模拟首次已写 checkpoint、Command 未 applied。<br>3. 再投递一次。 |
| **预期结果** | 唯一键与 checkpoint 内 command_id 双重去重；Graph 只离开 interrupt 一次，后续请求返回第一次应用结果。 |

### TC-M06-005: Cancel 在新模型或工具开始前生效

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Running Run 即将进入 call_model 或 reserve 下一工具；已持久化有效 cancel Command。 |
| **测试步骤** | 1. 执行 control guard。<br>2. 记录 provider / tool 调用。<br>3. 读取 checkpoint 与 Command。 |
| **预期结果** | 新模型请求或工具 reservation 不启动；checkpoint 转为 cancelled 并包含 cancel command_id 后，Command 才标 applied。 |

### TC-M06-006: 工具已 started 时先记录真实结果再取消

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 第一个副作用工具 receipt 已是 started，第二个工具尚未 reservation；期间提交 cancel。 |
| **测试步骤** | 1. 让第一个工具返回 succeeded / failed / unknown 任一真实结果。<br>2. 再执行 guard。<br>3. 检查第二个工具。 |
| **预期结果** | 第一个 receipt 按真实结果落库，不伪造回滚；第二个工具不启动；随后 Run 安全进入 cancelled，unknown 事实仍保留用于对账。 |

### TC-M06-007: 服务重启从同一 checkpoint 恢复

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Run 已完成工具副作用并在 wait interrupt 持久化；停止全部 Worker / Web 进程。 |
| **测试步骤** | 1. 重启服务并通过 bootstrap。<br>2. 提交合法 resume。<br>3. 核对节点、工具 receipt 和最终结果。 |
| **预期结果** | 新进程从原 thread / interrupt 节点恢复；已成功工具复用 receipt 不重做；Run 从等待后的下一安全路径继续。 |

### TC-M06-008: WebSocket 断线不取消，重连按游标恢复

| 项目 | 内容 |
|---|---|
| **优先级** | P1 |
| **前置条件** | Web Run 正在 stream，客户端在收到部分事件后断开；保存最后 RuntimeEvent 的 (created_at, id) 游标。 |
| **测试步骤** | 1. 断开 socket，不发 abort。<br>2. 让后台 Run 继续到 waiting 或 terminal。<br>3. 使用游标重连并调用 get_run_state。 |
| **预期结果** | 断线不生成 cancel Command；Run 后台继续；重连只补发游标后的事件并读取当前状态，不重复 terminal / delivery。 |

---

## M07 - 工具幂等账本与 Tool Exchange

### TC-M07-001: 工具执行前原子 reservation 为 started

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 准备 external_write 工具调用，含稳定 tool_call_id、assistant_message_id 和 JSON 参数。 |
| **测试步骤** | 1. 调用 reserve。<br>2. 在工具桩入口检查数据库。<br>3. 工具成功后 settle。 |
| **预期结果** | 工具调用前已存在唯一 started receipt，参数 hash / effect / retry 元数据完整；成功后原行转 succeeded 并保存摘要 / result_ref。 |

### TC-M07-002: succeeded receipt 恢复时复用且不重做

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 同一 run_id + tool_call_id 已有 succeeded receipt，工具桩若调用即失败。 |
| **测试步骤** | 1. 让恢复后的 execute_tools 再遇到该 call。<br>2. 构造 ToolMessage。<br>3. 检查桩与账本。 |
| **预期结果** | Runtime 直接复用既有结果 / 引用并重建合法 ToolMessage；工具桩调用为 0，receipt 不生成第二行。 |

### TC-M07-003: 并发 reservation 只有一个执行赢家

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 两个事务并发 reserve 同一 run_id + tool_call_id，参数一致；另准备参数 hash 冲突场景。 |
| **测试步骤** | 1. 同时提交一致请求。<br>2. 让唯一赢家执行。<br>3. 再用相同 key、不同参数请求。 |
| **预期结果** | 数据库唯一键 / savepoint 保证只有一个执行者；一致输家复用状态；参数冲突在任何外部调用前 fail closed。 |

### TC-M07-004: 失败重试按 effect 与 retry_policy 严格 gate

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 准备 read+safe、write+conditional、external_write+never 三类已 failed receipt。 |
| **测试步骤** | 1. 分别请求 retry。<br>2. 记录 reservation 与实际工具调用。 |
| **预期结果** | 仅明确 read+safe 满足策略时可重试；写和外部写不因调用方要求而越权重试；拒绝原因可诊断。 |

### TC-M07-005: 副作用结果 unknown 禁止自动重试

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 外部写工具超时且无法确认对端结果，receipt 标记 unknown。 |
| **测试步骤** | 1. 重启 Worker 并恢复工具节点。<br>2. 运行正常 retry / compact / context rebuild。<br>3. 检查外部调用计数。 |
| **预期结果** | 所有自动路径都阻塞并进入 interrupt / reconciliation；外部调用计数不增加，必须人工或工具专用对账后处理。 |

### TC-M07-006: Parallel Tool Exchange 作为单个原子 Block

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 一条 assistant 消息含 A/B/C 三个 calls，三条 results 完整，最近 20 条边界切在 Block 中间。 |
| **测试步骤** | 1. 构建消息 Blocks。<br>2. 从尾部选择窗口。<br>3. 运行完整性校验。 |
| **预期结果** | A/B/C 与全部 result 同进同出，必要时窗口扩展超过 20；原顺序、真实 call ID 和一对一关系保持。 |

### TC-M07-007: 不完整 Tool Exchange 默认 fail closed

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 分别构造缺首 / 中 / 末 parallel result、orphan result、重复 ID、started receipt 与账本冲突。 |
| **测试步骤** | 1. 对每组构建 Block。<br>2. 尝试发送 Provider。<br>3. 检查重建 / 对账分支和工具调用。 |
| **预期结果** | 整个 group 不进入模型；可证明的 succeeded 才重建，否则进入 reconciliation / interrupt；Provider Adapter 报错而不静默删项，工具不被重做。 |

### TC-M07-008: 超预算完整 Exchange 整组摘要且 watermark 安全

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 完整已结束 Tool Exchange 超过硬 token budget，后方另有 pending / started / unknown Block。 |
| **测试步骤** | 1. 执行窗口选择与 Run Compact。<br>2. 检查 run_summary 和 covered_through_run_message_id。<br>3. 恢复执行。 |
| **预期结果** | 完整 Block 整组移出并摘要 call ID、工具名、effect、状态、result_ref；watermark 不跨后方不安全 Block；恢复不重做已完成工具。 |

---

## M08 - Runtime Context 构造与隔离

### TC-M08-001: 新 Run 固化最新 Session Context 与最近消息

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Session Context version=12，watermark 后有 25 条用户可见消息，其中窗口边界包含完整 Tool Exchange。 |
| **测试步骤** | 1. 创建新 foreground Run。<br>2. 读取首个 checkpoint 的两个 snapshot。<br>3. 核对消息顺序和数量。 |
| **预期结果** | 固化 version 12 的 Session Context 和默认最近 20 条消息；优先保留最近用户原话，只有为完整 Tool Exchange 才允许少量扩展。 |

### TC-M08-002: Resume 复用原快照并只追加明确输入

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Run A 已固化 version=12 并 waiting；并行 Run B 把 Session Context 更新到 version=13。 |
| **测试步骤** | 1. 对 A 提交用户 resume payload。<br>2. 构建 A 的下一次模型输入。<br>3. 检查 Session snapshot 和新增输入。 |
| **预期结果** | A 继续使用 version 12，不静默混入 version 13 或 B 的消息；仅本次明确 resume payload 作为新 Run 输入追加。 |

### TC-M08-003: 模型输入按稳定顺序组装

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Static context、Session snapshot、Current Run、Related Run、recent session/run message 和入口输入均非空。 |
| **测试步骤** | 1. 调用 Context Builder。<br>2. 标记每段起止位置。<br>3. 检查大正文处理。 |
| **预期结果** | 顺序精确为 Static → Session → Current Run → Related → Recent Session → Recent Run → 当前输入；大正文只进摘要 / 引用。 |

### TC-M08-004: Current Run 使用 checkpoint 状态而非查询投影

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Checkpoint 显示 verifying 且有 verification_result，业务投影仍为 running 且错误为空。 |
| **测试步骤** | 1. 构建 Runtime Context。<br>2. 检查 Current Run 段。 |
| **预期结果** | 注入 verifying 和真实 verification_result；Context Builder 不读取 projected_execution_status 决定执行语义。 |

### TC-M08-005: Context Builder 只注入 Payload 显式提供的 Related Run 摘要

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Command payload 由 Planning Scheduler 显式携带 dependency `related_run_summaries`；数据库另有 parent、child、无关 active、terminal 与长期 waiting Run。 |
| **测试步骤** | 1. 用该 payload 构建 Runtime Context。<br>2. 检查入选 ID、字段与数据库查询。<br>3. 不提供 related summaries 再构建一次。 |
| **预期结果** | 仅显式 payload 中的依赖摘要与 artifact refs 入选；Context Builder 不自动从 DB 发现 parent/child/dependency，也不因同 Session 批量注入其他 Run；若产品需要通用关系查询器，必须另行实现并验收。 |

### TC-M08-006: Soft delete Session 后不再读取上下文

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Session 已 soft delete；其前台 Run 与独立来源 background Run 各有 checkpoint snapshot。 |
| **测试步骤** | 1. 尝试为新 Run 查询该 Session Context。<br>2. 恢复两个旧 Run。<br>3. 监控 Context 数据库查询。 |
| **预期结果** | 新 Run 不得注入已删 Session；前台协作进入 cancel；background Run 只用已固化 checkpoint，不再读取该 Session 最新摘要 / 消息。 |

### TC-M08-007: 无 Session 的后台 Run 使用显式空 Context

| 项目 | 内容 |
|---|---|
| **优先级** | P1 |
| **前置条件** | 创建 session_id=null 的合法 background Run。 |
| **测试步骤** | 1. 执行 prepare_context。<br>2. 读取 checkpoint 和模型输入。 |
| **预期结果** | session_context_snapshot 与 recent_session_messages_snapshot 是显式、可序列化的空结构；不会猜测 primary Session 或读取其他会话。 |

### TC-M08-008: Thread 历史与运行对象不得进入模型 / Checkpoint

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Thread 有多条历史 checkpoint；runtime context 含 DB session、client、token 和大工具正文。 |
| **测试步骤** | 1. 构建模型输入和待持久化 State。<br>2. 检查序列化结果。<br>3. 搜索敏感值与历史 checkpoint 内容。 |
| **预期结果** | 模型只收到最新可注入状态；State 不含数据库连接、client、token 或大正文，后者仅保留引用；历史 checkpoint 不进入 prompt。 |

---

## M09 - Session Compact

### TC-M09-001: 新增消息达到阈值触发 Session Compact

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Session 已有 version=3 的摘要；watermark 后新增消息数刚好达到配置阈值。 |
| **测试步骤** | 1. 在阈值前运行调度检查。<br>2. 写入最后一条消息后再次检查。<br>3. 执行 Compact 并查询 state。 |
| **预期结果** | 阈值前不触发，达到阈值后只创建一次 Compact 工作；成功后同一 session 记录滚动更新为 version=4。 |

### TC-M09-002: Token 预算触发 Compact，用户显式总结入口单独标记实现缺口

| 项目 | 内容 |
|---|---|
| **优先级** | P1 |
| **前置条件** | 准备消息数未达阈值但 Session Context Pack 超预算的会话，以及用户显式“总结当前会话”的请求；当前 ref 没有从该用户 intent/API 到 `SessionContextService` 的入口。 |
| **测试步骤** | 1. 对超预算会话运行已实现触发器。<br>2. 尝试从用户请求进入显式总结。<br>3. 记录 Compact 工作与普通 Run。 |
| **预期结果** | Token 超预算确定性触发 Session Compact 且不删除原消息；显式总结在入口实现前登记 `BLOCKED/待实现`，不得把普通 Agent 回答或直接 service 单测冒充该产品链路。 |

### TC-M09-003: Session Compact 输出严格遵守 v1 Schema

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 输入含已确认要求、决策、open item、证据和 workspace 引用；模型桩可返回合法 / 缺字段 / 自由文本结果。 |
| **测试步骤** | 1. 分别执行三种输出。<br>2. 校验 schema_version、列表字段、watermark、expected_version 和引用权限。 |
| **预期结果** | 仅完整 session_context_v1 结构可进入 CAS；缺字段或自由文本不写库、不启动无界 repair；关键字面量和引用被保留。 |

### TC-M09-004: Watermark 增量按 Message Position 读取

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Watermark 消息与后续消息含相同 created_at、UUID 字典序与时间顺序相反；另准备跨 Session watermark。 |
| **测试步骤** | 1. 解析 watermark 的 (created_at, id)。<br>2. 查询增量。<br>3. 换成跨 Session / 缺失 watermark。 |
| **预期结果** | 增量严格使用 Message Position，不比较 UUID 大小；非法 watermark 进入重建路径，不猜测覆盖范围。 |

### TC-M09-005: 并发 Compact 使用 version 与 watermark 双 CAS

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 两个 Worker 同时读取 version=7 和同一 watermark，分别生成不同候选摘要。 |
| **测试步骤** | 1. 并发提交两个 CAS。<br>2. 让输家重新读取胜者版本。<br>3. 重新合并输家的增量。 |
| **预期结果** | 只有一个候选从 7 更新到 8；输家不能覆盖新摘要，必须基于 version=8 与新 watermark 重新 compact / merge。 |

### TC-M09-006: Compact 失败保留旧摘要并回退最近消息

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 已有可用 Session Context；让 Compact 模型超时、输出非法或 CAS 重试耗尽。 |
| **测试步骤** | 1. 触发 Compact。<br>2. 构建下一个新 Run Context。<br>3. 查询 state 和原消息。 |
| **预期结果** | 旧 state / version / watermark 不被破坏；新 Run 使用旧摘要加最近 20 条合法用户可见消息兜底；原 ChatMessage 全部保留。 |

### TC-M09-007: Terminal Run 只提交幂等 SessionContextDelta

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Run 已 terminal，finalize 产生 source_run_id、terminal_checkpoint_id 和一份 Delta。 |
| **测试步骤** | 1. 首次处理 terminal side effect。<br>2. 重放同一 checkpoint。<br>3. 用同 run_id、不同 terminal checkpoint 尝试替换。 |
| **预期结果** | Delta 与 receipt 原子提交且仅合并一次；同 checkpoint 重放 no-op；不同 checkpoint 不能覆盖已确认 receipt。 |

### TC-M09-008: Session Context 只能由服务合并且保持会话隔离

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 准备 Direct Session A/B、共享 Group Session 和来自错误 tenant / run 的 Delta。 |
| **测试步骤** | 1. 尝试让 Graph 直接覆盖 state。<br>2. 通过 SessionContextService 合并各 Delta。<br>3. 查询四个 Context。 |
| **预期结果** | Graph 直接覆盖被拒；Delta 必须校验 tenant、session、source run 与引用；A/B 不串写，共享 Group state 的 agent_id 为空。 |

---

## M10 - Run Compact、Token Budget 与 Failover

### TC-M10-001: 达到有效输入预算 85% 触发 Run Compact

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 已知 primary model 能力、输出额度、static / tool / runtime reserve 与 safety margin；构造 84.9% 和 85.0% 两组输入。 |
| **测试步骤** | 1. 分别计算 effective_runtime_budget 与占用率。<br>2. 执行 compact_run_if_needed。 |
| **预期结果** | 84.9% 不因该阈值强制压缩；85.0% 触发；计算基于本次请求实际输出额度和剩余有效输入预算。 |

### TC-M10-002: Waiting、Resume 过大与 Verify 循环可提前压缩

| 项目 | 内容 |
|---|---|
| **优先级** | P1 |
| **前置条件** | 分别构造进入 waiting、resume 后上下文过大、verify / repair 达配置轮数但未到 85% 的 Run。 |
| **测试步骤** | 1. 推进三类 Run 到触发点。<br>2. 检查 compact 决策和 checkpoint。 |
| **预期结果** | 三类提前条件按配置触发稳定 Run Summary；压缩只处理 Run 语义历史，不改变 lifecycle / interrupt 精确状态。 |

### TC-M10-003: Run Compact 输出只更新 Summary 与安全 Watermark

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Run 内含 goal、进度、决策、阻塞、证据、产物、下一步及稳定 run_message_id。 |
| **测试步骤** | 1. 执行 Compact。<br>2. 校验输出 Schema。<br>3. 比较压缩前后 Graph State。 |
| **预期结果** | 输出仅为结构化 run_summary 与 covered_through_run_message_id；已覆盖旧消息按 reducer 删除，Session Context 与历史 checkpoint 不被修改。 |

### TC-M10-004: 精确恢复字段永不被摘要删除

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | State 含 pending writes、pending_tool_calls 精确参数、waiting_request、resume payload、verification_result 和 checkpoint metadata。 |
| **测试步骤** | 1. 让这些字段周围的 run_messages 超预算。<br>2. 执行一次或分批 Compact。<br>3. 从 checkpoint 恢复。 |
| **预期结果** | 所有精确字段逐字保留且可恢复；仅可安全沉淀的模型 / 工具历史被摘要，Graph 路由结果不变。 |

### TC-M10-005: Watermark 不跨 pending、started、unknown 或 malformed Block

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 依次排列完整 Block、pending、started、unknown、malformed Block，并给每条消息稳定 ID。 |
| **测试步骤** | 1. 运行 Run Compact。<br>2. 读取 watermark 和保留窗口。<br>3. 再次 compact。 |
| **预期结果** | Watermark 最多停在最后一个完整安全 Block；四类不安全 Block 不被跨越或删除；重复 Compact 不改变这一边界。 |

### TC-M10-006: 正常执行预算只按 Primary Model 计算

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Primary 有大窗口，fallback 有小窗口；Primary 调用正常。 |
| **测试步骤** | 1. 构建首次模型输入。<br>2. 检查所用 capability 和 compact 决策。 |
| **预期结果** | 正常路径不预先取 fallback 最小窗口，不做无谓压缩；使用 primary 的能力与请求输出额度计算。 |

### TC-M10-007: 安全 Failover 到小窗口时重建 Context

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Primary 在未产生有效响应且本轮无 started 副作用时返回可 failover 错误；fallback 窗口更小。 |
| **测试步骤** | 1. 触发 failover。<br>2. 用 fallback capability 重新计算预算。<br>3. 让 ModelStep 按较小窗口重建/裁剪输入后调用 fallback。<br>4. 查询 checkpoint 与 Session Context。 |
| **预期结果** | 当前 ref 只按 fallback budget 重建/裁剪本轮 Context 并保持 Tool Exchange 原子性，不调用或持久化 Run Compactor；临时小窗口输入不覆盖 `session_context_states`。若裁剪后仍超限，应显式失败而非虚构分批 Run Compact。 |

### TC-M10-008: 非可重试 Provider 错误与 Started/Unknown Receipt 均不触发 Fallback

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 场景 A：primary 在无有效响应时返回明确不可重试错误；场景 B：数据库 `agent_tool_executions` 已有 started/unknown receipt，checkpoint 保留对应 pending tool calls。 |
| **测试步骤** | 1. 对场景 A 执行 ModelStep 并记录 primary/fallback 调用。<br>2. 对场景 B 尝试推进模型节点。<br>3. 检查 checkpoint、receipt 与等待/失败状态。 |
| **预期结果** | 场景 A 不调用 fallback，按错误契约显式失败；场景 B 模型调用为 0，Ledger 保留 started/unknown receipt，checkpoint 保留 pending calls 并进入安全等待/对账路径。两者均不切模重跑或重复副作用。 |

---

## M11 - Projector、Events 与 Reconciliation

### TC-M11-001: 持久化投影只消费已提交 Checkpoint

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Graph stream 已产生 completed 事件，但 terminal checkpoint 提交可延迟 / 失败。 |
| **测试步骤** | 1. 消费内存 stream。<br>2. 在 checkpoint 提交前运行 Projector。<br>3. 提交后再运行。 |
| **预期结果** | Stream 只用于低延迟 UI；提交前不持久化 terminal 投影，提交后 Projector 才确认 projected_* 与 lifecycle event。 |

### TC-M11-002: 同一 Checkpoint 投影与事件幂等

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 一个包含 lifecycle、interrupt、next 和 result 的 checkpoint；启动两个 Projector。 |
| **测试步骤** | 1. 并发投影同一 checkpoint。<br>2. 再重放三次。<br>3. 查询 Run 和 events。 |
| **预期结果** | CAS 与 (run_id, source_checkpoint_id, event_type) 唯一性保证一次有效投影；重放安全跳过，无重复事件。 |

### TC-M11-003: 删除派生投影后可从 History 重建

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Run 有 created → running → waiting → resumed → completed checkpoint history。 |
| **测试步骤** | 1. 备份查询结果。<br>2. 删除 projected_* 与 checkpoint 派生 lifecycle events。<br>3. 全量重放 Projector。 |
| **预期结果** | 最新 Run 查询状态与关键事件序列恢复为相同语义；重建不调用 Graph、模型、工具或交付。 |

### TC-M11-004: History 顺序沿父链而非比较 Checkpoint ID

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 构造 checkpoint ID 字典序与真实父子时间线不一致的 history，并设置 watermark。 |
| **测试步骤** | 1. 从 watermark 定位待处理历史。<br>2. 执行增量 Projector。<br>3. 检查事件顺序。 |
| **预期结果** | Watermark 只做相等匹配；处理顺序沿 history / parent chain 从旧到新，不按 ID 大小排序。 |

### TC-M11-005: Retention 造成 History Gap 时只重建最新状态

| 项目 | 内容 |
|---|---|
| **优先级** | P1 |
| **前置条件** | projected_checkpoint_id 指向的历史已被 retention 清理，但最新 snapshot 仍存在。 |
| **测试步骤** | 1. 运行 Projector。<br>2. 查询 Run、events、告警。 |
| **预期结果** | 从最新 snapshot 重建当前投影并记录 event-gap 告警；不伪造缺失事件时间线，也不失败推进 Graph。 |

### TC-M11-006: Checkpoint 身份不匹配时停止后续 Side Effects

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Run Registry 与 checkpoint 的 run_id / tenant_id / graph identity 任一不一致。 |
| **测试步骤** | 1. 运行 checkpoint product handler。<br>2. 检查投影、交付、Session delta 和告警。 |
| **预期结果** | 在投影事务前 fail closed；不交付、不合并 Session delta、不写错误归属事件，并产生隔离告警。 |

### TC-M11-007: Reconciliation 修复 Command 与终态投影

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 准备过期 claimed Command、checkpoint 已含 command_id 但 Command 未 applied、terminal checkpoint 但投影 active 三种记录。 |
| **测试步骤** | 1. 运行一次 reconciliation。<br>2. 再运行一次验证幂等。 |
| **预期结果** | 过期 claim 可重领；已应用输入只补写 applied；terminal 投影被重建；第二次执行无额外变化，Graph 不被重复 invoke。 |

### TC-M11-008: Reconciliation 不猜测未知外部结果

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 存在 unknown 工具 receipt、completed+pending delivery、completed+failed terminal delivery、checkpoint 缺 Registry 和 terminal lane holder。 |
| **测试步骤** | 1. 运行 reconciliation。<br>2. 检查各记录处理。<br>3. 记录外部调用次数。 |
| **预期结果** | 可证明的 terminal lane 可释放，pending 或过期 claim 的 delivery 可重领；failed terminal delivery 不会自动复活，需显式管理 requeue/reset 能力；unknown 工具和孤立 checkpoint 只告警/隔离，不猜测归属或成功，外部工具调用为 0。 |

---

## M12 - Origin Session、交付与 Outbox

### TC-M12-001: 前台结果精确回投 Origin Session

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 用户与同一 Agent 有 origin 非 primary Session A 和 primary Session B；Run 从 A 创建。 |
| **测试步骤** | 1. 完成 Run。<br>2. 执行 terminal delivery。<br>3. 查询 A/B 消息与 delivery receipt。 |
| **预期结果** | 用户可见结果只写入 A，B 无新增；消息身份、run_id 和稳定幂等键正确，delivery_status=delivered。 |

### TC-M12-002: 前台 Origin 不可用时不回退 Primary

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Foreground / orchestration Run 已公开 ACK，随后 origin Session 删除或失去写权限；同 scope 有 primary。 |
| **测试步骤** | 1. 尝试交付 waiting / terminal 消息。<br>2. 查询 origin、primary、delivery 状态和事件。 |
| **预期结果** | 不向 primary 改投；不产生误归属消息；delivery_status=failed 并记录脱敏原因，执行 terminal 状态保持不变。 |

### TC-M12-003: Background Direct Run 可回退同 Scope Primary

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Trigger / Task background Run 记录 origin user / agent，origin Session 在首次写入前确定不可用；同 direct scope 有 primary。 |
| **测试步骤** | 1. 解析 delivery target。<br>2. 执行交付。<br>3. 查询 requested / actual target 与 fallback reason。 |
| **预期结果** | 结果只写同 tenant + agent + user 的 primary；receipt / event 同时记录原目标、实际目标和原因，不跨用户 / Agent。 |

### TC-M12-004: Background Group 回退不离开原 Group Scope

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Background Run 来自 Group G 的已删 Session；G 有另一个可写 primary Group Session，其他 Group 也有 primary。 |
| **测试步骤** | 1. 解析 fallback。<br>2. 投递并查询所有候选 Session。<br>3. 删除 G 的最后 primary 后重试新记录。 |
| **预期结果** | 首次只回 G 当前 primary；不会跨 Group；G 无 primary 时 delivery failed，系统不自动新建 Group Session。 |

### TC-M12-005: Origin 写入结果 unknown 时禁止立即 Fallback

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Provider 已可能接受 origin terminal 消息，但本地在确认落库前断开，结果为 unknown；primary 可用；记录 Provider 是否支持幂等键或结果查询。 |
| **测试步骤** | 1. 让 Worker 重领 outbox。<br>2. 用原 terminal delivery key 优先对账/重试 origin。<br>3. 检查 primary、Provider 消息和审计。 |
| **预期结果** | unknown 期间不切 primary；支持 Provider 幂等/查询时收敛到一个结果；不支持时明确记录 at-least-once 与潜在重复风险，不能承诺网络侧 exactly-once；无论如何不重跑 Graph/工具。 |

### TC-M12-006: Execution completed 与 Delivery 状态严格分离

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Graph 已写 completed checkpoint，让 provider delivery 连续失败。 |
| **测试步骤** | 1. 运行 Projector。<br>2. 在最大次数以内让 Delivery Worker 失败后重试并最终成功。<br>3. 另一个记录耗尽次数到 failed，再尝试普通 Worker 和管理 requeue。 |
| **预期结果** | projected_execution_status 始终保持 completed；阈值内 delivery 可从 pending 重试到 delivered；耗尽后为 failed 且普通 Worker 不再领取。当前没有 admin requeue/reset API 时该恢复步骤登记 `BLOCKED/运维缺口`；任何投递操作都不重跑 Graph、模型或工具。 |

### TC-M12-007: channel_deliveries 是持久化 Provider Outbox

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 外部渠道 terminal ChatMessage 事务可用，Delivery Worker 暂停。 |
| **测试步骤** | 1. 提交 terminal message 与渠道目标。<br>2. 检查同事务生成的 channel_deliveries。<br>3. 重启 Worker 后领取并发送。 |
| **预期结果** | Message、delivery_status / event 与 Outbox 事实原子一致；重启后继续发送。channel_deliveries 只表示 provider 投递，不是第六张执行状态表，也不参与 Graph 路由。 |

### TC-M12-008: 重复 Terminal Delivery 返回同一 Receipt

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 同一 Run 已用 run:{run_id}:terminal:{status} 完成交付；准备 checkpoint / Worker 重放。 |
| **测试步骤** | 1. 重放 terminal handler。<br>2. 并发启动两个 Delivery Worker。<br>3. 查询 ChatMessage、Outbox、event。 |
| **预期结果** | 本地返回同一 receipt，ChatMessage、channel_deliveries 和 delivery event 均唯一；已确认成功场景不再发送。若 Provider 已收但本地确认丢失，网络尝试可能按 at-least-once 重复，必须沿用同一幂等键并留存 unknown 审计，不能宣称绝对单次发送。 |

---

## M13 - Web、Task、Trigger、Schedule 与 Heartbeat

### TC-M13-001: Web 新消息原子进入 Runtime

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 有效 tenant / user / agent / ChatSession，WebSocket 已认证，模型桩可观察。 |
| **测试步骤** | 1. 发送普通用户消息。<br>2. 在事务提交点检查 ChatMessage、Run Registry、start Command。<br>3. 消费 Runtime stream。 |
| **预期结果** | 三项事实同事务提交，之后才通知 Worker；WebSocket 只映射稳定 RuntimeEvent，不进入 legacy tool loop。 |

### TC-M13-002: Web 等待续接、刷新恢复与 Abort 使用精确 Run

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Socket 收到 waiting 事件及 run_id/correlation_id；同 Session 另有一个 Run。当前 ref 没有对应单聊前端变更，浏览器步骤在 UI 落地前登记 `BLOCKED/待实现`。 |
| **测试步骤** | 1. 下一条消息携带 waiting identity并检查 resume Command。<br>2. 对另一执行发送 abort。<br>3. 用 Manual-Browser 在 waiting/terminal/delivery failed 阶段刷新并重进 Session。 |
| **预期结果** | 后端回复只恢复精确 Run；abort 持久化 cancel Command 而不影响同 Session 其他 Run；前端就绪后刷新可重建 waiting/terminal，并区分执行状态与交付状态。当前 ref 不得以 websocket 单测代替浏览器验收。 |

### TC-M13-003: Task 每次实际执行拥有独立 Run 身份

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 一个 Task 发生首次执行、幂等重投和下一次 supervision occurrence。 |
| **测试步骤** | 1. 注册首次执行。<br>2. 用同 source_execution_id 重投。<br>3. 创建下一 occurrence。 |
| **预期结果** | 首次与重投命中同一 Run / queue log；下一 occurrence 创建新 Run / Thread；Task.id 只作为业务来源，不复用旧 checkpoint。 |

### TC-M13-004: Task 完成投影与失败回退幂等

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 准备 completed、failed / cancelled terminal checkpoint 和 supervision Task。 |
| **测试步骤** | 1. 运行 task completion handler。<br>2. 重放同一 checkpoint。<br>3. 比较普通 / supervision Task 状态和日志。 |
| **预期结果** | 普通成功 Task 变 done，失败 / 取消按产品规则回 pending；supervision 成功后仍回 pending；每个 terminal receipt / log 只写一次。 |

### TC-M13-005: TriggerExecution 稳定去重且不回 legacy

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 同一 TriggerExecution.id 被队列重复投递；另准备 Runtime intake 拒绝 / 关闭场景。 |
| **测试步骤** | 1. 并发领取重复 occurrence。<br>2. 检查 source_execution_id 和事务。<br>3. 执行拒绝场景。 |
| **预期结果** | 重复投递命中同一 Run；TriggerExecution 与 start Command 原子提交；拒绝时 occurrence 明确结算失败，不被旧 Trigger loop 再领取。 |

### TC-M13-006: Manual Schedule 使用稳定 Occurrence 身份

| 项目 | 内容 |
|---|---|
| **优先级** | P1 |
| **前置条件** | 同一 schedule occurrence 以两个等价时区表示触发，并准备一次手工执行 API 请求。 |
| **测试步骤** | 1. 分别计算 occurrence identity。<br>2. 手工注册 Run。<br>3. 重投相同 occurrence。 |
| **预期结果** | 等价时刻得到相同稳定身份；请求事务内注册 Run；重投不重复执行；naive timestamp 被拒绝。 |

### TC-M13-007: Heartbeat 与 Oneshot 统一创建后台 Run

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Agent 有一次 heartbeat occurrence、一次 oneshot；模型分别返回无工作和需要工具。 |
| **测试步骤** | 1. 触发两类入口。<br>2. 检查 source_type、source_execution_id 和 Graph。<br>3. 搜索 heartbeat 生产代码的模型循环。 |
| **预期结果** | 每个 occurrence 创建独立 background Run；无工作快速 completed，需要工作走统一 Graph；不存在 heartbeat / oneshot 独立多轮 tool loop。 |

### TC-M13-008: Onboarding 通过 Runtime 持久完成且不依赖 Live Socket

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 准备 onboarding trigger、带 target phase 的 Chat Run、普通 Chat Run，并可在 Runtime 完成前断开 WebSocket；另准备 failed/cancelled 终态。 |
| **测试步骤** | 1. 触发 onboarding 并在提交后断开 socket。<br>2. 分别推进 completed、failed、cancelled。<br>3. 完成普通 Chat Run。<br>4. 重放 completion handler。 |
| **预期结果** | Onboarding instruction/target phase 随 Runtime Command 持久化；只有 completed onboarding Run 推进对应 phase，且无需 live socket；failed/cancelled 不推进，普通 Chat 不误推进；重放幂等。 |

---

## M14 - A2A 与 OpenClaw

### TC-M14-001: Native A2A Input ChatMessage 与 Target Run 原子接受

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 同 tenant 的 Native source/target Agent 权限关系有效，请求带稳定 message identity。 |
| **测试步骤** | 1. 提交 Native A2A 消息。<br>2. 在 provider acceptance 前检查事务。<br>3. 重投同一消息。 |
| **预期结果** | A2A input `ChatMessage`、target Run、start Command 与必要 receipt 原子落库后才确认接受；Native→Native 不创建 `GatewayMessage`；重投不创建第二个 target。 |

### TC-M14-002: task_delegate 原子创建 Target Run 与工具 Receipt

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Source Run 正在 execute_tools，目标 Agent 可访问，调用含真实 tool_call_id 与委托 mode；调用方不传 correlation_id。 |
| **测试步骤** | 1. 执行 task_delegate。<br>2. 在事务提交点检查 target Registry / Command、parent / root 和 receipt。<br>3. 注入事务回滚。 |
| **预期结果** | `source_execution_id` 与 correlation UUID 只由 `(source_run_id, tool_call_id)` 确定性派生；mode 只进入 `a2a:{mode}:...` 前缀而不改变 UUID。成功时 target/Command 创建并使 receipt 收敛 succeeded/accepted；A2A 事务回滚时 target/Command 不存在，但事务前已提交的 started receipt 保留且不得伪成功，完整调用链将其收敛为 unknown 并进入对账。 |

### TC-M14-003: Source 与 Target 使用独立 Thread 并通过关联等待

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 一次已接受的 task_delegate，source / target Run 均已创建。 |
| **测试步骤** | 1. 读取两者 thread_id 和 checkpoint。<br>2. 推进 source 到 waiting_agent。<br>3. 独立推进 target。 |
| **预期结果** | 两个 thread_id 分别等于各自 run_id；不共享完整 checkpoint；source interrupt 仅保存 correlation，target 可独立执行。 |

### TC-M14-004: Target 完成后原子恢复 Source

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Target 已 terminal，含 result summary / artifact refs；Source 正在匹配 correlation 的 waiting_agent。 |
| **测试步骤** | 1. 运行 A2A completion handler。<br>2. 检查 target 可见消息 / completion receipt 与 source resume Command。<br>3. 重放 callback。 |
| **预期结果** | 产品结果与 source resume 原子提交；Source 从原 checkpoint 恢复；重复 callback 因 receipt / Command 唯一键 no-op。 |

### TC-M14-005: Target 失败以结构化错误恢复 Source

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Target 进入 failed / cancelled，Source 等待其结果。 |
| **测试步骤** | 1. 发送 completion callback。<br>2. 检查 Source resume payload 与 lifecycle。<br>3. 让 Source 决定后续。 |
| **预期结果** | Target 失败不会直接把 Source 标 failed；Source 收到脱敏结构化 error，可选择重试委托、换 Agent、告知用户或结束。 |

### TC-M14-006: Notify 与委托等待语义分离

| 项目 | 内容 |
|---|---|
| **优先级** | P1 |
| **前置条件** | 同一 Source 分别发起 notify 与 task_delegate；准备 target delivery。 |
| **测试步骤** | 1. 执行 notify。<br>2. 执行 task_delegate。<br>3. 比较 source checkpoint 和 target 记录。 |
| **预期结果** | Notify 只产生幂等消息交付并继续 Source，不进入 waiting_agent；task_delegate 创建独立 target 并等待；两者都通过工具 receipt 防重复。 |

### TC-M14-007: Agent 循环限制从数据库父链重算

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 构造 A→B→A→B 等 delegated 祖先边、人类 / Planning 祖先、跨 tenant / 断裂父链。 |
| **测试步骤** | 1. 逐步加入候选有向边。<br>2. 计算重复边总数。<br>3. 让候选达到 MAX_AGENT_CYCLE_COUNT=5。 |
| **预期结果** | 只统计 delegated 边，达到 5 前可继续、达到 5 时拒绝且不创建 target；坏链 / 跨 tenant fail closed，计数不依赖进程内缓存。 |

### TC-M14-008: OpenClaw 委托与回报全程走 Durable Runtime

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | Native Source 委托 OpenClaw target，随后收到携带稳定 receipt / correlation 的 report；旧 A2A executor 桩可观察。 |
| **测试步骤** | 1. 创建 OpenClaw target 请求并提交。<br>2. 重启进程后消费队列。<br>3. 接收 report 并恢复 Source。<br>4. 尝试调用旧 executor。 |
| **预期结果** | Target 请求原子入队且可跨重启；report 从 receipt 精确恢复 Native Source；旧 executor fail closed、无副作用。 |

---

## M15 - 全部外部渠道

### TC-M15-001: 飞书 Intake 与 Delivery 使用持久化会话

| 项目 | 内容 |
|---|---|
| **优先级** | P0；现有 pytest 为 mock，真实飞书 sandbox 回归是上线前手工缺口 |
| **前置条件** | 飞书 sandbox bot / 群凭据可用；准备文本与图片消息、重复 event_id。 |
| **测试步骤** | 1. 发送群消息并观察 ACK 前事务。<br>2. 重投 event。<br>3. 完成 Run 并通过 Outbox 回投原 external_conv_id。<br>4. 让飞书已可能接收消息但本地确认丢失后重领。 |
| **预期结果** | Provider ACK 前已提交统一 Session、消息与 Runtime Command；图片 base64 不进展示正文；回投原飞书会话。本地消息/receipt 唯一；确认丢失时沿用同一幂等键并记录 unknown，若飞书不提供端到端幂等保证则接受并披露 at-least-once 重复风险。 |

### TC-M15-002: 钉钉使用统一 Group Scope 与持久化 Webhook

| 项目 | 内容 |
|---|---|
| **优先级** | P1；现有 pytest 为 mock，真实钉钉 sandbox 回归是手工缺口 |
| **前置条件** | 钉钉测试群与有效 webhook 可用；创建对应 external group ChatSession。 |
| **测试步骤** | 1. 从钉钉发消息启动 Run。<br>2. 重启服务。<br>3. 完成 Run 并投递。 |
| **预期结果** | Intake 复用统一外部 Group Session；重启后 Delivery Worker 从持久化 session webhook 发送，不依赖原 HTTP callback 内存。 |

### TC-M15-003: 企业微信多入口共用 Runtime 与 Outbox

| 项目 | 内容 |
|---|---|
| **优先级** | P0；现有 pytest 为 mock，真实企业微信应用 / 客服 sandbox 是手工缺口 |
| **前置条件** | 企业微信 HTTP、WebSocket / stream、客服三种测试入口可用。 |
| **测试步骤** | 1. 分别接收三种消息。<br>2. 完成对应 Run。<br>3. 重启 Worker 后投递并检查客服 session claim。 |
| **预期结果** | 三入口均先持久化 Runtime；原 callback 消失后仍可发送；客服投递前正确 claim session，消息不串会话；本地并发不会双发，但 Provider 已接收而确认丢失时不得承诺网络侧绝对去重。 |

### TC-M15-004: 微信渠道使用最新持久化 Delivery Context

| 项目 | 内容 |
|---|---|
| **优先级** | P1；现有 pytest 为 mock，真实微信 sandbox 是手工缺口 |
| **前置条件** | 微信测试会话存在；Run 执行期间渠道凭据 / destination context 合法更新一次。 |
| **测试步骤** | 1. 从微信消息启动 Run。<br>2. 更新持久化渠道上下文并重启 Worker。<br>3. 执行 terminal delivery。 |
| **预期结果** | Intake 不走 legacy LLM loop；Delivery 使用最新持久化且已授权的 context，而非过期内存 callback，结果回原会话。 |

### TC-M15-005: Slack 业务错误只重试 Provider Delivery

| 项目 | 内容 |
|---|---|
| **优先级** | P0；现有 pytest 为 mock，真实 Slack sandbox / 限流回归是手工缺口 |
| **前置条件** | Slack 测试 workspace 可用；provider 先返回 HTTP 成功但业务 error，再返回成功。 |
| **测试步骤** | 1. 通过 webhook 创建 Run。<br>2. 领取 channel_deliveries 并处理业务 error。<br>3. 重试同一 Outbox。 |
| **预期结果** | 明确业务 error 被识别为可重试投递失败；不标 delivered、不 resume Graph；随后沿用同一记录成功。若错误响应无法证明 Slack 未接收，则按 unknown/at-least-once 风险处理，不无条件承诺单条消息。 |

### TC-M15-006: Teams 通过 Durable Target 重建 Activity

| 项目 | 内容 |
|---|---|
| **优先级** | P1；现有 pytest 为 mock，真实 Teams sandbox 是手工缺口 |
| **前置条件** | Teams 测试 tenant / conversation reference 可用；原 webhook 请求结束后重启服务。 |
| **测试步骤** | 1. 接收 Teams activity 并启动 Run。<br>2. 完成 Run。<br>3. 从 Outbox durable target 重建并发送 activity。 |
| **预期结果** | 发送不依赖原请求对象；目标 tenant / conversation 不变；Provider 确认后才标 delivered。 |

### TC-M15-007: WhatsApp Webhook 原子接受并回原会话

| 项目 | 内容 |
|---|---|
| **优先级** | P1；现有 pytest 为 mock，真实 WhatsApp sandbox 是手工缺口 |
| **前置条件** | WhatsApp 测试号码 / conversation 可用，准备重复 provider message ID。 |
| **测试步骤** | 1. 发送 webhook。<br>2. 重投相同 provider ID。<br>3. 完成 Run 并通过 Outbox 回投。 |
| **预期结果** | Provider ACK 前持久化消息和 Runtime；重复输入不创建第二 Run；最终只向原 WhatsApp conversation 投递，本地 receipt 唯一；网络确认丢失时沿用 provider/message 幂等标识并记录潜在重复风险。 |

### TC-M15-008: Discord Interaction 过期后回退同 Channel

| 项目 | 内容 |
|---|---|
| **优先级** | P0；现有 pytest 为 mock，真实 Discord sandbox / interaction expiry 是手工缺口 |
| **前置条件** | Discord 测试 guild / channel / interaction token 可用；让 interaction token 在 Run 完成前过期。 |
| **测试步骤** | 1. 接收 interaction 并在 deferred ACK 前提交 Runtime。<br>2. 等 token 过期。<br>3. 执行 Outbox delivery。 |
| **预期结果** | Intake 已 durable；投递检测 token 过期并只回退同一 Discord channel，不改投平台 primary；幂等键阻止本地并发双发，但 Provider unknown outcome 仍按 at-least-once 风险审计。 |

---

## M16 - Security、Deploy 与 Observability

### TC-M16-001: 权限校验先于 Checkpoint 读取

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 准备其他 tenant / agent / user 的有效 run_id 与 thread_id；对 Checkpointer 建立调用监控。 |
| **测试步骤** | 1. 通过 get / resume / cancel / stream 接口提交越权请求。<br>2. 检查错误与 Checkpointer 调用。 |
| **预期结果** | 业务表权限校验先拒绝，Checkpointer 调用为 0；响应不泄露 Run 是否存在、waiting payload 或 checkpoint 内容。 |

### TC-M16-002: State、Event 与摘要移除凭据和大正文

| 项目 | 内容 |
|---|---|
| **优先级** | P0；BLOCKED / 安全实现缺口 |
| **前置条件** | 工具输入/输出含 API key、OAuth token、cookie、Authorization、DSN、签名 URL 和超限正文。当前 ref 会把原始 arguments 写入 `sanitized_arguments`，且允许最高 1MB 的 inline `result_summary`。 |
| **测试步骤** | 1. 执行工具、Compact、checkpoint 和 event 投影。<br>2. 扫描 Runtime State/Event/summary/tool ledger 相关列，并单独识别业务 `workspace_file_revisions` 等修订历史表。 |
| **预期结果** | 目标安全契约要求 Runtime State/Event/summary/tool ledger 去除或脱敏凭据，并将大正文转 artifact/result_ref；当前实现若检出明文 arguments 或大 inline result，本用例必须 Fail/Blocked，不能误报已脱敏。业务修订表按自身权限/retention 契约单独验收。 |

### TC-M16-003: Checkpoint 加密密钥只来自部署 Secret

| 项目 | 内容 |
|---|---|
| **优先级** | P0 |
| **前置条件** | 分别提供合法 AES Secret、未配置 Secret、非法长度 Secret；数据库可检查原始 checkpoint bytes，部署清单明确是否强制加密。 |
| **测试步骤** | 1. 启动 bootstrap / Runtime。<br>2. 对合法配置写入含敏感级用户文本的 checkpoint。<br>3. 尝试缺失 / 非法配置。 |
| **预期结果** | 合法 key 使用 EncryptedSerializer，数据库原始值不可读；非法长度 key 在连接前 fail closed 且日志不打印密钥；未配置 key 时实现只使用 allowlist serializer，不得误报“已加密”，生产是否允许由部署策略拦截。 |

### TC-M16-004: Runtime Retention Job 按可配置策略独立执行

| 项目 | 内容 |
|---|---|
| **优先级** | P1；BLOCKED / 待实现 |
| **前置条件** | Runtime checkpoint retention/pruning job 与经合规确认的可配置期限已实现；当前 ref 未发现该 job，技术设计中的 30 天仅为建议初始值。 |
| **测试步骤** | 1. 按配置准备 active/waiting 与到期 terminal checkpoint。<br>2. 运行 retention job。<br>3. 查询 Run/Command/event/receipt/Session state 并尝试恢复 active Run。 |
| **预期结果** | 能力落地后，active/waiting checkpoint 保留，到期 terminal checkpoint 可清理，产品事实按各自策略保留且不丢交付审计；实现和合规策略确认前不得使用固定 30 天执行或报告通过。 |

### TC-M16-005: 生产 Entrypoint 严格按迁移顺序启动

| 项目 | 内容 |
|---|---|
| **优先级** | P0；shell 单测已有，真实 PostgreSQL / 容器启动演练是集成缺口 |
| **前置条件** | 生产等价容器、真实 PostgreSQL，分别注入 Alembic 失败、checkpoint setup 失败和全部成功，并覆盖 ALLOW_MIGRATION_FAILURE=false / true。 |
| **测试步骤** | 1. 启动各组合容器。<br>2. 记录 Alembic、setup、Worker / Web 进程顺序、日志和退出码。 |
| **预期结果** | 顺序固定为 Alembic → checkpoint bootstrap → Worker / Web；默认 false 时任一前置失败即非零退出；显式 true 时按既有策略记录高可见错误后继续，且 Alembic 失败时不误跑 checkpoint setup。 |

### TC-M16-006: Worker Readiness 校验产品表与 Checkpoint 版本

| 项目 | 内容 |
|---|---|
| **优先级** | P0；真实 PostgreSQL smoke test 是上线前集成缺口 |
| **前置条件** | 分别准备缺产品表、checkpoint migration ledger 版本不匹配、全部正确三种数据库。 |
| **测试步骤** | 1. 对三库运行 readiness。<br>2. 尝试启动 Command / Delivery Worker。 |
| **预期结果** | 前两库 fail closed 且 Worker 不领取任务；完整且固定版本匹配的库通过，错误明确指出缺失表或版本。 |

### TC-M16-007: Runtime 指标区分执行、等待、恢复与交付

| 项目 | 内容 |
|---|---|
| **优先级** | P1；BLOCKED / 待实现 |
| **前置条件** | Runtime metrics exporter/dashboard 已实现；当前 ref 仅有 worker iteration result 与可供未来采集的结构化事实。执行一组 completed、waiting、resume 成功/失败、duplicate tool blocked、delivery failed/retry Run。 |
| **测试步骤** | 1. 收集 metric、结构化日志和 trace correlation。<br>2. 按 tenant / run 聚合。<br>3. 扫描敏感 payload。 |
| **预期结果** | 能力落地后可分别查询 Run 数、等待时长、恢复成功率、重复工具拦截数、outbox backlog/交付失败数，日志可按 run/command/checkpoint 关联且不含敏感正文；当前 ref 只能验证事实可计算，不得声称现成 metrics/dashboard 已通过。 |

### TC-M16-008: 维护窗口禁止新旧 Schema / Runtime 混跑

| 项目 | 内容 |
|---|---|
| **优先级** | P0；需生产等价真实 PostgreSQL 与多实例发布演练 |
| **前置条件** | 准备旧发布、新统一 Schema + LangGraph 发布和回滚包；可控制全部写入方与多实例启动。 |
| **测试步骤** | 1. 停止写入方并备份。<br>2. 执行统一聊天 / Runtime migration 与 checkpoint setup。<br>3. 启动新实例并阻止旧实例加入。<br>4. 演练回滚。 |
| **预期结果** | 新后端仅在全部前置成功后接流量；同部署不存在旧 Schema 或 legacy loop 实例；回滚只能整体回到与旧 Schema 匹配的发布，不用 feature flag 复活旧 loop。 |

## 附录 - 目标 ref 的自动化复用映射

> 下表只表示目标 ref 中已有 pytest 资产可映射到哪些模块，不表示这些测试已在当前 checkout 执行，也不把 mock 测试等同于真实 PostgreSQL、容器或渠道 sandbox 验证。2571b892 的 Toolathlon benchmark 测试不计入；c959dffe 的 checkpoint bootstrap 测试计入。

| 模块 | 关键实现依据 | 目标 ref 已有 pytest 映射 |
|---|---|---|
| M01 | cb38452d、0ef0f8d4 | test_agent_runtime_adapter.py、test_agent_runtime_config.py、test_websocket_runtime_chat.py、test_task_runtime_intake.py |
| M02 | 202607131910_unify_chat_schema.py | test_unified_chat_schema.py、test_chat_session_service.py、test_channel_session.py |
| M03 | dcf07cfc、7ab6bc02、3bdb88dc、8d685c47、e4e69c3e、c959dffe | test_runtime_schema.py、test_runtime_migration.py、test_agent_runtime_checkpointer.py、test_setup_langgraph_checkpoints.py、test_model_capabilities.py |
| M04 | 377013f4、2afce6da、60fbbb4b、db56b337、c72720cf、0cad257b | test_agent_runtime_command_worker.py、test_agent_runtime_thread_lock.py、test_agent_runtime_persistence.py |
| M05 | 24becd30 | test_agent_runtime_graph.py、test_agent_runtime_model_step_service.py、test_agent_runtime_node_executor.py、test_agent_runtime_checkpoint_side_effects.py |
| M06 | af6983dd、38befb55 | test_agent_runtime_graph.py、test_agent_runtime_command_worker.py、test_agent_runtime_cancel_source.py、test_agent_runtime_chat_stream.py |
| M07 | 0fedbb3f、ce0ec844、1e146a15、a35b012a | test_tool_execution.py、test_tool_exchange.py、test_agent_runtime_tool_step_service.py |
| M08 | d61b87f5 | test_agent_runtime_context_builder.py、test_agent_runtime_contracts.py |
| M09 | 17af3560、fbdc8b99 | test_agent_runtime_session_context_compactor.py、test_agent_runtime_session_context_completion.py、test_session_context_service.py |
| M10 | d210ba5e、7fbe93e2、ed3a7511 | test_agent_runtime_run_compactor.py、test_model_capabilities.py、test_agent_runtime_model_step_service.py |
| M11 | Runtime Projector / reconciliation 主线 | test_agent_runtime_projector.py、test_agent_runtime_checkpoint_side_effects.py、test_agent_runtime_worker_service.py |
| M12 | 1ef6dbf7、229202f0、7d6b5d04、628b79e2、82efd8aa | test_agent_runtime_delivery.py、test_agent_runtime_channel_delivery.py、test_agent_runtime_channel_provider_delivery.py、test_channel_delivery_migration.py |
| M13 | 3b9dfed9、4b5b8bd3、d0ec5c8a、397aa877、851f2176 | test_websocket_runtime_chat.py、test_task_runtime_intake.py、test_trigger_runtime_intake.py、test_schedule_runtime_intake.py、test_heartbeat_runtime.py |
| M14 | 2094d415、ce4b3654、fbd63807、31e5762b、6bd635bb | test_agent_runtime_a2a.py、test_agent_runtime_a2a_completion.py、test_gateway_runtime_a2a.py、test_agent_runtime_cycle_guard.py |
| M15 | 878e99d3、4a98353f、fe604542、508290ff、2ea8fcb7、8363a049、a31bf234 | test_feishu_channel_runtime.py、test_http_channel_runtime.py、test_stream_channel_runtime.py、test_wechat_channel_runtime.py、test_agent_runtime_channel_provider_delivery.py |
| M16 | c959dffe 与部署 / readiness 主线 | test_agent_runtime_checkpointer.py、test_setup_langgraph_checkpoints.py、test_agent_runtime_worker_service.py、test_runtime_migration.py |

仍需执行的关键缺口：真实 PostgreSQL checkpoint setup / restart / advisory lock，多副本容器启动与维护窗口演练，以及飞书、钉钉、企业微信、微信、Slack、Teams、WhatsApp、Discord 的真实 sandbox 收发与失败重试。
