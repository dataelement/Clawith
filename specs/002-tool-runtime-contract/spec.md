# Feature Specification: Tool Runtime 契约与执行链路修复

**Feature Branch**: `002-tool-runtime-contract`
**Created**: 2026-08-10
**Status**: Draft
**Input**: 修复 Tool Runtime 中的工具集漂移、调用身份冲突、失败反馈缺失、修复次数混用，以及执行时限、取消和 Receipt lease 不协调的问题，并为长期统一 Tool Registry 建立兼容边界。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 已接受的 Tool Call 稳定执行 (Priority: P1)

作为使用 Agent 完成任务的用户，我希望模型已经基于本轮可见 Tool 做出的合法调用不会因为执行前工具配置被再次解析而意外失败，从而避免 Agent 在正确决策后仍中断任务。

**Why this priority**: 这是 CoAligne 评审确认的主问题。模型可见条件与执行条件不一致会直接降低 Agent 的任务完成率，并产生没有业务价值的额外修复轮次。

**Independent Test**: 模型产生一个当轮合法 Tool Call 后，在执行前修改该 Tool 的普通可见性配置；当前 Call 仍按原绑定执行，下一次模型决策则使用更新后的可见工具集合。

**Acceptance Scenarios**:

1. **Given** 模型已收到当前可用 Tool 并返回一个名称合法的调用，**When** 调用进入执行阶段，**Then** 系统使用模型决策时已经确认的 Tool 绑定，不重新计算整套可见工具集合。
2. **Given** 一个 Tool Call 已被当前模型轮次接受，**When** 管理员随后修改该 Tool 的普通 assignment、enabled 或 readiness 状态，**Then** 当前调用不因该普通可见性变化被拒绝，变化从下一次模型轮次生效。
3. **Given** 当前 actor 已失去目标资源权限、凭证已撤销、精确 Tool 绑定已删除，或用户取消 Run，**When** 调用准备产生副作用，**Then** 系统按当前安全状态拒绝或取消执行，并给出明确结果，而不是继续执行或重新计算 Workset。
4. **Given** Run 从 checkpoint 恢复，**When** 继续执行尚未完成的调用，**Then** 系统使用与原模型决策一致的调用绑定，不因恢复到另一 Worker 而改变 Tool 目标。

---

### User Story 2 - Tool 失败能够驱动模型修正 (Priority: P1)

作为用户，我希望参数错误、明确业务拒绝或 Tool 绑定失效能够返回给模型，让模型修改参数、改选 Tool 或向我提问，而不是直接终止整个 Run。

**Why this priority**: 当前部分失败只保留普通文本，部分执行前失败直接终止 Run。没有统一、可操作的失败反馈，就无法建立可靠的 Agent 自主修复循环。

**Independent Test**: 让模型提交一个带有效调用身份但参数不合法的 Tool Call；系统返回一个安全、结构化、与原调用关联的失败；模型修正参数后再次调用并成功完成任务。

**Acceptance Scenarios**:

1. **Given** Tool Call 具有有效身份但参数不符合 Tool 要求，**When** 系统在 Handler 前发现错误，**Then** 模型恰好收到一个与原调用关联的失败结果，包含稳定错误码、可操作摘要和建议动作。
2. **Given** Tool 或外部服务明确拒绝请求且确认未产生不确定副作用，**When** 系统处理结果，**Then** 模型可以看到经过清洗的拒绝原因并决定修正或改选 Tool。
3. **Given** 失败信息包含密钥、完整敏感参数、原始异常或大段 Provider 响应，**When** 生成模型反馈，**Then** 敏感内容被删除或脱敏，只保留有界、可操作的信息。
4. **Given** 外部写操作可能已经发生但结果无法确认，**When** 系统处理该结果，**Then** Run 进入等待协调状态，模型和 Runtime 都不得将其当作普通可重试失败。
5. **Given** Tool Call 身份缺失、在同一模型响应中重复，或消息交换关系不可能成立，**When** 系统校验调用，**Then** 系统不得伪造调用身份或执行 Handler，并以协议错误结束该路径。

---

### User Story 3 - 多轮 Tool Call 身份不会碰撞 (Priority: P1)

作为连续多轮使用 Tool 的用户，我希望不同模型轮次即使收到 Provider 重复使用的局部 Call ID，也不会复用错误的执行记录、覆盖结果或中断 Run。

**Why this priority**: 已确认 Gemini 会在每次响应中从 `call_1` 开始编号，而当前耐久执行记录把该值当作 Run 内全局身份。这是一条确定性的多轮失败路径。

