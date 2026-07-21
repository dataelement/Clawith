# Clawith Unified Runtime + Group Chat E2E 测试用例册

> 更新依据：飞书测试用例模板 `KMoTdTIYyoIQMPxLTTKcPI4XnTd` revision 11、当前 `docs/single-agent-runtime/technical-design.md`、`docs/single-agent-runtime/runtime-context-compression-prd.md`、`docs/group-chat/prd.md`、`docs/group-chat/technical-design.md`，以及生产目标 `0ef0f8d4 + c959dffe`；全部用例使用真实 PostgreSQL、真实 API/Runtime/Worker/浏览器或渠道 sandbox，排除 `2571b892` Toolathlon benchmark，未实现能力只能登记 `BLOCKED/待实现`。

## 目录

| 模块编号 | 模块名称 | 用例数量 |
|-|-|-:|
| M01 | 真实环境、部署、迁移与 Checkpoint | 5 |
| M02 | Web Direct Chat 与 Session | 5 |
| M03 | Runtime Wait、Resume、Cancel 与重启 | 5 |
| M04 | 工具副作用、Unknown 与对账 | 5 |
| M05 | Task、Trigger、Schedule 与 Heartbeat | 5 |
| M06 | A2A 与 OpenClaw | 5 |
| M07 | 外部渠道 Origin、Fallback 与 Outbox | 5 |
| M08 | 群创建、成员与 Session | 5 |
| M09 | 群消息、Mention、ACK 与未读 | 5 |
| M10 | Planning、A2A 与 Mention Lane | 5 |
| M11 | 群上下文、公告、Memory、Workspace 与 Compact | 5 |
| M12 | 删除、租户隔离、故障、可观测性、容量与发布 | 5 |
| **合计** |  | **60** |

## M01 - 真实环境、部署、迁移与 Checkpoint

### TC-M01-001: 发布制品精确包含生产目标并排除 Toolathlon

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | CI 可从 `0ef0f8d4` 加直接后续 `c959dffe` 构建全新后端镜像；使用独立真实 PostgreSQL 和制品仓库。 |
| **测试步骤** | 1. 记录源码、镜像 digest 和 SBOM。<br>2. 部署镜像并查询版本/健康信息。<br>3. 检查容器文件、命令和路由是否包含 `2571b892` 的 Toolathlon benchmark 入口。 |
| **预期结果** | 运行制品可追溯到 `0ef0f8d4+c959dffe`；digest 固定；不包含或暴露 Toolathlon CLI、数据集和 benchmark 路由；后续证据均绑定该制品。 |

### TC-M01-002: 真实 PostgreSQL 在停写窗口完成 Unified Schema 迁移

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 从脱敏生产备份恢复真实 PostgreSQL；Web、Worker、Scheduler 和渠道消费者全部停止；备份与恢复点已验证。 |
| **测试步骤** | 1. 运行迁移前 tenant/identity/schema 审计。<br>2. 执行 Alembic upgrade 和 Unified Chat/Runtime backfill。<br>3. 检查约束、索引、revision 与抽样业务数据。<br>4. 重跑迁移。 |
| **预期结果** | 审计通过后一次收敛到唯一目标 revision；统一 chat/runtime 表、约束与索引完整，数据归属无歧义；第二次为安全 no-op；期间无旧新 writer 混跑。 |

### TC-M01-003: Entrypoint 按 Alembic、Checkpoint、服务顺序启动

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 真实 PostgreSQL 启用 TLS；`DATABASE_URL` 使用支持的 asyncpg `ssl=require` 参数；checkpoint schema 尚未创建。 |
| **测试步骤** | 1. 以 bootstrap role 启动真实容器。<br>2. 采集 entrypoint 日志和 PostgreSQL DDL。<br>3. 在每个阶段探测 HTTP/Worker readiness。<br>4. 查询 `langgraph_checkpoint` schema 与 saver migration ledger。 |
| **预期结果** | asyncpg SSL 被规范化为 psycopg 可用配置；Alembic 成功后才执行 checkpoint setup，setup 成功后才启动服务；schema/ledger 真实可用，提前阶段 readiness 不为真。 |

### TC-M01-004: 多副本并发 Bootstrap 由 PostgreSQL Advisory Lock 串行化

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 同一空 checkpoint schema；三个相同 bootstrap 容器连接同一真实 PostgreSQL，具备查看 `pg_locks` 和启动日志的权限。 |
| **测试步骤** | 1. 同时启动三个副本。<br>2. 在 setup 期间采样 advisory lock 持有者/等待者。<br>3. 等待全部进程结束 bootstrap。<br>4. 查询 saver ledger、表和错误日志。 |
| **预期结果** | 同一时刻只有一个 setup 持锁，其余等待后幂等通过；没有竞争 DDL、重复 ledger 或半建表；三个副本最终都在同一 checkpoint 版本后启动。 |

### TC-M01-005: Checkpoint 配置或 DDL 失败时生产启动 Fail Closed

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 生产式 `ALLOW_MIGRATION_FAILURE=false`；分别准备冲突 `ssl=disable&sslmode=require` 和无 checkpoint DDL 权限的数据库账号。 |
| **测试步骤** | 1. 用两种故障配置分别启动容器。<br>2. 探测 HTTP/Worker readiness 和进程退出码。<br>3. 检查错误日志脱敏及 PostgreSQL 残留对象。<br>4. 修复配置后重新启动。 |
| **预期结果** | 两种故障都在 Runtime/Worker 接流量前退出且错误可诊断，不留下错误版本的半成品 schema；日志不泄露 DSN 密码；修复后可正常 bootstrap。 |

---

## M02 - Web Direct Chat 与 Session

