# Clawith → SaaS 改造分析

> 版本：v0.1（待评审）
> 作用：盘点 clawith 当前代码中**不符合 SaaS 产品定位**的功能面，作为后续拆分 SaaS 改造 Epic 的输入。
> 分类原则：一个功能如果**只在「自部署 / 开源发行版」里合理**，而把它暴露给 SaaS 租户会带来安全、合规、支撑成本或商业模式上的问题，就会被列入此清单。
>
> 每一项包含：**现状** → **为何不符合 SaaS** → **建议 SaaS 形态** → **改造成本评估（S/M/L）**。
> 文末附**优先级矩阵**和**改造分期建议**。

---

## 一、LLM 接入：必须去 BYOK

### 1.1 租户级 LLM 模型池（核心问题）

- **现状**
  - `backend/app/models/llm.py::LLMModel`：字段 `tenant_id`、`api_key_encrypted`、`base_url`、`provider` → 每个租户/企业可以自建 LLM 模型条目，粘贴自己的 OpenAI / Anthropic / DeepSeek key，并可覆盖 `base_url`。
  - `backend/app/api/enterprise.py`：`POST /enterprise/llm-test`、`/enterprise/llm-models` — 企业管理员 CRUD 自己的 LLM 模型。
  - `frontend/src/pages/OpenClawSettings.tsx`：前端提供整套「添加模型 / 填 key / 选 provider / 测试连通」的 UI。
- **为何不符合 SaaS**
  - BYOK 让平台失去对成本、可用性、安全审计的控制权。
  - 用户填的 `base_url` 可以被改成攻击者自建端点 → SSRF / 数据外流风险。
  - SaaS 商业模式一般按 token / 按 seat 计费，BYOK 与计费模型冲突。
- **建议 SaaS 形态**
  - 删除前端 LLM 配置入口；`llm_models` 表变为**平台级全局池**（`tenant_id` 全部置空或移除字段），只由平台管理员通过后台（非租户 UI）维护。
  - 租户侧只能看到「模型档位」（例如 Standard / Pro / Vision），实际路由由平台按档位选模型并计量 token。
  - `api_key_encrypted` 仅存在平台密钥管理服务（KMS / Vault），不进租户数据库。
- **成本**：M（DB schema 可保留，UI 和权限收敛即可；但要配套实现"档位"抽象与计费挂钩）

### 1.2 LLM 调用计量 / 计费缺失

- **现状**
  - `backend/app/services/token_tracker.py`：记录 agent 级 `tokens_used_today / month / total`。
  - `backend/app/services/quota_guard.py`：做 `max_llm_calls_per_day`、消息配额等限制，但**与计费系统无任何挂钩**，属于「自部署自律」限额。
- **为何不符合 SaaS**
  - SaaS 必须有 tenant 级账单、seat、token 计费维度；现有数据无法生成对账单。
  - 没有超额预警 / 停机保护策略。
- **建议 SaaS 形态**
  - 新增 `tenant_usage_ledger`（按天、按模型、按功能 sku 聚合），对接订阅服务（Stripe / 内部计费）。
  - `quota_guard` 改造为「订阅档位 + 实时用量」双维判断。
- **成本**：L（涉及计费系统对接）

---

## 二、Agent 凭证：最危险的 BYOK 面

### 2.1 Agent 级账号密码 / Cookie 托管

- **现状**
  - `backend/app/models/agent_credential.py` + `backend/app/api/agent_credentials.py` + `frontend/src/components/AgentCredentials.tsx`：允许用户给每个 agent 存一组**第三方网站的账号密码 + cookies JSON**，供 agent 浏览器会话注入使用。
- **为何不符合 SaaS**
  - 平台替租户托管第三方明文账号密码 → 合规（ISO27001 / SOC2 / 个保法）极高风险。
  - 一旦平台被入侵，将导致**下游第三方账户的连锁泄露**。
  - 在我国属于"代保管他人登录凭证"，监管口径上接近金融账户。
- **建议 SaaS 形态**
  - **MVP 期：直接下线此功能**，或改为引导用户走 OAuth / 平台认证代理（Social Login bridge）。
  - 如必须保留，则走专门的机密管理服务（HSM / Vault），并对租户显式签署数据处理协议。
