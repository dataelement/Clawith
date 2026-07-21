# 群聊第二轮深度审计：新增问题总表

> 审计日期：2026-07-15
> 审计范围：PRD、技术方案、当前 integration worktree、Hy3 两组权威快照与前端状态机
> 性质：只读复盘；本文件记录已由代码或保存快照确认的问题，不把假设写成已复现事实

> 2026-07-16 产品决策校正：审计中的 `GA-SEC-01` 不再视为问题。Group 内 A2A 按 PRD 直接复用全局 Agent-pair A2A Session 与私下上下文，不按来源 Group/Session/User/Run 隔离；只要求未公开内容不自动进入当前 Group 的公开消息、Session Context、Group Memory 或 Group Workspace。下文保留原始审计记录用于追溯，但相关 source-scoped 修复建议不实施。

## 1. 结论

第一轮复盘主要暴露了 Planning 过重、公开 Chat 被内部状态污染、失败节点不可恢复和自主协作不足。第二轮继续下钻后，确认问题已经扩展到安全隔离、数据一致性、Runtime 可恢复性和前端并发状态机。

最严重的新增结论有五个：

1. A2A Session 只按租户和 Agent pair 复用，群内未公开协作内容会进入 pair-global `chat_messages` 和 Session Context，存在跨群、跨请求人串用风险。
2. 群 Runtime 只 denylist 了基础私有文件工具，但仍保留 `execute_code` 等可物化并回写 Agent 私有 Workspace 的工具，群隔离可以被绕过。
3. `send_channel_message` 等语义等价发送工具不在 autonomy 映射中，且 Runtime 初次执行 external-write 工具不要求确认，可以绕过审批边界。
4. Runtime Command 达到最大重试次数后仍保持 `pending`，但 claim 查询永久排除它；同 Run 后续命令会被它一直阻塞。
5. Markdown code fence 的 language 字符串未经 attribute escape 就进入 `dangerouslySetInnerHTML`，群消息或共享 Markdown 文件可形成持久化 XSS。

因此当前版本不能仅通过“复杂任务能跑到终态”作为上线判断。安全边界和数据一致性必须先修。

## 2. Critical / High 问题