### TC-M02-001: 浏览器 Direct Chat 从发送到最终回复完整闭环

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | E2E 环境的真实 Web、API、Runtime Worker、模型网关和 PostgreSQL 已就绪；测试用户可访问一个 Company Agent。 |
| **测试步骤** | 1. 在浏览器创建 Direct Chat Session 并发送带唯一标记的问题。<br>2. 观察 streaming、等待态和最终回复。<br>3. 查询 Run Registry、start Command、checkpoint、messages 与 delivery receipt。 |
| **预期结果** | 用户消息、Run 与 start Command 原子接受；真实 Worker 推进 checkpoint；最终回复只出现一次并回到原 Session；浏览器状态与 PostgreSQL 一致。 |

### TC-M02-002: 非法 Finish 在 ModelStep 修复，合法 Finish 经 Verify 后再交付

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 真实服务进程连接一个可控但仍走完整 HTTP provider adapter 的 E2E 模型端点；可先返回非法/空 finish，再返回无 pending tools 的合法非空 finish，并可在 terminal checkpoint commit 前注入故障。 |
| **测试步骤** | 1. 从真实 WebSocket 发起 Direct Chat。<br>2. 让非法 finish 进入 ModelStep 有界修复。<br>3. 返回合法 finish 并观察 `verifying`。<br>4. 在 completed terminal checkpoint 提交前注入退出，恢复后再完成。 |
| **预期结果** | 非法/空 finish 在 ModelStep 解析阶段被修复，不虚构 verify-repair；合法 finish 进入 `verifying`，默认 verifier 通过后在同一 `_verify()` 流程调用 finalizer 并转 completed/terminal，不存在独立 finalize node/state；terminal checkpoint 未提交前没有最终投影或 delivery，恢复后 post-checkpoint side effect 只执行一次。 |

### TC-M02-003: 两个 Direct Session 的 Context 与回复严格隔离

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 同一用户与 Agent 有 S1/S2；两会话分别写入不同唯一秘密标记，真实模型请求 trace 可审计但已脱敏。 |
| **测试步骤** | 1. 在 S1/S2 分别连续对话。<br>2. 在各自会话询问本会话标记。<br>3. 检查模型输入来源、checkpoint thread 和最终消息。 |
| **预期结果** | 每个 Run 只读取自己 Session 的 history/context；S1/S2 使用不同 thread；任何模型输入和回复都不出现另一会话标记。 |

### TC-M02-004: WebSocket 断线不取消 Run，刷新恢复与事件补流边界明确

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | S1 可触发持续数秒的真实 Run；浏览器代理可中断 WebSocket 但保持 API/Worker 正常。当前 WebSocket packet/前端未暴露完整 Runtime event cursor 或 reattach 协议。 |
| **测试步骤** | 1. 发送消息并在 streaming 中断开 WebSocket。<br>2. 等待 Worker 继续执行。<br>3. 重新连接并刷新页面核对持久化消息/checkpoint。<br>4. 待 cursor/reattach 协议实现后再验证事件补流；此前登记 `BLOCKED/待实现`。 |
| **预期结果** | 当前可验收断线不生成 cancel、Run 在服务端继续、刷新后恢复最终消息/终态；不能声称已按 `(created_at,event_id)` 补齐中间事件。事件无重复/漏失的精确重放必须在协议落地后单独通过。 |

### TC-M02-005: 删除 Direct Session 后不再读取或投递其上下文

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | S1 有历史消息与 context，另有 S2；S1 当前无不可中断副作用。 |
| **测试步骤** | 1. 通过真实 API 删除 S1。<br>2. 尝试读取、发消息和恢复 S1 Run。<br>3. 从 S2 发起新 Run。<br>4. 检查模型输入与投递目标。 |
| **预期结果** | S1 默认查询、上下文读取和新执行全部拒绝；不会把 S1 history 注入 S2；S2 正常工作且不会接收原本属于 S1 的前台结果。 |

---

## M03 - Runtime Wait、Resume、Cancel 与重启

### TC-M03-001: waiting_user 经浏览器输入恢复同一 Run

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 配置真实 Agent 在缺少必填参数时调用 wait；浏览器、API、Worker 与 PostgreSQL 均运行。 |
| **测试步骤** | 1. 发送缺少参数的任务。<br>2. 等待公开问题和 `waiting_user` checkpoint。<br>3. 在浏览器提交答案。<br>4. 等待完成并检查 run/thread/correlation。 |
| **预期结果** | wait 以可恢复 JSON interrupt 持久化；答案只恢复原 Run/Thread 一次；恢复后继续原任务并形成唯一终态回复，不新建替代 Run。 |

### TC-M03-002: waiting_agent 与 waiting_external 只接受匹配回调

| 项目 | 内容 |
|-|-|
| **优先级** | P0；waiting_external BLOCKED / 待实现 |
| **前置条件** | 创建真实 A2A 子任务使 source 进入 `waiting_agent`；另准备 `waiting_external` checkpoint。当前 ref 尚无真实 webhook/timer/provider callback adapter 提交 external/timer resume Command。 |
| **测试步骤** | 1. 对 waiting_agent 先发送错误 actor/run/correlation，再完成真实 target。<br>2. 检查 source 精确恢复。<br>3. 外部 adapter 落地后从真实 webhook/timer 发送错误与合法事件；此前不得直接插 Command 冒充 E2E。 |
| **预期结果** | waiting_agent 错误回调 fail closed，合法 target 终态精确恢复 source；waiting_external 子场景在生产入口实现前保持阻塞，落地后必须由真实入口原子写 resume 并只按 checkpoint correlation 恢复。 |

### TC-M03-003: 重复和乱序 Resume 最多推进一次

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | R1 处于 waiting_user；准备同一 idempotency key 的并发 resume、旧 correlation resume 和终态后的迟到 resume。 |
| **测试步骤** | 1. 并发提交两个相同合法 resume。<br>2. 提交旧 correlation。<br>3. R1 完成后提交迟到 resume。<br>4. 统计 Commands、checkpoints、模型调用和回复。 |
| **预期结果** | 合法并发输入只有一次 applied 推进；旧/迟到输入确定性拒绝或返回已终态；不重复调用模型、工具或交付回复。 |