- **成本**：S（下线）/ L（合规化留存）

---

## 三、工具 / MCP / Sandbox：租户不应碰到基础设施

### 3.1 Sandbox 后端可选（docker / self_hosted / e2b / judge0）

- **现状**
  - `backend/app/services/sandbox/config.py`：Tool 的 sandbox 类型可以是 `subprocess / docker / e2b / judge0 / codesandbox / self_hosted / aio_sandbox`。
  - 部分类型需要租户自填 `api_key / api_url`。
- **为何不符合 SaaS**
  - 租户不应感知到「底层沙箱」。不同沙箱后端的安全边界差异巨大，自选等于把安全事故外包给租户。
  - `self_hosted` / `subprocess` 允许租户自己接本地进程 → 在多租户平台上等于放弃隔离。
- **建议 SaaS**
  - 收敛到 **1 个官方沙箱**（如 aio_sandbox 或 e2b 托管），对租户隐藏类型选择。
  - 移除 `self_hosted / subprocess / docker` 等本地选项；`tool.config` 中关于 sandbox 的字段由平台管控，不在 tenant UI 暴露。
- **成本**：M

### 3.2 MCP 服务器任意接入

- **现状**
  - `backend/app/models/tool.py::Tool.mcp_server_url` + `backend/app/api/tools.py`：允许 agent 在运行时从 Smithery / ModelScope 装任意 MCP 服务器（README 里也作为亮点宣传）。
- **为何不符合 SaaS**
  - 任意 MCP URL 意味着任意 HTTP 外联 → SSRF、数据外泄、恶意工具供应链攻击。
  - 多租户下，一个租户装的 MCP 可能被另一个租户间接利用。
- **建议 SaaS**
  - 建立**平台审核过的 MCP 市场**（白名单）；租户只能从市场启用，不能粘 URL。
  - agent 级自动装工具需要平台审批 / 沙箱预检。
- **成本**：M

### 3.3 Tool 级 API Key（搜索引擎、Judge0 等）

- **现状**：部分 builtin 工具（web_search、代码执行、邮件等）允许租户/agent 配置自己的 `api_key`。
- **建议 SaaS**：平台统一采购并通过档位下发；`tool.config` 中的 key 字段在 SaaS 版本里不向租户 UI 暴露。
- **成本**：S

---

## 四、渠道集成（Slack / Discord / Feishu / 钉钉 / 企微 / Atlassian）

### 4.1 每租户自填 Bot Token / Signing Secret

- **现状**
  - `backend/app/api/slack.py`、`discord_bot.py`、`feishu.py`、`dingtalk.py`、`wecom.py`、`atlassian.py` + `frontend/src/components/ChannelConfig.tsx`：每个 agent 可以绑定租户自行注册的 Slack App / 飞书应用 / 钉钉机器人，填 `bot_token / signing_secret / app_id / app_secret`。
- **为何不符合 SaaS**
  - 租户获得一等公民"自建应用"的心智 → 运营/支撑成本激增（「我们不用 Slack，我们用 Gmail」）。
  - 平台无法统一做授权审计，单个租户的 app 配置错误会导致"clawith 挂了"的归因污染。
  - 正确的 SaaS 形态是**平台一个官方 Slack App，租户走 OAuth install 授权**，无需看到 token。
- **建议 SaaS**
  - Slack / Discord / Feishu / 钉钉 / 企微：改为**平台 OAuth 安装流程**，后端自动存 installation；租户端只看到「连接 / 断开 / 已连接工作区」。
  - Atlassian 同理。
  - 短期保留老路径给私有化版本，SaaS 版本通过 feature flag 隐藏。
- **成本**：L（每个渠道都要重写 OAuth install 闭环）

---

## 五、SSO / 企业身份

### 5.1 租户自建 IdP

- **现状**
  - `backend/app/models/identity.py::IdentityProvider` + `backend/app/api/sso.py`：租户自己填 OIDC client_id/secret/issuer、域名映射。
- **为何不符合 SaaS**
  - 本身**不完全算错**：企业 SSO 是 SaaS 的标准付费功能。但需要放到**Enterprise 档位**，而非 free / pro 可见。
