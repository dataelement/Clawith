# 后端重构剩余工作表

状态：讨论与实施导航，不是产品合同批准或阶段验收记录。

核对日期：2026-09-10。实现基线：`64f83bb1`。本表按模块、入口和能力组织待办，不增加新的业务对象、职责模块或审批状态。建议顺序尚未作为实施方案批准；本次不启动 G007 功能代码。

## 权威来源与状态口径

- [owner-contracts.json](owner-contracts.json)：34 个职责模块；18 个已有获批合同和实现，16 个处于 `unreviewed`。合同批准不等于全部产品功能完成。
- [product-contracts.json](product-contracts.json)：17 个待确认产品方案，即上述 16 个模块加 Auth 的完整产品流程。Auth 最小登录基础已经实现。
- [coverage.json](coverage.json)：401 条旧接口及生命周期记录，当前全部为 `disposition_approved`。它记录旧能力处置决定，不是 401 项替代实现已验收。
- [能力覆盖矩阵](backend-capability-coverage-matrix.md)：冻结的旧功能分类和复用能力索引，不是当前代码完成情况。
- [阶段计划](goal-gates.json)：G007 是 Auth 产品流程及获批后续模块的累计 E2E 阶段；G008 管完整后端、初始数据库基线和旧参考清理；G009 管正式部署及负载等资格验收。
- [G006 实现证据](../artifacts/rewrite/G006/product-input-e2e.txt)：受控功能测试与正式性能验收分开。性能测试按用户要求暂停；混合负载驱动缺失仍保留为未完成项，不改门禁或阈值。

以下“已有”以代码和现有测试为依据，不代表真实供应商、部署或前端已验收。“待核对”不得直接判为缺失或批准删除；进入该工作包时需要检查真实调用链。“待讨论”表示需要形成产品和实施合同，不能直接按旧接口搬运。

## A. 17 个待确认产品方案

下表中的功能来自旧能力清单，是讨论范围，不是要求全部保留。模块名也不保证最终一模块对应一个页面。

| 模块 | 已有基础 / 当前状态 | 待讨论与实现范围 | 主要依赖 | 验收重点 |
| --- | --- | --- | --- | --- |
| `auth` | 最小登录、固定登录有效期和请求鉴权已实现；完整产品合同未审 | 注册、验证邮件、找回/修改密码、个人资料、外部账号绑定、切换租户 | Identity/Tenant、Credential、SSO、邀请、系统邮件 | 注册到登录闭环、凭据失效、重复提交、跨租户隔离；不让登录过期终止自主 Agent |
| `sso` | 无目标业务实现 | 身份提供方配置、登录/回调、扫码与浏览器关联、账号绑定 | Auth、Identity/Tenant、Credential | 身份关联正确、回调校验、失败不串账号；与组织同步分开 |
| `organization` | Identity/Membership 基础可复用；产品合同未审 | 部门、成员、外部通讯录同步、外部成员与平台账号关联 | Identity/Tenant、Credential、SSO 的已定边界 | 重复同步、停用成员、局部失败、租户隔离 |
| `invitation` | 产品合同未审 | 邀请用户、邀请码、有效条件、使用/失效、加入企业 | Auth、Identity/Tenant、系统邮件 | 并发使用、重复加入、过期和错误租户 |
| `onboarding` | 显式 Agent/Tool provisioning 可复用；产品合同未审 | 首次使用、企业初始化、个人助手、默认 Agent、完成条件 | Auth、邀请、Agent、模板、Workspace、Model | 重试不重复创建；缺配置不假报完成；不恢复启动隐式补数据 |
| `okr` | 产品合同未审 | 目标/KR、周期、对齐、进度、收集/跟进、成员日报、公司报告、Agent 参与方式 | 组织、Agent、Run、Trigger、通知 | 人工与 Agent 操作同一事实；采集和报告结果可追溯；不把 OKR 当 Run 状态 |
| `focus` | 产品合同未审 | 旧 Focus 记录的创建、查询、完成；确认保留、合并还是删除 | Agent、相关业务消费者 | 与 Goal/Todo/OKR 的边界先确定，不另造重复任务生命周期 |
| `notification` | 消息事实和 Channel 投递基础已有；通知产品合同未审 | 收件箱、未读、已读、广播、产生通知的事件与接收人 | Identity/Tenant、业务事件、Channel/邮件 | 消息与通知归属、重复事件、未读计数、失败隔离 |
| `published_page` | 产品合同未审 | 发布产物、页面列表、公开访问、撤回及访问范围 | Workspace/临时产物、Permission、必要 Sandbox 发布能力 | 发布源及权限、私人内容外泄防护、撤回后的访问 |
| `plaza` | 产品合同未审 | 帖子、评论、点赞、统计、可见范围 | Identity/Tenant、Agent、Permission | 作者归属、权限、分页、重复点赞 |
| `enterprise_settings` | 产品合同未审 | 企业级配置、通知栏、邮件模板、系统邮件测试 | Identity/Tenant、Credential、邮件机制 | 设置的实际消费者、Secret 不进入普通配置、测试失败可见 |
| `platform_administration` | Platform Principal 基础已有；产品合同未审 | 企业创建/管理/启停、平台设置、跨企业指标 | Identity/Tenant、Auth、Audit、Observability | 平台与租户身份隔离、明确目标租户、禁用行为 |
| `agentbay` | 旧控制入口已删除；产品合同未审 | 远程浏览器/环境控制、点击、输入、拖拽、控制权 | Sandbox、Credential、Permission、Workspace | 谁拥有环境及控制权、取消和释放、与 Agent 自动执行协调 |
| `directory` | Agent/Permission/成员服务可复用；产品合同未审 | 人与 Agent 的目录、候选查询、自定义目录 | Identity/Tenant、组织、Agent、Permission | 可见性与候选一致、分页、不恢复旧关系标签/Memory 权限体系 |
| `agent_template` | 产品合同未审 | 模板读取、创建、删除、从模板创建 Agent、配置复制范围 | Agent、Model、Workspace、Tool、Credential | 模板不复制 Secret 或他人授权；失败和重复创建处理 |
| `observability` | Run/History、用量和审计事实已有；产品合同未审 | 活动记录、执行查询、用量/统计、企业及平台指标 | 各事实 owner、Permission、Audit | 指标口径、来源、权限、分页与有界聚合；不成为执行事实来源 |
| `tenant_knowledge` | 产品合同未审 | 企业信息、知识文件、管理操作、Agent 读取、Context 来源注入 | Identity/Tenant、Permission、Agent/Context 消费接口 | 企业隔离、来源可追溯、更新后的读取；不得默认为第四种 Workspace |

