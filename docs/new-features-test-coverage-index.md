# 统一 Runtime 与群聊 v1 测试覆盖总览

> 基线日期：2026-07-14
> 文档用途：说明本轮新功能测试用例的代码基线、需求来源、范围边界、执行顺序和发布门槛。
> 说明：当前 checkout 已切换为 `feature/unified-chat-single-agent-runtime-group-chat@0ef0f8d4`；未提交的 PRD 工作稿已从原分支安全恢复到本工作区。生产启动修复 `c959dffe` 是该 tip 的直接后续提交，作为附加发布验收基线单独纳入。
> 格式模板：飞书文档《Agent 通讯录与组织通讯录一期测试用例文档》，docx token `KMoTdTIYyoIQMPxLTTKcPI4XnTd`，读取 revision `11`。三套用例严格使用“模块目录 + `TC-Mxx-xxx` 编号 + 优先级 / 前置条件 / 测试步骤 / 预期结果”结构。

---

## 1. 本轮输出

| 功能域 | 测试用例文档 | 模块数 | 用例数 | 主要测试目标 |
|-|-|-:|-:|-|
| 统一 Single Agent Runtime | `docs/single-agent-runtime/runtime-test-cases.md` | 16 | 128 | 统一聊天迁移、LangGraph Runtime、Context/Compact、可靠恢复、工具幂等、所有单 Agent 入口和渠道交付 |
| Group Chat v1 | `docs/group-chat/group-chat-v1-test-cases.md` | 13 | 104 | 群领域、群 session/message、@ 唤醒、Planning、A2A、群上下文、群文件、未读、删除与权限 |
| Unified Runtime + Group Chat E2E | `docs/unified-runtime-group-chat-e2e-test-cases.md` | 12 | 60 | 真实 PostgreSQL、真实进程/Worker/模型、浏览器、渠道 sandbox 与跨功能故障恢复主链路 |
| **本轮新增合计** |  | **41** | **292** |  |

已有的 Directory 功能不重复生成：`docs/agent-roster/agent-directory-phase1-test-cases.md` 已包含 17 个模块、166 条用例。若把它计入本分支完整功能验收，四套文档合计 58 个模块、458 条用例。

## 2. 代码与提交基线

### 2.1 主实现基线

| 项目 | 值 |
|-|-|
| 基线 | `upstream/main` / `fa8a429b` |
| 实现 ref | `feature/unified-chat-single-agent-runtime-group-chat` |
| 实现 tip | `0ef0f8d4` |
| 独有提交 | 102 个 |
| 改动规模 | 201 个文件，约 `+59.5k / -7k` |
| 后续生产启动修复 | `c959dffe`，checkpoint schema 在 Runtime Worker 启动前确定性初始化 |

主实现提交按测试域分组如下：

| 提交域 | 代表提交 | 对应用例 |
|-|-|-|
| 统一聊天与群 Schema | `1c292132`、`70337485`、`77b9eb4d`、`61aeec45`、`132f916b` | Runtime M02；Group M01-M04 |
| Runtime Schema、Checkpoint 与模型能力 | `e4e69c3e`、`dcf07cfc`、`e2c5e160`、`24becd30`、`7ab6bc02`、`3bdb88dc`、`8d685c47`、`c959dffe` | Runtime M03-M05、M16 |
| Command、锁、恢复与投影 | `377013f4`、`2afce6da`、`c72720cf`、`0cad257b`、`60fbbb4b`、`db56b337` | Runtime M04-M06、M11 |
| Tool Ledger 与 Tool Pair Integrity | `0fedbb3f`、`ce0ec844`、`1e146a15`、`a35b012a` | Runtime M06-M07 |
| Session/Run Context 与 Compact | `d61b87f5`、`d210ba5e`、`7fbe93e2`、`ed3a7511`、`b7d8d000`、`ca78a087` | Runtime M08-M10；Group M09-M11 |
| Web、Task、Trigger、Heartbeat | `4d47513e`、`76faf823`、`5f67c169`、`97846918`、`c195b8d2`、`ee5c5f4b` | Runtime M13 |
| A2A 与 OpenClaw | `870c2070`、`2094d415`、`ce4b3654`、`fbd63807`、`31e5762b`、`6bd635bb` | Runtime M14；Group M08 |
| Group mention、Planning 与文件边界 | `f339eb5e`、`8f22041e`、`d4b6ad1e`、`a96c2835`、`8bc23efc`、`1aa1a718`、`15429fb4`、`74dbadcb`、`cc2a871d` | Group M05-M12 |
| 渠道 Runtime 与可靠交付 | `878e99d3` 至 `a31bf234`、`1ef6dbf7`、`229202f0`、`7d6b5d04`、`82efd8aa` | Runtime M12、M15 |
| 移除第二执行循环 | `6bd635bb`、`cb38452d`、`0ef0f8d4` | Runtime M01、M13、M16 |

### 2.2 旁支如何处理