**Independent Test**: 在同一个 Run 中连续两个模型轮次分别调用不同 Tool，但 Provider 都返回 `call_1`；两个调用独立执行、独立记录，并分别与正确的 Tool Result 配对。

**Acceptance Scenarios**:

1. **Given** 同一 Run 的两个不同 Assistant Turn 都包含 Provider-local `call_1`，**When** 系统执行它们，**Then** 两次调用拥有不同的 Clawith 调用实例和执行记录。
2. **Given** 同一个 Assistant Tool Call 因 checkpoint replay 再次进入执行，**When** Runtime 恢复，**Then** 它复用原执行记录，不重复产生副作用。
3. **Given** 同一 Assistant Turn 中存在重复 Call ID，**When** 系统准备执行，**Then** 在任何 Receipt 或副作用产生前拒绝整个非法交换。
4. **Given** 多轮历史包含相同 Provider-local Call ID，**When** 生成 Tool Result、Activity、Chat、A2A correlation 或下一次 Provider 请求，**Then** 每个结果仍与正确调用实例关联。

---

### User Story 4 - 修复循环有独立且可理解的预算 (Priority: P2)

作为用户，我希望 Agent 可以多次修正真正可修复的 Tool 错误，但在持续重复同一错误或围绕同一个 Tool 打转时及时暂停，并允许我纠正后重新开始计数。

**Why this priority**: 当前全局模型轮次、Provider retry、Tool replay、Verifier repair 和模型修复容易被混为一种“重试”。独立预算可以兼顾自主完成率、成本和安全。

**Independent Test**: 分别制造连续相同错误、同 Tool 不同错误、成功后再次失败、用户纠正后恢复，以及不应计数的 pending/permission/unknown-write 事件，验证每种计数和重置边界。

**Acceptance Scenarios**:

1. **Given** 同一稳定错误已经连续作为模型可见失败出现 9 次，**When** 第 10 次相同失败被记录，**Then** 系统保存该失败并暂停，不能开始第 11 次模型调用。
2. **Given** 同一个 Tool 在当前 episode 中出现 9 次可计数失败，错误指纹可以变化，**When** 第 10 次失败被记录，**Then** 系统暂停，不能开始下一次模型调用。
3. **Given** 失败指纹变化但 Tool 相同，**When** 记录新失败，**Then** 连续相同错误计数重新开始，但同 Tool episode 总数保留。
4. **Given** 被跟踪 Tool 成功、新 Run 开始，或用户明确纠正后恢复，**When** 后续再发生失败，**Then** 按对应规则开启新的 repair episode。
5. **Given** 事件属于 Provider transport retry、安全内部 replay、permission/confirmation wait、async pending、cancel 或 unknown external write，**When** 系统处理事件，**Then** 不增加模型修复计数。
6. **Given** 全局 Run 模型轮次已经达到上限，**When** 本地 Tool repair budget 尚未耗尽，**Then** 全局上限仍独立生效并展示不同的停止原因。
7. **Given** 普通 Tool 或 `write_file` 的 arguments JSON 无效或截断，**When** Runtime 请求模型修复，**Then** 两类 Tool 都分别最多提供 10 次重写机会；safe-read Runtime replay 最多执行同一调用 10 次。本轮只统一上限数值，不重构这些独立计数器。

---

### User Story 5 - 长时间 Tool 执行可控且可恢复 (Priority: P2)

作为用户，我希望网络、邮箱、云桌面和代码执行不会无限等待；取消 Run 能尽可能停止正在进行的操作；Worker lease 变化也不会被误认为 Handler 已超时或已取消。

**Why this priority**: 执行时限、用户取消和 Receipt lease 是三种不同机制。混用它们会造成无法终止的操作、错误重试或执行完成后无法结算。

**Independent Test**: 对选定的网络读取、邮箱读取、云桌面读取和长时间代码执行分别制造超时、取消、lease renewal 和 lease loss，验证底层操作、结果分类和副作用安全。

**Acceptance Scenarios**:

1. **Given** 一个外部读取操作超过该操作允许的最长等待，**When** deadline 到达，**Then** Agent loop 停止等待，并在底层能力支持时终止对应网络、进程或 SDK 操作。
2. **Given** 用户取消正在执行的 Run，**When** Handler 或 backend 支持取消，**Then** 取消信号传递到底层操作，并停止继续续租。
3. **Given** 一个合法 Handler 的运行时间超过默认 Receipt lease，**When** 当前 Worker 仍然健康且拥有执行权，**Then** lease 被续期，其他 Worker 不能并发接管同一执行。
4. **Given** Handler 在可能产生外部写之后发生 deadline、取消或连接中断，**When** 无法证明最终结果，**Then** 状态为 unknown/reconcile，系统不得自动重放。
5. **Given** Tool 有显式时限、配置默认时限和最大时限，**When** 用户省略或提供时限，**Then** 系统按“显式值优先、否则配置默认值、最后受最大值限制”的规则执行。