## B. 其余 17 个已有基础模块的产品收口

与 A 中 Auth 合计覆盖全部 34 个 owner。以下不要求重写已验证的基础服务；重点是补产品入口、业务组合和功能映射。表中 API 待办参考当前 [应用路由接线](../app/application.py)，不能由服务方法存在推断管理界面已可用。

| 模块 | 已有基础 | 剩余工作 / 待核对 | 依赖与验收范围 |
| --- | --- | --- | --- |
| `identity_tenant` | Account/Tenant/Membership、基本管理和身份读取 | 企业创建/加入、租户信息/标识/Logo、成员角色与管理入口；与 A 中账号组织流程一起设计 | Auth、组织、邀请、平台管理；真实管理 API 和隔离测试 |
| `agent` | Agent 身份、配置、归档等 owner 服务 | 完整创建/配置/归档入口、Soul/greeting 等已有决议的配置落点、正常初始化 | Model、Workspace、Tool、Permission；用户实际创建可运行 Agent 的路径 |
| `permission` | 最小 RBAC、可见性授予和登录捕获 | Agent 可见范围管理、候选人和管理员操作入口 | Agent、目录、成员；不擅自新增实时撤权或复杂权限体系 |
| `credential` | 三种所有者、加密、轮换、读取和绑定约束 | Tenant/Agent/个人凭据管理与连接流程；验证哪些管理操作尚无产品入口 | Tool/MCP、Model、Channel、SSO；Secret 不回传前端和模型 |
| `model` | 固定 Model Policy、Provider、用量/失败归一、Context Profile | 模型管理、配置能力来源、连通性测试与默认模型管理入口 | Credential、Agent；不恢复 fallback、步骤上限或 Token 配额 |
| `audit` | 异步独立观察与持久化基础 | 管理员查询/筛选/展示及各新业务事件接入 | 管理权限、业务 owner；审计故障不决定业务结果 |
| `workspace` | 三类空间、Memory、Skill 包、文件 CAS 和访问边界 | 浏览/预览/下载产品入口、Memory/Skill 展示与受控管理；Sandbox 映射/写回另审 | Permission、Market、Sandbox；普通文件不开放未经确认的人类编辑 |
| `tool` | Definition/Grant、角色限制、发现/调用、MCP 与个人连接 | Tool 管理/授权/测试入口、具体业务 executor、安装组合、OAuth 能力核对 | Market、Credential；框架可用不等于所有外部工具已恢复 |
| `capability_market` | 共享目录、安装绑定和源可用性基础 | GitHub/ClawHub 导入问题、预览/搜索/安装管理、Agent 自主安装完整链 | Tool、Workspace、Credential；现有未提交草稿不作为完成证据 |
| `context` | 来源组装、压缩、增量和缓存相关基础 | 新企业知识、目录等产品来源接入时逐项追踪；不整体推翻已定模型 | 新来源 owner；来源、快照与实际模型输入一致 |
| `run` | 单 Runner/Loop、History/Snapshot、Child/Waiting/终态 | 新业务继续通过公共执行边界接入；正式负载验收暂停 | 不新增产品专用执行状态机；模型重试与服务重启规则保持已定 |
| `session` | API/WebSocket、消息、附件、工作控制、Goal 连续推进 | 对照旧会话管理能力核对改名/归档等处置与新入口；后续前端接入 | 不要求用户先选择内部 Task/Run；消息和完成状态分开 |
| `a2a` | 独立目标 Main、等待/接手、追加附件、临时文件返回 | 新业务工具使用该协议；协作查看入口按产品需要核对 | 不传递发送方整个 Workspace/权限；不恢复终止 Run |
| `group` | 群、成员、话题、消息/Run、实时和附件链 | 逐条对应旧群管理、公告、Workspace 浏览等能力及前端入口 | Permission、Workspace、Channel；已实现项不重复重写 |
| `trigger` | 配置、事件/定时输入、显式目的地、来源权限和完整结果读取 | 任务结果与配置前端；新业务订阅；确认旧覆盖映射 | 无目的地可查询、有目的地单向投递；不自动重放旧执行 |
| `heartbeat` | 独立配置/发生记录、Run 接线和结果读取 | 配置及结果前端、业务消费和正式负载 | 不生成 Trigger；沿用已定无人值守规则 |
| `channel` | 七类消息 Provider 的新入口、映射、投递和监听机制 | 真实供应商验证、管理前端与旧能力映射；SSO/通讯录/业务工具另归其 owner | 聊天接通不等于文档/审批/日历工具接通；不自动恢复已删除 Provider |

