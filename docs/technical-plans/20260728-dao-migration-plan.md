# DAO 层改造迁移计划

> 状态：进行中（基础设施 + auth 域已完成，其余业务待迁移）
> 起始提交：`60ffcb0` refactor(db): introduce ContextVar DAO layer (#678)

## 一、现状

**已完成的基础设施**（`60ffcb0` 引入，可作为标准范式）

- `app/dao/base.py` — `BaseDAO`，基于 `ContextVar` 的 `session()` 上下文管理，内置 CRUD
- `app/database.py` — `_session_ctx`、`transaction()` 事务边界工具、`get_db()` 依赖
- 8 个 DAO 单例：`user / identity / identity_provider / invitation_code / org_member / participant / system_setting / tenant`

**完全改造完成的业务**

- `auth.py`（0 处 `get_db` 残留）
- 相关 service：registration / password_reset / platform / system_email / email_service

**未完成的工作量（量化）**

| 层 | 指标 | 数量 |
|---|---|---|
| API 层 | 残留 `Depends(get_db)` | 231 处，分布在 ~38 个路由文件 |
| API 层 | 混合状态（部分改造） | `agents.py` 16 处残留 |
| Service 层 | 直接 `async_session`/`get_db` | 29 个文件 |
| DAO 单例 | 已建 / 模型总数 | 8 / ~30 个模型 |

---

## 二、目标与原则

1. **数据库访问收敛到 DAO**：API / Service 不再直接 `Depends(get_db)` 或 `async_session()`，只调用 DAO 方法或 `transaction()`。
2. **事务按需、不默认**：`transaction()` 仅在「多步写需要原子性」时使用；单条读 / 单条写走 DAO 即可（见决策点 1）。
3. **多租户隔离不破**：每个自定义查询方法必须过滤 `tenant_id`（见 `.agents/rules/design_and_dev.md`）。
4. **风格统一**：每个 DAO 一个 `XxxDAO(BaseDAO[Model])` 类 + 模块级单例 `xxx_dao`，在 `app/dao/__init__.py` 汇总导出。
5. **可增量、可回滚**：一次只动一组相关模型，每个 PR 自洽、可独立合并、有测试。

---

## 三、迁移标准步骤（每个模型/模块套用）

1. 新建 `app/dao/xxx_dao.py`，继承 `BaseDAO[Model]`，把该路由/service 里所有原生 SQL 查询搬成具名方法。
2. 查询方法默认走 `async with self.session()`（自动复用 context session 或新建）。
3. 需要跨多个 DAO 写一致的操作，外层用 `async with transaction():` 包裹，DAO 内部 `flush()` 而非 `commit()`。
4. 在 `__init__.py` 注册单例。
5. 改造调用方：路由去掉 `db: AsyncSession = Depends(get_db)`，service 去掉 `async_session()`。
6. 补/改单元测试（mock DAO 或用现有测试 DB fixture）。
7. Ruff（line 120 / py3.11）+ `grep get_db` 清零校验。

---

## 四、关键设计决策

### 决策点 1 · Service 层（含守护任务）的事务策略 ✅ 已对齐

> Service 层（含守护任务）强制走 **DAO**；事务只在「多步写需要原子性」时用 `transaction()` 显式包裹，**按需而非默认**。

`transaction()` 对守护任务的本质作用不是"开事务"，而是"建一个 session 并注入 ContextVar"。
因为守护任务在请求外运行、`_session_ctx` 为 None，会走 `transaction()` 的最后一条分支（新建 session + commit）。
因此判断标准与请求内一致——看是否需要原子性，而不是看是否在请求外。

| 操作 | 推荐做法 |
|---|---|
| 单条读 | DAO 方法即可，DAO 内部 `self.session()` 自己建 session |
| 单条写 | DAO `create/update/delete`，内部 `flush()`，session 由 `self.session()` 退出时 commit |
| 多条写、要原子 | `async with transaction():` 框住，内部 DAO 只 `flush()`，最外层 commit 一次 |

**关键坑**：`BaseDAO.session()` 自建的 session 退出时会 commit。所以多次 DAO 调用各自 commit、没有原子性；要原子性**必须**外层 `transaction()`，此时各 DAO 复用同一 context session。

### 决策点 2 · 读操作 commit 开销（待定）

当前 `BaseDAO.session()` 对自建 session 一律 commit，读操作 commit 无副作用但略浪费。
可选：给 `BaseDAO` 加 `readonly` 路径只 flush / 不 commit。

### 决策点 3 · 跨 DAO 组合查询放哪（建议）

放进调用方 service 用 `transaction()` 编排，而不是在某个 DAO 里写跨表 join，保持 DAO 单模型职责。

---

## 五、分阶段计划（按优先级 + 耦合度排序）

> 每个 Phase = 一个或多个独立 PR。优先级依据：核心域 > 业务频次 > 渠道适配器。

### Phase 0 · 收尾已动工模块 ⭐ 最高优先级

- `agents.py`（16 处残留）：已是混合状态，风险最高。补齐 `agent_dao`（含 `agent_credential` 关联），清掉全部 `get_db`。
- **目标**：让"改造中"文件归零，消除双范式并存。

### Phase 1 · 核心域（高频 + 高耦合）

| 文件 | get_db | 待建 DAO（模型） |
|---|---|---|
| `tools.py` | 18 | `tool_dao`（Tool） |
| `enterprise.py` | 36 | `audit_dao`、`org_dao`（Org 已部分有 org_member）、`tenant_setting_dao` |
| `tenants.py` | 14 | `tenant_setting_dao`（tenant_dao 已有） |
| `chat_sessions.py` | 6 | `chat_session_dao` |
| `tasks.py` | 7 | `task_dao` |
| `users.py` | 4 | 复用 user_dao |
| `focus.py` | 4 | `focus_dao` |
| `notification.py` | 6 | `notification_dao` |
| `schedules.py` | 7 | `schedule_dao` |

### Phase 2 · 组织 / 关系 / 治理

| 文件 | get_db | 待建 DAO |
|---|---|---|
| `relationships.py` | 10 | 复用 org_member / 新建关系查询方法 |
| `organization.py` | 3 | 补 org_member_dao |
| `advanced.py` | 10 | 多模型，逐方法迁移 |
| `admin.py` | 9 | 复用 system_setting / audit |
| `activity.py` | 4 | `activity_log_dao` |
| `onboarding.py` | 5 | `onboarding_dao` |
| `agent_credentials.py` | 5 | `agent_credential_dao` |
| `agentbay_control.py` | 9 | 评估是否纯转发 |
| `pages.py` / `plaza.py` / `skills.py` / `okr.py` | 0~3 | `published_page_dao`、`plaza_dao`、`skill_dao`、`okr_dao` |

### Phase 3 · 渠道适配器（量大但模式重复，可并行）

`feishu / dingtalk / wecom / wechat / teams / slack / whatsapp / discord_bot / google_workspace / atlassian / sso` —— 这些大多只是查 `channel_config` / `participant`，模式高度雷同。

- **建议**：先沉淀 `channel_config_dao`，再做一次性批量迁移模板，渠道逐个套用。
- 含 `gateway.py`(6) / `messages.py`(3)。

### Phase 4 · Service 层下沉（29 个文件）

事务策略按决策点 1 处理——**按需 `transaction()`，不默认包事务**。按依赖深度分两批：

1. **浅依赖**（2-3 处，纯查询）：`audit_logger / activity_logger / chat_session_service / channel_user_service / token_tracker / template_seeder / feishu_ws / dingtalk_stream / timezone_utils` → 直接换 DAO 调用。
2. **深依赖 / 后台守护**（`agent_tools` 75 处、`heartbeat`、`okr_*`、`trigger_daemon`、`scheduler`、`quota_guard`、`task_executor`、`resource_discovery`、`agent_context`、`wechat_channel`、`wecom_stream`、`agent_seeder`、`agentbay_client`）→ 逐方法判断：单步写走 DAO；多步原子写用 `transaction()` 框住。

---

## 六、每个 PR 的验收清单

- [ ] 目标文件 `grep -E "Depends\(get_db\)|async_session"` 归零（守护类按决策点 1 处理，多步写处可见 `transaction()`）
- [ ] 新 DAO 方法均过滤 `tenant_id`（适用时）
- [ ] `app/dao/__init__.py` 已注册新单例
- [ ] 相关单测通过；Ruff 通过
- [ ] 无 `DetachedInstanceError`（参考 #686：session 关闭后不要再访问关系字段，必要时 `selectinload`）

---

## 七、推进节奏

- **本周**：Phase 0（agents 收尾）单独出一个 PR，跑通"收尾混合文件"的流程。
- **接下来 2-3 周**：Phase 1 按文件拆 PR（每个文件 1 PR，便于 review）。
- **并行**：Phase 3 渠道迁移可交给多人/多 agent 并行套模板。
- **最后**：Phase 4 service 下沉收尾，重点处理守护进程的上下文与原子性判断。