### TC-M03-004: Cancel 在副作用前后遵守真实状态边界

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 准备两个真实 Run：R1 尚未开始下一模型/工具，R2 的可回滚 sandbox 工具已记录 `started`。 |
| **测试步骤** | 1. 对 R1/R2 提交 cancel。<br>2. 继续运行 Worker 到稳定状态。<br>3. 查询 tool receipt、checkpoint、外部副作用和最终消息。 |
| **预期结果** | R1 在新调用前取消；R2 不伪装未执行，而是先记录真实 succeeded/failed/unknown 结果再进入取消闭环；任何已发生副作用不被自动重做。 |

### TC-M03-005: 真实进程重启后从同一 Checkpoint 恢复

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | R1 处于 waiting，R2 正在可安全重试节点；真实 PostgreSQL 持久化完成；可强制终止全部 API/Worker 进程。 |
| **测试步骤** | 1. 记录两个 Run 最新 checkpoint 和 Commands。<br>2. 强杀进程并用同一制品重启。<br>3. 恢复 R1、让 R2 对账后继续。<br>4. 检查 thread、调用次数和终态。 |
| **预期结果** | 重启不创建新 Thread/Run；R1 从原 interrupt 恢复，R2 按已提交 checkpoint/receipt 决定重试或对账；两者均只产生一次终态交付。 |

---

## M04 - 工具副作用、Unknown 与对账

### TC-M04-001: Worker 崩溃后复用已成功的真实工具 Receipt

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 测试租户配置一个可清理的真实副作用工具（例如创建临时任务或文件）；可在 provider 成功且 receipt 提交后、checkpoint 推进前终止 Worker。 |
| **测试步骤** | 1. 触发工具并确认外部对象已创建。<br>2. 在指定窗口强杀 Worker。<br>3. 重启并让同一 Run 恢复。<br>4. 对比外部对象、receipt 和 checkpoint。 |
| **预期结果** | 外部对象只创建一次；恢复命中 succeeded receipt 并复用结果，不再次调用 provider；checkpoint 最终消费同一结果继续。 |

### TC-M04-002: 并发 Worker 对同一 Tool Call 只有一个执行赢家

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 两个真实 Worker 可并发领取同一 Run 的重复 Command；工具 sandbox 支持按请求键查询调用次数。 |
| **测试步骤** | 1. 同时释放两个 Worker。<br>2. 观察 thread lock 和 tool reservation。<br>3. 等待 Run 完成。<br>4. 查询 provider、tool ledger 与 checkpoints。 |
| **预期结果** | 只有 advisory lock/reservation 赢家执行 provider；另一方等待或对账；tool ledger、外部对象和 checkpoint 中均只有一份逻辑结果。 |

### TC-M04-003: Provider 结果 Unknown 时禁止自动重试副作用

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 真实 sandbox provider 可接受请求后由网络故障注入层丢弃响应；无法通过查询接口立即确认结果。 |
| **测试步骤** | 1. 触发副作用并在 provider 接受后切断响应。<br>2. 等待 Worker 超时与重试周期。<br>3. 重启 Worker并运行 reconciliation。<br>4. 检查 provider 调用数和 Run 状态。 |
| **预期结果** | 账本记录 unknown；系统不自动重发非幂等副作用，也不猜测 succeeded/failed；Run 保持可人工对账状态且无第二个外部对象。 |

### TC-M04-004: Parallel Tool Exchange 作为完整 Block 持久化和压缩

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | Agent 一轮真实模型输出两个并行只读工具调用；可在第二个结果提交前终止 Worker，并可触发 Run Compact。 |
| **测试步骤** | 1. 执行并在只完成一个 tool result 时崩溃。<br>2. 重启恢复完整 Exchange。<br>3. 触发 compact。<br>4. 检查 message block、watermark 和模型输入。 |
| **预期结果** | 不完整 Exchange 不作为完整历史供模型继续；恢复后形成一组完整 assistant tool_calls + 全部 results；compact 不跨越半个 Block 或丢失 pending 结果。 |

### TC-M04-005: Primary 安全失败时按 Fallback 小窗口重建 Context

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 配置真实 primary/fallback Provider，fallback 输入窗口更小；可令 primary 在尚无有效 model step 且无 started tool 时返回可 failover 错误，另准备已有完整有效 step 和已 started tool 两种场景。 |
| **测试步骤** | 1. 触发安全 failover 并记录两次模型输入预算。<br>2. 检查 fallback 前的 Context 重建/裁剪和 Session Context。<br>3. 在已有有效 step、started tool 后分别请求普通 failover。<br>4. 检查模型/工具调用与 checkpoint。 |
| **预期结果** | 安全场景按 fallback 自身小窗口重新构建/裁剪本轮 Context，保持 Tool Exchange 完整且不覆盖共享 Session Context；当前实现不虚构持久化 Run Compact。已有有效 step 或 started 副作用后禁止普通切模重跑，转入原 checkpoint 恢复/对账。 |

---

## M05 - Task、Trigger、Schedule 与 Heartbeat

### TC-M05-001: Task 每次真实执行拥有独立 Run 且投影幂等

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 测试租户有可重复执行的真实 Task，Task Worker、Runtime Worker、模型网关与 PostgreSQL 均运行。 |
| **测试步骤** | 1. 连续执行同一 Task 两次。<br>2. 让第二次在终态投影前重启 Worker。<br>3. 查询 task executions、Run、thread、Commands、checkpoints 和 Task 状态。 |
| **预期结果** | 两次 execution 各有独立 run/thread 和稳定关联；每次只执行一次；重启后终态投影收敛且不重复回写 Task 完成/失败。 |