## C. 跨模块能力工作包

这些不是额外 owner 名额。实施前确定现有 owner 和真实消费者；只有确有独立责任时才讨论新边界。

| 工作包 | 已有 / 未完成边界 | 需要先确认 | 通过什么验收 |
| --- | --- | --- | --- |
| Sandbox 与代码执行 | 保留 `app/services/sandbox/` 机制和测试，尚无新产品执行入口 | 支持哪些环境、资源归属、凭据、文件映射/写回、终止清理；保留成熟设计，不搬旧依赖 | 实际 Tool→Sandbox→文件/结果→清理；失败与取消；不能只测保留 helper |
| 浏览器与远程人工控制 | AgentBay 也是 A 中模块；不能脱离 Sandbox 重复实现 | 浏览器环境、控制权、人机接管、可见性、会话结束后的资源处理 | 实际控制、自动执行与取消/释放边界 |
| 外部业务工具 | 通用 Tool/MCP 与 Channel 已有，具体能力不因此自动可用 | 按旧独立操作核对飞书文档/日历/审批/多维表格等、邮件、Google Workspace、Atlassian、搜索、部署、图像相关能力；分别决定原生 Tool、MCP、Skill 或明确删除 | 逐操作注册、授权、真实 executor、规范结果、错误与副作用测试；供应商实测另列 |
| 文件产物生成与转换 | G006 已有附件预览及 PDF/Office 文本提取；保留转换 helper 不等于新 Tool 已接通 | HTML/PDF/PPTX 等生成转换、产物存放/返回/发布；与 Published Page、Sandbox 的依赖 | 真实生成文件及内容/格式、权限、大小、失败/清理；不重复实现文档读取 |
| 安装与连接闭环 | Market/Tool/Workspace/Credential 的组合，不新增安装市场 owner | 管理员与 Agent 安装入口、共享/私有影响、个人账号和 OAuth；审查当前 GitHub/ClawHub/MCP 草稿 | 发现→安装/绑定→下次发现/加载→实际调用；失败不假报成功 |
| 系统邮件与外部通知 | 保留通用邮件机制；系统邮件与 Agent 邮件工具不是同一个业务入口 | 系统配置、模板、发件凭据、Auth/邀请/通知消费；Agent 个人邮箱操作独立归 Tool | 实际消费者发信、模板和凭据范围、发送失败及不确定结果 |
| 显式初始化与部署 | 有显式 provision 方法；无完整目标基线和产品 bootstrap 验收 | 新环境首个管理员/租户、模型/Agent/工具配置如何建立；不恢复启动隐式修复 | G008 新环境创建、启动、完整业务 E2E、初始化重复执行及失败 |
| 前端整体替换 | 旧页面与草稿不代表匹配新 API | React/shadcn 页面迁移、管理端与使用端边界、实时状态、文件/结果入口 | 页面与新 API 契约、浏览器流程、构建和响应性能；独立排期 |
| 正式运行资格 | 功能回归已记录；混合压测入口尚缺，性能执行已暂停 | 后续测试环境和混合场景驱动；平台部署、外部 Provider 验证 | 按既有 G008/G009 与累计门禁验收，不降低 50 Agent 目标 |