---

### User Story 6 - 旧 Run 可兼容，长期 Tool 注册可渐进迁移 (Priority: P3)

作为平台维护者，我希望升级后仍能恢复受支持的旧 checkpoint，同时新的 Tool 定义、Handler、授权和恢复能力逐步收敛到同一注册来源，避免再次发生能力发布遗漏。

**Why this priority**: 直接删除旧路径会影响运行中的 Run；一次性迁移全部 Tool 又风险过高。需要可观测、可删除的兼容层和分批迁移边界。

**Independent Test**: 使用没有新 Tool context 的历史 checkpoint 恢复执行，并验证兼容路径、日志、结果语义和清理条件；同时验证新注册项缺少 Schema、Handler 或安全声明时无法对模型开放。

**Acceptance Scenarios**:

1. **Given** 旧 checkpoint 只有 pending Tool Calls，**When** 新版本恢复它，**Then** 使用明确标识的 legacy compatibility path，并且同一 pending batch 不为每个 Call 重建一次 Tool 环境。
2. **Given** 旧 checkpoint 中的合法 Call 已无法执行，**When** 统一失败反馈能力启用后，**Then** 模型收到一个 legacy binding unavailable 结果，而不是无原因 terminal。
3. **Given** 新 checkpoint 的 Tool context 与 pending Call 不匹配，**When** 准备执行，**Then** 在 Receipt 前按 context corruption 拒绝，不能猜测绑定。
4. **Given** 一个 Tool 注册项缺少模型定义、可执行 Handler 或必要安全属性，**When** 系统准备将其加入 Workset，**Then** 该注册项被拒绝并给出可诊断原因。
5. **Given** legacy compatibility 使用量在完整保留周期和回滚窗口内持续为零，**When** restore 测试也证明没有依赖，**Then** 兼容路径才可以被删除。

### Edge Cases