### TC-M05-002: Trigger 重复投递按 TriggerExecution 身份去重

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 配置一个真实业务 Trigger；可固定同一 TriggerExecution/occurrence ID 并从两个消费者并发投递。 |
| **测试步骤** | 1. 并发投递同一 occurrence 三次。<br>2. 等待 Trigger 与 Runtime Worker 处理。<br>3. 再投递一个新 occurrence。<br>4. 统计 Runs、Commands、工具效果和结果。 |
| **预期结果** | 重复 occurrence 只对应一个 Run 和一套副作用；新 occurrence 创建新 Run；入口不回退 legacy loop，也不因重复消息留下多条失败投影。 |

### TC-M05-003: Manual Schedule 每次请求创建新 Occurrence，幂等重试缺口可见

| 项目 | 内容 |
|-|-|
| **优先级** | P0；幂等重试 BLOCKED / 待实现 |
| **前置条件** | Scheduler 和 Runtime Worker 真实运行；Manual API 当前服务端每次生成新的 `uuid4` occurrence，且不接受客户端 occurrence/idempotency key。 |
| **测试步骤** | 1. 创建 Schedule 并手动触发一次。<br>2. 在响应丢失后重试同一 HTTP 请求。<br>3. 等待后台 Runs 完成。<br>4. 检查 occurrence、run_count、origin 与 delivery。 |
| **预期结果** | 当前两次请求生成两个 occurrence/Run 并分别增加 run_count；结果各自按 origin 交付。若产品要求响应丢失后安全重试，必须先增加客户端稳定幂等标识；在此之前不得把 Manual 与 Cron 的稳定 occurrence 语义混用或报告幂等通过。 |

### TC-M05-004: Cron Schedule 在多 Scheduler 与重启下每期只执行一次

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 两个真实 Scheduler 副本指向同一 PostgreSQL；创建高频但安全的测试 Cron，并可在触发窗口重启一个副本。 |
| **测试步骤** | 1. 跨越三个触发窗口。<br>2. 在第二个窗口并发轮询并重启一个 Scheduler。<br>3. 等待全部 Runtime Runs 完成。<br>4. 对照每期 occurrence 与副作用。 |
| **预期结果** | 每个计划时间点只有一个 occurrence/Run；没有漏期或同一期双跑；重启只恢复未完成工作，不改变已生成 occurrence 身份。 |

### TC-M05-005: Heartbeat 与 Oneshot 都经统一 Runtime 且关闭入口时显式失败

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 真实 Heartbeat/Oneshot 服务可发起安全测试任务；支持切换新 Run intake 开关但不存在 legacy worker。 |
| **测试步骤** | 1. 各触发一次并追踪 Run/Command/checkpoint。<br>2. 关闭对应 intake 后再次触发。<br>3. 恢复开关并观察已有 waiting Run。 |
| **预期结果** | 开启时两者都创建明确 origin 的后台 Run 并由统一 Worker 推进；关闭时新执行显式失败且不走 legacy；已有 LangGraph waiting Run 仍可按固化版本恢复。 |

---

## M06 - A2A 与 OpenClaw

### TC-M06-001: Native Source 到 Native Target 原子创建 Target Run

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 同租户 Native Agent A1/A2 均可运行；真实 A2A Gateway、Runtime Workers 和 PostgreSQL 已启动。 |
| **测试步骤** | 1. A1 通过 Native A2A 向 A2 发起带唯一关联的任务。<br>2. 在入口提交点注入一次数据库错误并重试。<br>3. 查询 A2A input `ChatMessage`、target Run/start Command 与 source 关联。 |
| **预期结果** | 成功路径在一个事务中写 A2A `ChatMessage` 和 A2 Run/Command，不创建 `GatewayMessage`；故障全部回滚；重试只生成一套 target 事实，source/target 使用独立 thread。 |

### TC-M06-002: task_delegate 等待 Target 完成后精确恢复 Source

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | A1 可调用真实 `task_delegate` 给 A2；A2 能产生可验证结果。 |
| **测试步骤** | 1. 触发 A1 委托并等待 A1 进入 waiting_agent。<br>2. 让 A2 完成。<br>3. 重放 target-terminal 事件。<br>4. 检查 A1 resume、tool receipt 和最终回复。 |
| **预期结果** | A2 结果与委托关联原子回写；A1 只恢复一次并消费结构化结果；重复 terminal 不重复 resume、模型调用或用户交付。 |

### TC-M06-003: Target 失败或取消以结构化结果恢复 Source

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 分别让两个真实 target Runs 因确定性业务错误 failed、因用户动作 cancelled；source Runs 均在 waiting_agent。 |
| **测试步骤** | 1. 推进两个 target 到终态。<br>2. 观察 source checkpoint 和下一次模型输入。<br>3. 完成 source。<br>4. 检查公开消息脱敏。 |
| **预期结果** | Source 收到区分 failed/cancelled 的结构化结果而非伪成功；可以据此继续或终止；内部堆栈/凭据不进入 source 模型输入或用户消息。 |

### TC-M06-004: A2A Notify 与 Delegate 等待语义分离并执行 Cycle Guard

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | A1/A2/A3 可执行 notify 和 delegate；可构造数据库 parent chain 与重复有向委托边。 |
| **测试步骤** | 1. A1 notify A2 后继续自身执行。<br>2. A1 delegate A2 并观察等待。<br>3. 执行 A→B→C 正常链。<br>4. 重复有向边直至候选调用达到限制。 |
| **预期结果** | notify 不让 source waiting，delegate 必须关联等待；正常无环链通过；循环计数从真实 parent chain 重算，达到安全阈值的候选委托在创建 Target Run 前拒绝。 |