Sandbox 的当前范围见[复用决议](../../.agents/notes/proposed/architecture/2026-09-03-sandbox-reuse-candidate.md)。外部工具清单目前是能力族，不是逐操作完整验收清单；进入该包前还需从已批准旧能力证据和实际前端/外部消费者展开，不能用 401 条 HTTP/生命周期记录代替模型可调用能力盘点。

## D. 建议讨论和实施顺序

1. 账号、租户、组织、邀请、SSO：先定共享身份/加入关系，再逐模块实施，不扩展最小 RBAC。
2. Agent、可见性、Model/Credential、能力管理和 Workspace 管理入口：把已有底层串成可操作的产品配置流程；模板与 Onboarding 在依赖确定后落地。
3. Sandbox、AgentBay、外部工具、产物生成及安装闭环：按业务依赖拆包，不能因其不在 17 项中而遗漏。
4. OKR、Focus、通知、企业知识、目录、发布页、广场及企业设置：每项先确定保留范围和跨模块依赖，再实现。被前序流程需要的通知/企业设置子能力应前移，不必等待整个模块完成。
5. 平台管理、可观测性和剩余管理组合：所需最小管理能力可随前序模块落地，指标和完整产品面随后收口。
6. 完整后端与数据库基线按 G008 收口；前端单独推进；正式性能保持暂停，恢复时仍按既定门禁执行。

此顺序是依赖建议，不把 B/C 的所有工作自动塞进原 G007 许可范围。原阶段清单只列 Auth 产品与获批后续 owner；已有 owner 的新语义、Sandbox 激活和跨阶段工作，实施前需要按既有合同流程明确归属与计划。无需现在重审已定架构，也无需一次性确定所有字段。

## E. 每个工作包的进入与完成条件

进入前列出：用户/Agent 的目标、保留/删除的操作、已有实现与缺口、事实 owner、权限和来源边界、对其他模块的依赖。随后确定必需的数据结构、公共服务/API/Tool/Event、事务与外部副作用边界、错误/重试/取消和升级处理，以及可执行的验收路径。实现细节仅在影响合同或跨模块协作时提前确认。

完成时提供：获批合同、代码与 owning Note、最小提交、真实入口测试、累计回归，以及旧能力的替代/删除证据。再按现有工具推进 coverage 对应行，不能直接把整张表标为完成。此工作表是导航，不替代三个治理清单，也不引入第四套批准状态。

明确不恢复：OpenClaw/Gateway、旧 LangGraph/Checkpoint/Command/Tool Ledger、持久 Task 状态机、Experience/RAG 权威、模型 fallback、已删除的配额/审批实现和旧 API 兼容层。若后续有新的产品需求，另行讨论，不以旧文件或旧页面存在作为保留授权。

## 本次核验范围

已交叉核对 34 个 owner、17 个产品方案条目、401 条覆盖记录的当前状态、目标模块目录、应用路由、Builtin provision、保留服务家族和 G006 验收记录。当前 Market/MCP/Skill 导入草稿及前端原型未改动，未计入已提交实现。没有重跑业务测试、外部服务或性能；本表不宣称逐操作盘点已经覆盖全部历史动态消费者。