| ID | 严重度 | 问题 | 已确认影响 | 详细记录 |
|-|-|-|-|-|
| GA-SEC-01 | 已撤销 | A2A 上下文按 Agent pair 全局复用 | 后续产品决策确认这是全局 Agent-pair A2A 的预期连续性；只禁止私下内容自动进入 Group 公开上下文 | `known-issue-group-runtime-security-boundaries.md` |
| GA-SEC-02 | Critical | 群 Run 可借 `execute_code` 穿透 Agent 私有 Workspace | `workspace/`、`memory/`、`skills/`、`soul.md` 被物化到临时目录，执行后 `sync_back=True`；群成员可诱导读取、泄露或修改私有状态 | 同上 |
| GA-SEC-03 | Critical | 外部发送可绕过 autonomy/确认 | `send_channel_message` 可路由到飞书发送却没有 autonomy 映射；Runtime 对新 external-write reservation 返回 `requires_confirmation=false` | 同上 |
| GA-RUN-01 | Critical | Command 重试上限形成永久 pending 墓碑 | 第 5 次失败后命令不可再 claim，也没有转 terminal/reconcile；后续 resume/cancel 被 earlier pending 永久挡住 | `known-issue-runtime-command-attempt-tombstone.md` |
| GA-SEC-04 | Critical | Markdown code fence language 持久化 XSS | 用户或 Agent 控制的 code language 可跳出 `class` 属性，在群消息和 Workspace Markdown 预览中执行 HTML/事件处理器 | `known-issue-markdown-and-url-security.md` |
| GA-DATA-01 | High | Workspace 乐观锁和编辑状态会静默覆盖 | 保存前才读取最新 token，旧 draft 能合法覆盖新版本；切文件/切群还能把 A/G1 内容写进 B/G2 | `known-issue-group-workspace-editing-data-corruption.md` |
| GA-MEM-01 | High | `group_read_memory` 身份字段确定性混淆 | 第二组 35/43 次失败；失败参数 100% 是 `participant_id`，成功参数 100% 是 Agent ref ID | `known-issue-group-memory-verification-and-side-effects.md` |
| GA-VERIFY-01 | High | Verifier 只验证工具名，不验证目标对象和任务语义 | 跨 Agent memory 全失败时，只要 self read 同名工具成功，step 仍可被标为 `planning_tool_evidence_verified` | 同上 |
| GA-FAIL-01 | High | failed Run 保留永久业务副作用 | 一个最终 failed 的 child 已成功写入 2,655 字符 memory，失败后文件和 revision 仍存在，后续可被当成正式记忆 | 同上 |
| GA-CTX-01 | High | 每个成功 Run 都调用一次 Session Compact 模型 | Finalizer 永远生成 delta；terminal handler 无阈值调用 compactor；即使无待压缩消息也至少调用一次模型，直接违反短 Session 不压缩的 PRD | `known-issue-session-context-and-performance-amplification.md` |
| GA-CTX-02 | High | 依赖结果与公开消息重复注入 | 完整 dependency `result_summary` 进入 system Runtime JSON，同一内容又已作为公开群消息进入 recent messages；step 越多，Prompt 越膨胀 | 同上 |
| GA-PERF-01 | High | Workspace 内容与回执放大 | 主任务 216 次 read 只覆盖 31 个路径，正文约 6.08 倍放大；write receipt 近乎回显完整正文；工具 I/O 只占 Runtime 约 0.07% | 同上 |
| GA-PERF-02 | High | Session 列表热点 N+1 | API 对每个 Session 单独计算 unread，前端又在每条消息、mark-read 和断线轮询时刷新，数据库查询量随 Session 数线性放大 | 同上 |
| GA-PLAN-01 | High | Planning 输入契约存在不可满足死区 | API 允许 100 mentions，Plan 最多 50 steps 且要求每个候选 Agent 至少一步；51–100 Agent 时不存在合法计划 | `known-issue-group-lifecycle-and-contract-gaps.md` |
| GA-PLAN-02 | High | 1MB 原文无预算门禁进入 Planning | 消息正文允许 1,000,000 字符，Planning 在调用前不按模型预算拒绝或截断，可稳定触发超限、长延迟和成本放大 | 同上 |
| GA-LIFE-01 | High | 发送/调度/交付与删除存在 TOCTOU | 校验活跃群、Session、成员后再写入，中间没有一致锁；删除扫描完成后仍可能创建新 child 或向已删除 Session 写回复 | 同上 |
| GA-DATA-02 | High | Storage 与 revision/audit 事务不原子 | 文本写入和删除先改对象存储再写 DB；DB commit 失败时文件已变、revision 回滚；二进制上传有 compensation，文本路径没有 | 同上 |
| GA-DELETE-01 | High | 群解散没有异步硬删闭环 | 当前只 soft delete 群/Session、移除成员、发 cancel；消息正文、Workspace/Memory、revision 正文和 Session Context 无清理 worker | 同上 |
| GA-MENTION-01 | High | mention 名称子串可唤醒错误 Agent | 选中 `Ann` 后文本变成 `@Anna`，`includes('@Ann')` 仍保留 Ann ID；同名成员也无法消歧 | `known-issue-group-frontend-state-races.md` |
| GA-MENTION-02 | High | IME 回车误发送/误选 mention | Composer 不检查 `nativeEvent.isComposing`，中文/日文/韩文输入法确认候选时可能直接 submit | 同上 |
| GA-UI-01 | High | 前端协议无法区分 Run Compact 与 Session Compact | WS 丢掉持久事件中的 `phase/scope`，Session compactor又不发布群级事件；当前 `compacting` 只能代表 Run 内部状态 | 更新 `known-issue-group-compact-ui-scope.md` |