### TC-M06-005: Native 与 OpenClaw 双向委托按各自 Durable 契约闭环

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 已配置真实可隔离的 OpenClaw sandbox 端点和凭据；OpenClaw adapter、outbox 与 Runtime Worker 均运行。 |
| **测试步骤** | 1. Native Agent 委托 OpenClaw，并在等待期间重启本地 Worker后接收 report。<br>2. 检查 source receipt/Resume 与本地 target Runs。<br>3. 再由 OpenClaw 通过 Gateway 委托 Native Agent。<br>4. 检查 Native target Run/Command 与回报。 |
| **预期结果** | Native→OpenClaw 只持久化 GatewayMessage、source tool receipt 和 source Resume Command，不创建本地 OpenClaw target Run/checkpoint；OpenClaw→Native 才创建本地 Native target Run/Command。两向均可跨重启恢复且无旁路内存 loop。 |

---

## M07 - 外部渠道 Origin、Fallback 与 Outbox

### TC-M07-001: 飞书 Sandbox 入站到同一会话回信完整闭环

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 至少一个真实飞书 sandbox 租户、测试机器人/用户/群、有效凭据和事件订阅已配置；真实渠道 adapter、API、Worker、PostgreSQL 均运行。 |
| **测试步骤** | 1. 从飞书测试会话发送带唯一标记的消息。<br>2. 等待 Agent 执行与飞书回信。<br>3. 查询 `source_channel`、`external_conv_id`、origin Session、Run 和 outbox receipt。 |
| **预期结果** | 入站只创建一个持久化会话和 Run；回复通过 outbox 回到同一飞书会话，本地 message/outbox/receipt 唯一且 origin 可追踪；Provider 已收但确认丢失时按 at-least-once 风险处理，不承诺网络侧绝对只出现一次。 |

### TC-M07-002: Provider 暂时失败只重试 Delivery 不重跑 Agent

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 飞书 sandbox 可用；受控网络故障层能在 delivery 前制造超时/5xx 后恢复，模型与 Runtime 正常。 |
| **测试步骤** | 1. 完成一个 Agent Run 并在发送阶段注入暂时故障。<br>2. 观察 execution 与 delivery 状态。<br>3. 恢复网络等待 outbox 重试。<br>4. 统计模型、工具和 provider 调用。 |
| **预期结果** | execution completed 与 delivery pending/failed 分离；只重试 provider delivery，不重跑模型或工具；恢复后同一 delivery key 获得唯一 receipt。 |

### TC-M07-003: Foreground 外部 Origin 不可用时禁止回退其他会话

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 飞书 sandbox 发起 foreground Run；在终态前删除/失效其持久化 origin Session，同时同用户另有 active primary Session。 |
| **测试步骤** | 1. 完成 Run 并触发 delivery。<br>2. 检查原会话、primary、其他渠道会话和 outbox。<br>3. 重试 delivery。 |
| **预期结果** | Foreground 交付 fail closed，不向 primary 或其他 channel/conv 回退；失败状态持久化且可诊断；重试不制造错投消息。 |

### TC-M07-004: Background 只回退同一 Durable Scope 的 Primary

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 两个飞书来源的 Direct background Runs 原 Session 均失效；R1 的 `(tenant_id, agent_id, user_id)` scope 有可写 primary（可为 Web Session），R2 同 direct scope 无 primary；另有其他用户/Agent/群会话。 |
| **测试步骤** | 1. 完成 R1/R2。<br>2. 检查 delivery target 选择。<br>3. 观察飞书和 Web 会话。<br>4. 重放 terminal delivery。 |
| **预期结果** | R1 回退到同一 `(tenant_id, agent_id, user_id)` direct scope 的 primary，允许从飞书 origin 合法回到 Web primary；R2 明确 delivery failed；绝不跨 tenant、Agent、用户或 group scope，也不自动创建会话。 |

### TC-M07-005: Provider 接受但响应丢失时按 At-Least-Once 风险重试与审计

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 飞书 sandbox 与网络故障层可在 provider 接受消息后丢弃响应；暂时无法按 provider ID 对账。 |
| **测试步骤** | 1. 触发最终 delivery 并在 Provider 接受后断链。<br>2. 观察同一 `channel_deliveries` 的退避、attempt_count 与稳定幂等键。<br>3. 重启 Worker 并运行到 delivered 或最大次数 failed。<br>4. 对账飞书消息、outbox 与日志。 |
| **预期结果** | 当前状态只在 pending/claimed/delivered/failed 间转换，不虚构 unknown 状态；异常按同一 target/key 自动退避重试，达到上限为 failed，且不切换 target、不重跑 Graph。若 Provider 无幂等能力，可能产生重复消息，必须在日志/执行报告披露 at-least-once 风险。 |

---

## M08 - 群创建、成员与 Session

### TC-M08-001: 真实 API 创建、查询和修改群且不自动造 Session

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 部署 `0ef0f8d4+c959dffe` 后端；真实 PostgreSQL 有测试租户、human Participant 和认证 token。 |
| **测试步骤** | 1. POST 创建两个同名群。<br>2. GET 列表/详情并 PATCH 其中一个。<br>3. 查询 groups、members 和 chat_sessions。<br>4. 以非成员查询。 |
| **预期结果** | 两群以 ID 区分，创建者自动成为 manager；修改只影响目标群；新群没有隐式 Session/primary；非成员不可见且不泄露存在性。 |

### TC-M08-002: 成员邀请、移出与 Agent 可见性权限端到端生效

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 有 manager、普通 human、可见 Company Agent、Private Agent 和另一租户 Participant。 |
| **测试步骤** | 1. manager/普通 human 分别邀请可见 Agent。<br>2. 尝试邀请 Private/跨租户目标。<br>3. 普通成员与 Agent 尝试移出成员。<br>4. manager 移出并重新邀请同一 Participant。 |
| **预期结果** | 合法邀请创建/恢复唯一 membership；Private 与跨租户拒绝；只有 manager 可移出；Agent 无管理权；重邀复用原 membership 且移出期间无访问权。 |

### TC-M08-003: 第三方同步成员绑定闭环按实现状态执行