| ref / commit | 处理方式 | 原因 |
|-|-|-|
| `feature/session-routing-origin-delivery` / `4aad57ee` | 产品语义并入 Runtime M12，不作为独立实现基线 | 主实现已经删除旧 `trigger_runtime/invoker.py`，并用统一 delivery/outbox 重做了 origin session 与渠道回投 |
| `benchmark/toolathlon-single-agent-runtime` / `2571b892` | 不纳入本轮产品功能用例 | 这是 benchmark-only CLI 与评测适配，不是 PRD 中的用户功能；可另建工程评测用例 |
| `benchmark/toolathlon-single-agent-runtime` / `c959dffe` | 纳入 Runtime M03/M16 | checkpoint bootstrap 顺序和 DSN 校验属于生产启动与发布安全 |
| `feat/company-agent-auto-contact` 的 Directory 提交 | 复用已有 166 条 Directory 用例 | 避免重复并保持已有执行报告中的用例 ID 稳定 |

## 3. 需求来源与优先级

测试用例按以下顺序解释需求：

1. 当前工作树中未删除线的产品 PRD 正文。
2. 同目录终版技术设计中的可执行契约。
3. `feature/unified-chat-single-agent-runtime-group-chat` 的 commit 决策、实现代码和现有 pytest。
4. 旧设计或历史提交只用于回归，不覆盖更新后的 PRD/技术设计。

主要需求文件：

| 文件 | 负责范围 |
|-|-|
| `docs/single-agent-runtime/runtime-context-compression-prd.md` | Runtime 产品目标、上下文、恢复、压缩、权限和验收标准 |
| `docs/single-agent-runtime/technical-design.md` | Schema、Graph、Command、锁、工具账本、交付、失败处理和上线门槛 |
| `docs/group-chat/prd.md` | 群聊产品规则、成员、session、@、Planning、memory、workspace 和未读 |
| `docs/group-chat/technical-design.md` | 群领域模型、统一 chat 表、可靠 mention、上下文、Compact、删除和 API |
| `docs/group-chat/chat-model-refactor.md` | 统一聊天 Schema 的强制迁移、回填和切换门槛 |
| `docs/session-routing/session-routing-design.md` | origin session 优先、同 scope primary 兜底和第三方渠道回投产品语义 |

优先级定义：

| 优先级 | 定义 | 发布要求 |
|-|-|-|
| P0 | 数据安全、租户隔离、执行唯一性、不可重复副作用、消息不丢失、核心主链路 | 全部通过；任一失败阻断发布 |
| P1 | 重要边界、异常恢复、兼容、管理能力和可观测性 | 必须通过，或有明确 owner、修复版本与可接受的临时隔离 |
| P2 | 大规模性能、长尾渠道、操作体验和增强型回归 | 允许在不影响 P0/P1 的前提下排期，但必须保留结果和风险 |

## 4. 固定测试数据

| 数据 ID | 内容 |
|-|-|
| `TD-RT-BASE-01` | 租户 T1；用户 U1/U2；Agent A/B/C；Direct Session S1 primary、S2 non-primary；可用 primary/planning/compact/fallback 模型 |
| `TD-RT-TENANT-02` | 租户 T2；与 T1 同名用户、Agent、session 和外部会话，用于跨租户隔离 |
| `TD-RT-FAIL-01` | 可注入 Worker kill、DB commit failure、checkpoint failure、provider timeout、delivery unknown 的故障环境 |
| `TD-RT-TOOL-01` | read-only、idempotent write、non-idempotent external write、unknown-outcome 四类工具 |
| `TD-RT-CHANNEL-01` | Web、Feishu、Slack、Teams、DingTalk、WeCom、WeChat、Discord、WhatsApp 的测试 session 与伪 provider |
| `TD-GC-BASE-01` | T1 中群 G1；manager U1、member U2/U3；Agent A/B/C；primary Session GS1、普通 Session GS2 |
| `TD-GC-MEMBER-01` | Company/Custom/Private/不可用 Agent；已绑定/未绑定平台账号的第三方成员；已移出成员 |
| `TD-GC-PLAN-01` | parallel、sequential、dependency DAG、循环依赖、未知 step、非法 Agent 等 Planning 输出 |
| `TD-GC-CONTEXT-01` | 超过 20 条消息、长公告、每 Agent 独立群 memory、多层 workspace、多个 session 和冲突决策 |
| `TD-GC-FAIL-01` | 群/session 删除、Agent 移出、Planning 失败、Compact 失败、清理部分失败和并发 primary/read-state 更新 |
| `TD-E2E-LIVE-01` | 生产等价真实 PostgreSQL、独立后端/Worker/Scheduler 进程、浏览器、真实规划/Compact 模型、飞书渠道 sandbox 与 OpenClaw sandbox |

所有 P0 用例必须能够重复执行；测试结束后删除或恢复测试数据、开关和外部伪消息，并检查没有残留 pending Command、持有中的 lane、未对账工具 receipt 或重复 delivery。

## 5. 已识别的口径差异

这些差异不能在执行时临时猜测，测试文档已经固定如下口径：