## 3. Medium / Low 问题

| ID | 严重度 | 问题与触发条件 |
|-|-|-|
| GA-UI-02 | Medium | 普通 WebSocket 断线不会清理 working/compacting 临时状态；终态事件丢失后动画最长残留 10 分钟。 |
| GA-UI-03 | Medium | 首次历史请求失败后 `hasMore` 保持 false；Realtime 即使补回最新 50 条，也无法再访问更早消息。 |
| GA-UI-04 | Medium | 顶部加载旧消息时若先收到 realtime append，会错误消费滚动高度标记，真正 prepend 后页面跳动。 |
| GA-DRAFT-01 | Medium | Composer 草稿和 picked mention 不按 Session 隔离；切 Session 后可能把旧草稿和 ID 发到新会话。 |
| GA-WORKSPACE-01 | Medium | 读取 403/500/网络错误被展示为空文件，用户仍可编辑并把真实内容清空。 |
| GA-WORKSPACE-02 | Medium | 目录显示删除按钮，但后端明确拒绝目录删除。 |
| GA-WORKSPACE-03 | Medium | 公告/Memory 409 后不 refetch、不更新 token，重复保存必继续失败；切页则丢未保存 draft。 |
| GA-WORKSPACE-04 | Medium | 前端 delete 不传后端已支持的 expected token，可在并发更新后无条件删除新版。 |
| GA-A2A-01 | Medium | A2A Session 首次创建是 SELECT-then-INSERT，无 upsert/savepoint；并发首次调用可能主键冲突并回滚工具事务。 |
| GA-LIFE-02 | Medium | 移出 Agent 不取消其活跃 Run；模型和非 group 工具仍可继续执行、计费或产生外部副作用。 |
| GA-LIFE-03 | Medium | 删除 Session 会给所有历史 foreground/orchestration Run 发 cancel，包括已经完成的数百个 Run，形成事务和命令洪峰。 |
| GA-SCHED-01 | Medium | 全租户共用按 created_at 排序的全局 FIFO 和默认 4 worker；没有 tenant quota/轮转，单租户可造成其他租户饥饿。 |
| GA-SCHED-02 | Medium | 28 个 foreground 的 queue 中位 69.80s、最大 579.35s；14 个 >30s 的 Run 没有同 Agent blocker，现有 `queue_seconds` 无法区分 lane、worker capacity 和 poll wait。 |
| GA-SEC-05 | Medium | 完整 JWT 放入 WebSocket 和 Workspace 下载 URL，可能进入代理/access log、诊断和复制链接。 |
| GA-SEC-06 | Medium | Agent Markdown 可自动加载任意 http(s) 图片，查看者会发起 tracking 或内网 blind GET。 |
| GA-UPLOAD-01 | Medium | 单次 50MB 上传先完整缓冲，新版本覆盖时又完整读取旧文件用于 rollback；并发覆盖可给 worker 带来约 100MB/请求以上内存压力。 |
| GA-FE-TEST-01 | High（覆盖缺失） | 前端没有 test script、测试依赖或任何 test/spec；Workspace、mention、realtime 和滚动竞态无自动化保护。 |
| GA-DOC-01 | Medium | 最新产品决策要求“@ 接受后用头像状态点、不发确认消息”，当前 `group_status.py` 也实现 ephemeral status；但 PRD 2.6.2 仍要求普通群消息确认且不提供独立状态，文档已与实现和最新决策冲突。 |
| GA-DOM-01 | Low | 消息列表无虚拟化或内存上限，加载历史后 DOM 和 state 只增长。 |
| GA-COPY-01 | Low | 新会话 UI 提示标题可留空，但通用 PromptModal 禁止空值确认。 |

## 4. 本轮运行快照对已有判断的修正

### 4.1 第二组不是跨组泄漏，但比“串对话”更具体