| 项目 | 内容 |
|-|-|
| **优先级** | P1 |
| **前置条件** | 第三方通讯录同步和平台账号绑定能力已部署，并有真实 sandbox 目录中的 bound/unbound 成员。当前目标 ref 未提供完整能力时，本用例登记 `BLOCKED/待实现`，不得以 mock 或手工插库记为通过。 |
| **测试步骤** | 1. 执行真实目录同步并查询邀请候选。<br>2. 分别邀请 bound/unbound 成员。<br>3. 移出 bound 成员后再次同步。<br>4. 检查 Participant、membership 和访问权。 |
| **预期结果** | bound 成员解析为 human Participant 并可入群；unbound 在写 membership 前拒绝且不建占位身份；后续同步不擅自恢复已移出成员。 |

### TC-M08-004: 群 Session 创建、Primary 选举与删除在并发下闭合

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 无 Session；两个 human clients 和 manager 可并发调用真实 API，Agent token 可用于负向请求。 |
| **测试步骤** | 1. 并发创建 S1/S2。<br>2. Agent 尝试创建/改名。<br>3. 写消息形成可控活跃排序。<br>4. manager 删除 primary，再删除最后 Session。 |
| **预期结果** | 始终最多一个 active primary；Agent 请求拒绝；删除 primary 按固定排序选唯一 replacement；删除最后 Session 后允许无 primary且不自动补建。 |

### TC-M08-005: 群管理浏览器主链路在前端就绪后验收

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 对应群聊前端已实现并部署，浏览器使用真实 API/认证/数据库；目标 ref 本身无前端变更，因此仅部署该 ref 时登记 `BLOCKED/待实现`，不得声称已通过。 |
| **测试步骤** | 1. 以 Manual-Browser 创建群、搜索/邀请成员。<br>2. 创建、切换、重命名和删除 Session。<br>3. 刷新并用第二成员登录。<br>4. 核对 API 与数据库。 |
| **预期结果** | 能力就绪后 UI 与后端权限/primary 状态一致，刷新可恢复且无伪造默认 Session；当前 ref 缺前端时只能记录明确阻塞。 |

---

## M09 - 群消息、Mention、ACK 与未读

### TC-M09-001: 单 Agent Structured Mention 从消息提交到回复原子闭环

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1/S1 有 human U1 和 active Agent A1；通过真实 API 或已就绪前端发送结构化 Participant mention，Runtime Worker 与真实模型运行。 |
| **测试步骤** | 1. U1 发送含唯一标记并 mention A1 的消息。<br>2. 观察消息、ACK 和最终回复。<br>3. 查询 Run Registry、start Command、checkpoint 和 delivery receipt。<br>4. 重放原请求。 |
| **预期结果** | 用户消息、A1 Run 和 start Command 同事务接受；ACK/最终回复各一次且回 S1；重放不产生第二 Run、ACK 或回复。 |

### TC-M09-002: 文本名称、人类和无效 Mention 不触发越权 Run

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 有 A1、human U2、已移出 A2；另有跨群 A3 和不存在 Participant ID。 |
| **测试步骤** | 1. 发送仅含 `@A1名称` 的纯文本。<br>2. 发送结构化 mention U2。<br>3. 同一消息 mention A1、A2、A3、无效 ID 并重复 A1。<br>4. 查询消息与 Runs。 |
| **预期结果** | 前两条公开可见但不触发 Agent；混合消息只为 active A1 触发一次；无效目标不阻塞有效消息、不泄露身份详情，也不创建占位 Run。 |

### TC-M09-003: ACK 在 Durable Start 提交后且 Graph 前幂等可见

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 可通过真实进程闸门暂停 Graph 节点但允许入口事务与 ACK worker 工作；G1/S1/A1 有效。 |
| **测试步骤** | 1. 在入口事务提交前观察消息流。<br>2. 提交后保持 Graph 暂停并等待 ACK。<br>3. 重启 ACK worker、重放 Command。<br>4. 放行 Graph 到 completed/failed/cancelled。 |
| **预期结果** | 提交前无 ACK；Run/start Command durable 后、Graph 未执行也有一条普通公开 ACK；稳定 delivery key 防重复；终态再形成唯一安全可见闭环。 |

### TC-M09-004: 消息历史按 Message Position 稳定且读取不重触发 Mention

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 在真实 PostgreSQL 中准备 `created_at` 相同、`id` 不同的多条公开消息，其中一条曾 mention A1 并已完成。 |
| **测试步骤** | 1. 通过 API 分页读取并在页间插入新消息。<br>2. 刷新订阅并执行上下文重建。<br>3. 重复读取历史。<br>4. 对比 Run/Command 数量。 |
| **预期结果** | 分页和事件均按 `(created_at,id)` 全序，无重复/漏项；历史 token 原样展示；读取、重连和上下文重建不再次触发 mention。 |

### TC-M09-005: 未读在并发与延迟 Read Update 下保持单调

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 有 U1/U2、S1/S2；已生成 human 消息、Agent ACK/终态、background callback、内部 event 和 workspace 变更。 |
| **测试步骤** | 1. U1/U2 从不同设备读取到不同位置。<br>2. 先提交新位置 P2，再延迟提交旧 P1。<br>3. 查询 membership JSON read state 与各 Session 未读。<br>4. 对照消息类别和发送者。 |
| **预期结果** | Watermark 以 Message Position 单调前进且每 Session 独立；公开 Agent/callback 计入其他成员未读，发送者自身及内部/workspace 事件不计入；延迟 P1 不回退。 |

---

## M10 - Planning、A2A 与 Mention Lane