| 差异 | 本轮测试口径 |
|-|-|
| Runtime PRD 仍描述 legacy/langgraph 可分入口灰度；实现提交已经删除生产入口的 legacy tool loop | 灰度开关只控制是否接受新的 Runtime intake；禁用或配置错误时 fail closed，不允许回退旧执行循环；已有 LangGraph Run 始终按原 Runtime 恢复 |
| Runtime PRD 列出五张 Runtime 支撑表；实现另外新增 `channel_deliveries` | `channel_deliveries` 是跨 provider 边界的可靠投递 outbox，不是第六张执行状态表；它不得驱动 Graph 路由或恢复，但必须接受独立 migration、claim、retry 和幂等验收 |
| Group PRD 使用“删除群后硬删除全部数据”；技术设计采用 `groups.deleted_at` 立即不可见，再异步硬删消息/文件/Context，并保留最小成员与审计元数据 | 用户侧立即且不可恢复；正文和文件必须最终物理清理；最小审计元数据允许按技术设计保留，不得被业务查询或恢复入口读取 |
| Group PRD 的 topic 主章节已加删除线，但名词表和 workspace 沉淀仍有引用 | v1 不建 topic 表、不加 `chat_sessions.topic_state`、不提供 topic UI；topic 只允许作为 `session_context_states` 内部摘要状态 |
| 第三方渠道的 group session 使用 `session_type=group, group_id=null`；原生群要求真实 `groups.id` | 两者共享聊天模型但不是同一个业务对象；外部群不能绕过 Group API 伪装成原生群成员关系 |
| origin session 写入结果可能为 unknown | 必须先使用幂等键对账原目标；未确认失败前禁止立即 fallback 到 primary，避免双投递 |

实现 ref 尚未形成完整闭环的 PRD 能力仍保留测试用例，但不能报告为“已有自动化通过”：群聊前端、跨空间复制、第三方同步成员入群、真实 Provider/外部渠道、真实 PostgreSQL 迁移与多进程故障恢复。执行报告应将这些项标记为 `Blocked`、`Not Run` 或按实际环境执行，不能用 mock 单测代替。

## 6. 执行顺序

```text
静态契约与迁移审计
  -> PostgreSQL migration/checkpoint smoke
  -> Runtime 单元与服务测试
  -> Group 单元与 API 测试
  -> 故障注入与双 Worker/并发测试
  -> Web/渠道/群聊 E2E
  -> frontend build + Manual Browser
  -> 维护窗口、回滚、监控与容量验收
```

建议命令和证据顺序：

1. 在实现 worktree/ref 上运行聚焦 pytest，并保存 commit SHA、数据库版本和配置快照。
2. 用真实 PostgreSQL 执行统一聊天、Runtime、workspace scope、delivery outbox migration 和 checkpoint setup；静态 migration test 不能替代这一步。
3. 运行 Runtime、Group、渠道和迁移相关完整 pytest；现有 commit 记录中的 `451 Runtime and group tests` 只能作为历史证据，不能替代当前执行。
4. 执行 Worker kill、重复 Command、双 Worker、工具 unknown、delivery unknown、Session/Group 删除等故障注入。
5. 运行前端构建；仓库当前没有前端测试框架，因此 UI/E2E 用例标记 `Manual-Browser`，不得报告为自动化通过。
6. 对接至少一个真实外部渠道 sandbox；其余渠道可先用 provider mock，但发布风险必须单独列出。

## 7. 发布门槛

- P0 用例 100% 通过，且没有未对账的副作用或双投递。
- 真实 PostgreSQL migration、checkpoint setup、双 Worker 竞争和服务重启恢复通过。
- Web、Task、Trigger、Heartbeat、A2A 和启用的生产渠道都只走统一 Runtime；仓库中不存在可达的第二 tool loop。
- `completed` 与 `delivered` 可独立失败和重试；delivery 重试不重新推进 Graph。
- 同一 Agent 的群 mention 按 Message Position 串行，同时 Direct/Task/Trigger/Heartbeat 不被该 lane 阻塞。
- 群、Session、Context、checkpoint、workspace 和 delivery 全部通过租户与 scope 隔离测试。
- 删除、取消、unknown 工具和 unknown delivery 均完成故障注入验收。
- 可查询 Run 数、等待时长、恢复成功率、重复工具拦截数、pending Command、stale lane、delivery 重试/失败和清理失败告警。
- 维护窗口步骤、checkpoint setup 顺序、失败停止策略和回滚限制已完成演练并留存证据。

## 8. 执行报告要求

每条用例的结果必须记录 `Pass / Fail / Blocked / Not Run`，并至少包含：

- 测试目标 commit SHA 与配置。
- 对应用例文档路径与 `TC-Mxx-xxx` ID；相同编号在不同文档中通过文档路径区分。
- pytest node ID、API 请求/响应、浏览器截图、数据库断言或日志片段。
- 缺陷分类：产品缺陷、自动化/断言缺陷、环境问题、PRD 阻塞。
- 故障注入点、恢复后 checkpoint/Command/tool receipt/delivery receipt 的最终状态。
- 清理结果及是否存在残留副作用。

不要把“已有单元测试通过”直接等同于功能验收通过；当前实现提交明确未验证真实 PostgreSQL migration、真实 provider、浏览器和多进程故障恢复，这些是本轮测试用例必须补齐的主要风险。