- 同一个模型响应中包含多个 Tool Call，其中前一个已成功产生副作用，后一个发生参数、授权或 binding 失败。
- 模型响应中的 Call ID 为空、重复、长度异常，或 Tool name 不在当轮可见集合。
- Model Step 完成后进程崩溃，Tool Step 在另一 Worker 上从 checkpoint 恢复。
- MCP Tool 在 Model Step 后被重命名、删除、迁移到其他 tenant、修改 endpoint，或只轮换 credential。
- 管理员关闭 Tool，但当前已接受调用仍在等待人工确认；用户随后拒绝、接受或取消。
- Safe-read 内部 retry 已耗尽，最终只应产生一次模型可见失败和一次 repair 计数。
- Tool Result 已写入 checkpoint，但节点被重新调度；结果消息和 repair counter 不能重复追加。
- 同一 Tool 在不同错误之间交替，连续相同错误计数不断重置，但同 Tool episode 最终达到 10。
- Unknown external write 在重启、重连、用户输入或模型继续推理时仍不得自动重放。
- Handler 完成时 lease 已丢失；旧 owner 不能覆盖新 owner 或绕过 fence 结算。
- 底层线程调用无法真正取消；系统必须停止等待并明确记录底层取消能力限制。
- 旧 Worker 与新 Worker 同时运行时，新的调用实例身份不能提前允许 Provider-local ID 重复。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统 MUST 在每个模型决策轮次只构建一次模型可见 Tool 集合，并用同一集合完成 Tool name 校验。
- **FR-002**: 系统 MUST 为每个已接受 Tool Call 保存其所属 Assistant Turn、Provider Call ID、Tool 名称、Tool Contract 版本以及可恢复的执行绑定。
- **FR-003**: 新格式 checkpoint 的 Tool 执行 MUST 使用已保存绑定，且 MUST NOT 通过重新计算 Agent assignment、enabled、channel 或 readiness 来决定当前 Call 是否可执行。
- **FR-004**: 普通 Tool availability 变化 MUST 从下一次模型轮次生效；当前已接受 Call 仍 MUST 接受当前 actor、tenant、目标资源、credential 和 cancel 状态检查。
- **FR-005**: 系统 MUST 为 Provider Call、Clawith 调用实例、执行记录和业务幂等分别维护不会混用的身份。
- **FR-006**: 同一 Assistant Turn 内 Provider Call ID MUST 唯一；不同 Assistant Turn MAY 使用相同 Provider-local ID，且不得产生内部碰撞。
- **FR-007**: 同一调用实例的 checkpoint replay MUST 复用原执行记录；不同调用实例 MUST NOT 复用执行结果或副作用记录。
- **FR-008**: 所有内部结果消息、Activity、Chat、异步操作和 A2A correlation MUST 使用调用实例身份关联；Provider wire output MUST 保留原 Provider Call ID。
- **FR-009**: 系统 MUST 在 Handler 前验证输入是合法对象，并符合该 Call 已接受的 Tool 参数要求。
- **FR-010**: Typed builtin、legacy adapter、MCP、A2A、group 和 AgentBay 调用 MUST 经过同一个不可绕过的授权/审批入口后才能预留执行并产生副作用。
- **FR-011**: 依赖真实资源状态、只能在 Handler 内完成的对象级授权 MUST 返回统一、可分类的 authorization result。
- **FR-012**: 对具有有效调用身份的可修复失败，系统 MUST 生成恰好一个与原 Call 关联的 Tool Result。
- **FR-013**: 模型可见失败 MUST 包含执行状态、稳定错误码、有界摘要、模型可采取的动作、副作用确定性，以及可选安全修复提示。
- **FR-014**: 模型可见失败 MUST 删除或脱敏 secrets、完整敏感参数、stack trace、未清洗异常和无界 Provider payload。
- **FR-015**: Permission/confirmation、async pending、cancel、unknown external write 和协议损坏 MUST 使用各自独立状态，不得伪装成普通可修复 Tool failure。
- **FR-016**: Unknown possible write MUST 阻止自动重放，直到通过外部查询、稳定幂等结果或明确人工处理完成协调。
- **FR-017**: 系统 MUST 在第 10 次连续相同且模型可见的可修复失败后暂停，并且 MUST NOT 启动第 11 次模型调用。
- **FR-018**: 系统 MUST 在同一个 Tool repair episode 的第 10 次可计数失败后暂停，并且 MUST NOT 启动下一次模型调用。
- **FR-019**: 不同错误指纹 MUST 只重置连续相同错误计数，不得清除同 Tool episode 总数。
- **FR-020**: 对应 Tool 成功、新 Run 或用户明确纠正后恢复 MUST 按定义重置 repair episode；无关 Tool 成功不得清除其他 Tool 的失败 episode。
- **FR-021**: Provider transport retry、安全内部 replay、permission/confirmation wait、async pending、cancel 和 unknown external write MUST NOT 增加模型修复计数。普通 Tool protocol repair、`write_file` protocol repair 和 safe-read replay 保留独立计数结构，但各自上限 MUST 统一为 10；计数结构重构不属于本轮改动。
- **FR-022**: 全局模型轮次上限 MUST 与 Tool repair budget、Provider retry、Command retry 和 Verifier repair 保持独立，并报告不同停止原因。
- **FR-023**: Verifier repair MUST 按当前问题 episode 计数；历史已结束问题不得耗尽新的 verifier episode。
- **FR-024**: 外部 I/O 和长时间操作 MUST 具有与具体操作匹配的最长等待规则；系统 MUST NOT 用单一固定秒数替代所有 Tool 的时限。
- **FR-025**: 用户取消 MUST 尽可能传播到底层进程、网络或 SDK 操作；不支持强制取消时 MUST 明确记录该限制。
- **FR-026**: 长时间执行 MUST 在仍拥有执行权时维护 Receipt ownership；lease 到期 MUST NOT 被解释为 Handler 已超时或已取消。
- **FR-027**: 发生 deadline、cancel 或连接中断后，只要外部写结果无法证明，系统 MUST 将结果标记为 unknown/reconcile。
- **FR-028**: Tool 显式时限、配置默认时限和最大时限 MUST 遵循一致且可验证的优先级。
- **FR-029**: 旧 checkpoint MUST 通过明确、可观测且有删除条件的 compatibility path 恢复；新 checkpoint MUST NOT 使用该路径。
- **FR-030**: 系统 MUST 记录 Workset/Tool Contract 版本、legacy fallback、失败处置、repair counter transition、执行时长、deadline、cancel 和 lease 事件，且不得记录原始秘密。
- **FR-031**: 长期 Tool 注册来源 MUST 能够把模型定义、Handler、副作用、retry、authorization、recovery 和执行控制能力关联到同一稳定身份。
- **FR-032**: 未完整迁移的 Tool MUST 保持隐藏；系统 MUST NOT 仅因为存在 legacy Handler 就将其暴露给模型。