### TC-M10-001: 多 Agent Mention 使用真实 Planning Model 创建 Child Runs

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1/S1 有 A1/A2；独立 `MULTI_AGENT_PLANNING_MODEL_ID` 指向真实可用模型，业务 Agent 使用不同模型；可审计模型请求。 |
| **测试步骤** | 1. 发送同时 mention A1/A2 的明确协作任务。<br>2. 检查 Planning Root、固定模型快照、请求 tools 和结构化计划。<br>3. 等待 child Runs 与根汇总。 |
| **预期结果** | 入口只建一个 Planning Root；规划模型被 pin 且无业务工具；只为 mention 集合内 Agent 建 child Runs；最终只交付一套汇总结果。 |

### TC-M10-002: 用户依赖顺序驱动真实 Scheduler 且失败只阻塞后代

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 用户任务明确要求 A1 完成后 A2 审核，同时安排独立 A3 步骤；配置 A1 调用确定性失败的安全测试工具。 |
| **测试步骤** | 1. mention A1/A2/A3 并等待计划。<br>2. 在 A1 未终态前检查 ready children。<br>3. 让 A1 failed、A3 completed。<br>4. 重放 child-terminal 通知并检查根汇总。 |
| **预期结果** | A2 依赖 A1 且不会提前创建/启动；A1 失败只阻塞 A2，A3 继续；重复通知不重复 child/resume；根如实汇总成功、失败和阻塞。 |

### TC-M10-003: 同 Agent Mention Lane 按服务端位置串行并跨重启释放

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 同租户 G1/G2 都有 A1；创建按 `(created_at,id)` 可确认顺序的 R1/R2/R3，R1 会进入 waiting_user；两个 Runtime Workers 运行。 |
| **测试步骤** | 1. 并发提交三条 mention A1。<br>2. 确认 R1 waiting 后观察 R2/R3。<br>3. 重启 Workers 并恢复 R1 到 terminal。<br>4. 记录后续 claim 顺序。 |
| **预期结果** | 三者共享 `group_mention:{tenant}:{agent}` lane；waiting 持 lane，R2/R3 不越过；checkpoint terminal 后按 Message Position 依次释放，重启/reconciliation 不造成双占。 |

### TC-M10-004: Planning 与普通 A2A 不占 Mention Lane

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 一个多 mention Planning 被真实模型延迟；同时准备单 mention A1 和普通 Direct A2A→A1。 |
| **测试步骤** | 1. 暂停 Planning 输出。<br>2. 提交单 mention 和普通 A2A。<br>3. 恢复 Planning 并观察 A1 child 创建。<br>4. 检查各 Run lane key 与执行时序。 |
| **预期结果** | Planning Root 和普通 A2A 无 group mention lane；单 mention 可先执行；只有 Planning 创建出的 A1 child 进入 lane，并按自己的 origin Message Position 排队。 |

### TC-M10-005: 群协作中的 A2A Cycle Guard 从真实 Parent Chain 重算

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 群内 A1/A2/A3 配置真实 delegate 工具；可通过安全任务形成 A→B→C 和重复 A↔B 有向边，数据库 parent chain 可审计。 |
| **测试步骤** | 1. 执行正常 A→B→C。<br>2. 逐次形成重复有向边。<br>3. 在达到第五次候选调用前后检查 Target Run。<br>4. 插入 human/planning ancestor 再验证。 |
| **预期结果** | 正常链通过；只统计 Agent delegation 有向边，human/planning 不计；重复预算按全链 `sum(max(edge_count-1,0))` 计算，多条边可共同达到阈值；候选调用使总重复次数达到 5 时在创建 Target Run 前拒绝。 |

---

## M11 - 群上下文、公告、Memory、Workspace 与 Compact

### TC-M11-001: 真实模型输入按固定群 Context 顺序且保留当前消息

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1/S1/A1 已填充规则、任务、公告、summary、超过 20 条消息、A1 memory、workspace 索引和 Planning 信息；模型网关 trace 可审计。 |
| **测试步骤** | 1. 发送带唯一标记的当前 mention。<br>2. 读取本次冻结 snapshot 与实际模型输入。<br>3. 对照各来源顺序、Session 和租户。<br>4. 制造超预算后重试新 Run。 |
| **预期结果** | 输入顺序符合设计且最近窗口只含 S1 最新 20 条公开消息；当前触发消息永不被截掉；无其他 Session/租户内容，截断区块带来源说明。 |

### TC-M11-002: Announcement 权限、Revision 快照与截断读取闭环

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 有 human U1、Agent A1 和超长公告；R1 已冻结旧 revision，U1 随后更新公告再创建 R2。 |
| **测试步骤** | 1. U1 通过真实文件/API 更新公告。<br>2. A1/非成员尝试写入。<br>3. 比较 R1/R2 模型输入。<br>4. 让 A1 用受权工具读取被截断剩余内容。 |
| **预期结果** | 只有 active human 可写；R1 保持旧 revision，R2 读取新 revision；默认按上限截断并标注，完整剩余内容只经权限工具显式读取。 |

### TC-M11-003: Group Memory 自动注入与 Peer 读写权限真实生效

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 有 A1/A2 及各自唯一 memory 标记，U1 为 active human；真实文件服务和 Runtime Worker 运行。 |
| **测试步骤** | 1. 触发 A1 Run 并检查模型输入。<br>2. A1 用工具读 A2、写 A1、写 A2。<br>3. U1 读写两者。<br>4. 移出 A1 后重试。 |
| **预期结果** | 只自动注入 A1 memory；A1 可读 peer 但只能写自己，U1 按权限管理；写 A2 和移出后的访问拒绝且无文件副作用。 |

### TC-M11-004: Group Workspace 跨 Session 共享且跨空间复制按状态验收