- **建议 SaaS**
  - 保留，但加订阅档位 gate；默认租户只能用 Google / 微信 / 邮箱 + 平台 OAuth。
  - 前端 `EnterpriseSettings.tsx` 加档位检查。
- **成本**：S

---

## 六、邮件 / 短信通道

### 6.1 SMTP 凭证

- **现状**
  - `backend/app/services/email_service.py`：agent 工具级邮箱（发/收邮件）允许租户填 SMTP / IMAP。
  - `backend/app/services/system_email_service.py`：系统邮件（密码重置）走 `.env` 里的平台 SMTP。
- **为何不符合 SaaS**
  - **系统邮件**是平台侧，OK，但要换成托管邮件服务（SES / Resend / 阿里云邮件推送）。
  - **Agent 工具级邮件**让用户填自己的 IMAP 密码 → 又是一处账号托管风险，同 §2.1。
- **建议 SaaS**
  - 系统邮件 → 托管；去掉 `.env` 硬编码。
  - Agent 邮件工具 → 改走 Gmail / Outlook OAuth，不再手填 SMTP 密码。
- **成本**：M

---

## 七、平台 / 基础设施暴露

### 7.1 平台管理面板暴露给租户 admin

- **现状**
  - `frontend/src/pages/PlatformDashboard.tsx`、`AdminCompanies.tsx`、`backend/app/api/admin.py`、`backend/app/services/platform_service.py`：包含跨租户的公司列表、token 用量、运行中 agent 列表等。
- **为何不符合 SaaS**
  - 这些页面**只能平台 owner 看**；现有权限模型里如果任何租户 admin 能访问（即使只是路由未做 gate），就是严重漏洞。
- **建议 SaaS**
  - 把 PlatformDashboard / AdminCompanies / InvitationCodes 拆到**独立前端应用**（例如 `/platform/*` 子域），与租户应用路由隔离。
  - 后端所有 `/api/admin/*`、`/api/platform/*` 的权限中间件改为只接受 `role=platform_admin`，并在集成测试里明确校验。
- **成本**：M

### 7.2 System Settings（JSONB 键值）对租户开放

- **现状**：`backend/app/models/system_settings.py`：平台级 key-value 表，但 API 层如果被租户 admin 直接命中，就能改全局开关。
- **建议 SaaS**：API 限定 platform_admin；租户不可见。
- **成本**：S

### 7.3 AgentBay 控制面

- **现状**：`backend/app/api/agentbay_control.py` + `AgentBayLivePanel.tsx` 暴露远程 agent 沙箱的控制入口，可能看到底层容器状态。
- **建议 SaaS**：前端隐藏技术细节（容器 id / host / raw log），只保留功能按钮（重启 / 查看结果）。
- **成本**：S

### 7.4 文件存储硬绑本地 FS

- **现状**：`backend/app/api/files.py` 把文件存在 `{AGENT_DATA_DIR}/{agent_id}/`，README 也明说挂到宿主机 `./backend/agent_data/`。
- **为何不符合 SaaS**：多副本部署 / 扩缩容时无法共享；无 per-tenant 存储配额；没有生命周期策略。
- **建议 SaaS**：引入 `StorageBackend` 抽象（local / S3 / OSS），生产用对象存储；per-tenant bucket prefix + quota。
- **成本**：M

---

## 八、注册与准入

### 8.1 "首个注册用户即平台管理员"

- **现状**：README 明示 `The first user to register automatically becomes the platform admin`（需要在代码中双重确认该逻辑在 SaaS 下是否仍生效）。
- **为何不符合 SaaS**：SaaS 平台只能有**固定的平台运营账号**，不应通过注册顺序决定身份。
- **建议 SaaS**：删除该逻辑；platform_admin 账号通过 seed 脚本 / 运维后台创建。
- **成本**：S

### 8.2 邀请码作为唯一准入

- **现状**：`invitation_code.py` + `InvitationCodes.tsx`，管理员批量生成邀请码。
- **为何不符合 SaaS**：SaaS 需要**自助注册 + 订阅档位** + 可选邀请码（用于企业 onboarding）。
- **建议 SaaS**：邀请码降为可选；新增 self-signup + 邮箱验证 + 试用期流水线。
- **成本**：M

---

## 九、广场（Plaza）跨租户问题

### 9.1 tenant_id 可选 / 跨租户可见