### Key Entities

- **Tool Workset**: 某一模型决策轮次真正允许模型看到和选择的 Tool 集合，包含每个 Tool 的模型定义、稳定绑定引用、版本和可用性决定。
- **Step Tool Context**: 随 checkpoint 保存的本轮 Tool 上下文，关联 Assistant Turn、Workset 版本、已接受 Call、Tool Contract 和执行绑定。
- **Provider Call Identity**: Provider 在单个模型响应内提供的 Tool Call 标识，只用于协议配对。
- **Call Instance**: Clawith 对一次具体 Assistant Tool Call 建立的 Run 内稳定身份，用于跨 checkpoint、消息、Activity、异步操作和 A2A 关联。
- **Tool Execution Receipt**: 一次调用实例的耐久执行记录，保存执行状态、ownership、尝试次数、副作用分类、结果和协调信息。
- **Tool Result**: 返回给模型和 Runtime 的标准化执行结果，包含成功、失败、pending 或 unknown 状态及安全反馈。
- **Repair Episode**: 某个 Tool 或 Verifier 问题的一段连续修复过程，拥有独立计数、错误指纹和重置边界。
- **Execution Binding**: Model Step 已接受的具体 Tool 执行目标，不包含可执行代码或解密凭证，但足以在恢复时解析相同 Handler/Provider target。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 在覆盖普通 availability 变化、checkpoint restart 和跨 Worker 恢复的测试矩阵中，100% 已接受 Tool Call 使用原模型轮次绑定执行；新格式 Tool Step 的 Workset 二次解析次数为 0。
- **SC-002**: 在所有受支持 Provider 的多轮 Tool 场景中，重复的 Provider-local Call ID 产生 0 次执行记录、Tool Result、Activity、Chat 或 A2A correlation 碰撞。
- **SC-003**: 同一调用实例在至少一次 checkpoint replay 后仍只产生一条有效执行记录；未知外部写的自动重放次数为 0。
- **SC-004**: 100% 带有效身份的可修复参数、binding 和明确业务失败产生恰好一个模型可见 Tool Result；敏感信息泄漏测试通过率为 100%。
- **SC-005**: 第 10 次连续相同错误和第 10 次同 Tool episode 失败均在规定边界暂停；普通 Tool JSON repair、`write_file` JSON repair 和 safe-read replay 的独立上限均为 10；所有 off-by-one、reset 和 exclusion 测试通过率为 100%。
- **SC-006**: Permission、confirmation、pending、cancel、unknown write、Provider retry 和全局模型轮次上限均显示独立原因，测试中不存在跨预算误计数。
- **SC-007**: 所有列入范围的 IMAP、DNS、AgentBay read 和代码执行路径在配置的最长等待内返回结果或明确状态，不产生无限等待测试用例。
- **SC-008**: 长时间 Handler 的 lease renewal、lease loss 和 cancel 测试均不会产生并发双执行或旧 owner 越权结算。
- **SC-009**: 所有受支持旧 checkpoint 可以通过 compatibility fixture 恢复；新 checkpoint 使用 legacy fallback 的次数为 0。
- **SC-010**: Tool Runtime 相关回归测试、静态检查和涉及的前端构建全部通过，且原有 unknown-write、Receipt replay 和 Provider Receipt 安全断言没有被弱化。

## Assumptions

- 现有耐久 Tool Receipt、safe-read bounded retry、pending 和 unknown/reconcile 机制继续作为执行安全基础，不在本功能中删除。
- 普通 assignment、enabled 和 readiness 是模型侧可用性，不被当作已接受 Call 的紧急撤销信号。
- 立即停止当前调用依赖耐久 Run cancel，或撤销底层 actor、资源或 credential 权限；通用 Tool hard-revoke 数据模型不属于本功能。
- 完整 Provider Schema capability matrix、默认 Tool 集合收窄、通用 Tool Search 和通用并行执行不属于本功能。
- 未迁移的 AgentBay Action 继续保持隐藏，后续按 Tool family 分批迁移。
- 旧 checkpoint 兼容路径只在有观测证据证明不再使用后删除。
- 用户已经确定所有 Tool 相关 repair/retry 上限统一为 10，并保留独立的计数结构与全局 Run 模型轮次上限；计数结构后续统一重构。