| 项目 | 内容 |
|-|-|
| **优先级** | P1 |
| **前置条件** | G1 有 S1/S2 和真实 workspace；跨空间复制的后端/前端、权限、preview/confirm 尚未完整实现时，该子场景登记 `BLOCKED/待实现`，不得用直接复制文件冒充通过。 |
| **测试步骤** | 1. 从 S1 创建文件并由 S2 读取/更新。<br>2. 并发提交 stale revision 与路径穿越。<br>3. 能力就绪后以 Manual-Browser preview、confirm 复制明确文件到 G2。<br>4. 修改源文件并检查目标。 |
| **预期结果** | 基础 workspace 以 group 共享，revision 冲突和路径逃逸拒绝；跨空间能力就绪后只复制经确认且有权内容并记录来源/审计，不自动同步；未就绪时明确阻塞。 |

### TC-M11-005: Group Session Compact 共享状态、预算与 CAS 端到端收敛

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | S1 有 A1/A2 不同模型预算和超过阈值的真实公开消息；独立 Compact Model 已配置；两个 compact workers 可并发。 |
| **测试步骤** | 1. 达到最小有效预算 85% 前后观察触发。<br>2. 并发运行两个 workers。<br>3. 检查 batch、watermark identity、version CAS 和 recent window。<br>4. 注入一次模型失败后继续发消息。 |
| **预期结果** | S1 只维护 `agent_id=null` 的共享 context；按完整消息分批且保留最近 20 条；只有一个 CAS 赢家，watermark 不回退；失败保留旧摘要且不阻塞对话。 |

---

## M12 - 删除、租户隔离、故障、可观测性、容量与发布

### TC-M12-001: 删除 Session 取消前台派生协作而保留独立后台 Run

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | S1 绑定 foreground root、Planning orchestration、delegated descendants 和独立 Task/Trigger background Runs；manager 可调用真实删除 API。 |
| **测试步骤** | 1. 删除 S1 并重放请求。<br>2. 等待 cancel Commands 应用。<br>3. 让 background Runs 完成。<br>4. 检查 checkpoints、delivery 和消息。 |
| **预期结果** | 前台根及其 orchestration/delegated 后代幂等取消；独立后台继续；前台不回退其他 Session，后台仅按合法 origin/fallback 交付。 |

### TC-M12-002: 群解散立即不可见且全量异步硬删按实现状态验收

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | G1 含消息、Sessions、公告、memory、workspace、context 和 Runs；manager 可解散。当前目标 ref 尚缺全量 hard-delete worker 证明，最终正文/文件清理必须登记 `BLOCKED/待实现`，不得从 `deleted_at` 推断通过。 |
| **测试步骤** | 1. 调用真实解散 API 并立即从列表、直链、文件 API 访问。<br>2. 检查 cancellation 与 `deleted_at`。<br>3. 能力就绪后运行异步清理并故障重试。<br>4. 检查当前文件、`workspace_file_revisions.before_content/after_content`、Context 与审计保留。 |
| **预期结果** | 当前实现可验收用户侧立即不可见和 Run 取消；能力完备后最终硬删公开正文/文件、group-scoped revision history 正文与 Context，且重试幂等，只保留设计允许的最小 membership/audit 元数据；未实现部分明确阻塞。 |

### TC-M12-003: 租户隔离和权限校验先于 Checkpoint 与文件读取

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | T-A/T-B 各有 Direct/Group Session、Run、checkpoint、announcement、memory、workspace 和渠道 origin；准备跨租户 ID 拼接请求。 |
| **测试步骤** | 1. 从 API、resume/cancel、工具和 delivery 入口交叉替换 tenant/group/session/run/participant ID。<br>2. 观察 PostgreSQL、文件和模型网关访问。<br>3. 检查响应与安全日志。 |
| **预期结果** | 所有错配在敏感 checkpoint/文件/模型读取前 fail closed；不泄露对象存在性或内容；无跨租户 Command、Run、工具副作用和交付。 |

### TC-M12-004: 数据库、Worker、模型与渠道故障均产生可操作观测证据

| 项目 | 内容 |
|-|-|
| **优先级** | P0；完整 Observability BLOCKED / 待实现 |
| **前置条件** | 可受控暂停 PostgreSQL、强杀 Worker、制造模型 5xx 和飞书 delivery 超时。当前 ref 只有通用 trace ID/日志和各 worker 持久化事实，没有完整 Runtime metrics、分布式 trace、alerts 或通用 reconciliation daemon。 |
| **测试步骤** | 1. 在不同阶段逐一注入四类故障。<br>2. 恢复依赖并运行明确存在的 Command Worker、Projector 与 outbox worker。<br>3. 关联 run/command/checkpoint/delivery IDs。<br>4. metrics/traces/alerts 落地后再验收仪表盘和告警。 |
| **预期结果** | 当前可验收结构化日志脱敏、持久化事实可关联、各专用 worker 收敛；不能声称已有通用 reconciliation 或全套指标/trace/告警。完整观测能力实现后，才要求区分 intake/execution/wait/resume/delivery 并产生可操作告警。 |

### TC-M12-005: 容量压测与发布演练证明无双写和可恢复

| 项目 | 内容 |
|-|-|
| **优先级** | P0 |
| **前置条件** | 已评审生产等价负载模型、SLO、数据量和容量阈值；使用真实 PostgreSQL、多个 API/Worker/Scheduler、副本渠道 sandbox，并准备备份与回退手册。 |
| **测试步骤** | 1. 混合压测 Direct、Task、A2A、群 mention、compact 和 delivery。<br>2. 观察索引、锁、lane、outbox 与 checkpoint 增长。<br>3. 演练停写迁移、滚动发布、扩缩容和 roll-forward 恢复。<br>4. 压测后对账，并验证 downgrade guards。 |
| **预期结果** | 在评审 SLO 内无越权、重复副作用、消息漏失、lane 双占或 watermark 回退；发布期间无旧新 schema/runtime 双写；扩缩容和 roll-forward 后 durable 状态收敛。非空 Runtime 表/新 Session 语义下的数据库 downgrade 与旧 legacy Runtime 回退被明确阻止，不把它们写成可执行回滚方案。 |

---