- 真正触发 Planning 的是纯结构化 @ 消息，而不是前一条“大家自我介绍一下吧”。
- Planner 只看到纯 @ goal 和 7 个金融化 role description，于是从角色信息发明了金融研究任务。
- 后续 child 虽然能看到三条用户消息，但 system Runtime instruction 声称 trigger 是“完整、权威原始请求”，并强制执行金融 step，压过了普通历史消息。
- cross-group scan 未发现主组数字、Workspace 或 announcement 进入第二组，因此现有证据不支持数据库层跨组泄漏。
- 这次误任务仍运行 24 分 22 秒，产生 56 次工具调用和 6 份持久 memory，说明错误触发没有成本熔断。

### 4.2 “一开始就压缩”有两个不同根因

1. 主组 186 条 `route=compact` checkpoint 中，178 条（95.7%）没有 summary/watermark；它们只是经过 compact 节点，不代表真的压缩。前端若按 route 显示动画会从一开始反复误报。
2. 另一方面，成功 Run 的 terminal handler 确实会无阈值调用 Session Context compactor；这会制造真实但不应发生的 per-Run Compact 模型请求。

UI 必须只根据已提交的群 Session compact event/watermark 展示群级动画；后端同时要删除 per-terminal unconditional compact。

### 4.3 工具层不是长任务慢的主因

- 主组工具真实执行总耗时约 4.55 秒，只占 foreground runtime 6,651.22 秒的 0.068%。
- 第二组工具耗时占比约 0.080%。
- 性能瓶颈优先级应是 Planning 模型、模型调用、Prompt 基线、上下文重复、worker capacity 和队列公平性，而不是先优化磁盘 API。

## 5. 修复顺序

### P0：先封安全和永久卡死

1. ~~A2A Session 增加来源 scope，群内 A2A 不写 pair-global Chat/Session Context。~~ 已由 2026-07-16 产品决策撤销；保持全局 Agent-pair A2A。
2. 群 Runtime 改为 allowlist 工具集；禁止 `execute_code` 等访问私有 Workspace 的间接工具。
3. 所有语义等价外部发送工具统一映射到同一 autonomy action，并在 Runtime 执行前做确定性确认。
4. max-attempt Command 原子转 `failed/reconciliation_required`，解除 earlier-command 和 lane 阻塞。
5. 修复 Markdown attribute escape，并加真实 sanitizer/CSP 回归用例。

### P1：再修数据和上下文

1. Workspace 保存必须使用“打开文件时”的 version token；切文件/切群/读失败都不能复用旧 draft。
2. memory 工具 schema 明确区分 `participant_id` 与 `agent_ref_id`；Verifier 校验目标集合和产物，而不是工具名计数。
3. failed/cancelled Run 的持久副作用必须带 provenance、隔离或可回滚，不能无标识成为正式 memory。
4. 去掉每个 terminal Run 的 Session Compact；只对滑出 recent-window 且达阈值的消息批次压缩。
5. 依赖结果改为短结构化引用，避免与公开消息重复注入；read cache 和 write receipt 禁止回显全文。
6. 补 Storage/DB compensation 和群硬删 worker。

### P2：补交互、恢复和容量

1. mention token 化、IME 防误发、草稿按 Session 隔离。
2. Runtime/Planning/Compact 建立可恢复的 REST projection，WS 只做增量通知。
3. 批量 unread 查询、列表虚拟化、队列 wait-reason 拆分、tenant 公平调度。
4. 为上述 race 和安全边界补前端单测、后端并发测试和 E2E。

## 6. 证据边界

- Hy3 权威终态以 `test-artifacts/hy3-finance-run-20260715/hy3_authoritative_snapshot.json` 为准。
- `audit_two_group_contexts_result.json` 是中途快照，只能证明审计时点之前未发现跨组数据，不代表之后所有 child 都已逐个终态审计。
- 本轮没有重新连接 3010，也没有写数据库或执行攻击 payload；安全问题是由可达代码路径确认，正式修复后仍需隔离环境回归。