- **现状**：`backend/app/models/plaza.py::PlazaPost.tenant_id` 可空；`plaza.py` 创建帖子时接受 `tenant_id`。如果查询不强制过滤，会泄露跨租户内容。
- **为何不符合 SaaS**：多租户第一铁律——任何 list / feed 查询都必须强 tenant_id。
- **建议 SaaS**
  - `tenant_id` 改为 NOT NULL；
  - 所有 Plaza 的 list / search / mention / comment 查询加 `tenant_id = current_tenant` 硬过滤；
  - 出一个**平台级 Plaza**（只读 broadcast）用来放官方公告，单独的表 / 单独的 role。
- **成本**：S（代码）+ 必须加回归用例

---

## 十、开发者 / 自部署心智残留

这些不是 SaaS 安全问题，但**不应出现在 SaaS 用户可见的前端/文档里**：

| 项 | 位置 | 处理 |
|----|------|------|
| README 的 docker-compose / setup.sh / helm 章节 | `README.md`、`setup.sh`、`restart.sh`、`helm/` | SaaS 版不发；保留在 OSS 仓库 |
| `update_schema.py` / `remove_old_tool.py` 等运维脚本 | `backend/` | 不随 SaaS 镜像发布，仅在运维环境可见 |
| `.env.example` 暴露 SMTP / DB 字段 | repo 根 | SaaS 部署走 Secrets Manager |
| AGENTS.md / ARCHITECTURE_SPEC_EN.md 等技术白皮书 | repo 根 | 评估是否作为营销资料；不在 SaaS 产品内暴露 |

---

## 十一、优先级矩阵

| 优先级 | 理由 | 项目 |
|------|------|------|
| **P0（上线必须）** | 安全 / 合规 / 商业模式硬伤 | §1.1 LLM BYOK 下线、§2.1 凭证托管下线、§3.1 Sandbox 收敛、§3.2 MCP 白名单、§7.1 平台面板隔离、§8.1 首用户管理员、§9.1 Plaza 跨租户 |
| **P1（上线首版）** | 影响体验但不阻断 | §4 渠道 OAuth 化（至少 Slack + 飞书先行）、§1.2 计费挂钩 MVP、§7.4 对象存储、§6 邮件托管化 |
| **P2（后续迭代）** | 企业版 / 长尾 | §5 SSO 档位、§3.3 工具 key 收敛、§7.2 system_settings 加固、§7.3 AgentBay 面板脱敏、§8.2 邀请码降级 |

---

## 十二、建议的改造分期

- **Phase 0 — 清单冻结与评审**（本文档 + 1 次评审会）
- **Phase 1 — P0 安全收敛**（2 周内必须完成才能开放 SaaS 注册）
  - 下线 LLM BYOK UI / API
  - 下线 AgentCredential BYOK
  - Sandbox 类型收敛到 1 个
  - MCP 白名单机制
  - 平台面板路由隔离
  - Plaza `tenant_id` 强制
- **Phase 2 — 计费 & 存储基础设施**
  - `tenant_usage_ledger` + 订阅档位
  - 对象存储接入
  - 邮件托管化
- **Phase 3 — 渠道 OAuth 化**
  - Slack / 飞书 / 钉钉 / 企微 官方 App，逐个迁移
- **Phase 4 — 企业版差异化**
  - SSO 档位 gate
  - 邀请码体系重构
  - 剩余长尾项

---

## 评审问题（请同事回答）

1. SaaS 版是否**完全**禁止 BYOK LLM？还是在 Enterprise 档位保留？
2. Agent 凭证托管：**直接下线**还是走合规化留存路线？
3. 沙箱我们只保留哪一个作为 SaaS 默认（aio_sandbox / e2b / 自建）？
4. MCP 白名单由谁维护？平台运营 or 自动爬 Smithery + 审核？
5. 计费系统用 Stripe 还是内部对接（影响 Phase 2 时间线）？
6. 自部署开源版本是否仍然保留所有 BYOK 功能？如果是，SaaS / OSS 两个发行分支的维护成本怎么规划？

---

**状态**：🟡 待评审 — 请同事对 §1-§9 的定性和 §11 的优先级给出 ACK / NACK / 补充，之后再细化 Phase 1 的工单拆分。
