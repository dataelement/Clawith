# Agent 通讯录 Phase 1.1 - 权限拆分设计

## 背景

Agent 通讯录 Phase 1 的目标，是把旧的手动关系配置模型，替换成一套确定性的通讯录可见性规则。第一步不是先做工具，也不是先改发送链路，而是先把当前混在一起的三个概念拆开：

- 哪些人类用户能使用某个 Agent
- 哪些人类用户能管理某个 Agent
- 一个 Agent 在通讯录里能看到并联系哪些人或其它 Agent

本文是 Phase 1.1 的实现约定，语义以 `docs/organization-roster-business-prd-v2.md` 的第 8 节和第 9 节为准。`docs/company-directory-prd.md` 描述的是上一版最小一阶段方案，只让 `company` Agent 自动互通；它现在只能作为历史参考，不再作为兼容目标。

## 当前行为

当前后端仍然把 `access_mode` 和 `AgentPermission` 同时用作“人类用户可见 / 人类用户可用 / 人类用户可管理 / Agent 之间可联系”的组合模型。

对平台人类用户来说：

| access_mode | 当前人类用户使用 / 列表可见 | 当前人类用户管理行为 |
| --- | --- | --- |
| `company` | 同租户人类用户通常能看到和使用。 | 创建者可管理；组织管理员 / 平台管理员可管理非 private Agent；普通人类用户通常是 `use`。 |
| `custom` | 同租户人类用户默认不能使用。必须存在 `AgentPermission(scope_type="user")` 才能看到 / 使用。 | 权限行可以授予 `use` 或 `manage`；管理员也能管理非 private Agent。 |
| `private` | 除创建者外不可见。 | 实际上只有创建者可管理；管理员不能管理别人创建的 private Agent。 |

对 Agent-to-Agent 通信来说：

| 目标 access_mode | 当前默认 A2A 行为 |
| --- | --- |
| `company` | 同租户 `company` 目标可以不依赖显式 `AgentAgentRelationship` 自动联系。 |
| `custom` | 不会自动联系。旧路径里仍然依赖显式关系或旧权限派生行为。 |
| `private` | 不会自动联系。 |

核心问题是：当前 `custom` 仍然表示“定制使用可见性”。目标产品语义要求 `custom` 改成“定制人类管理权限”，而人类使用 / Agent 调用范围与 `company` 相同。

## 目标产品语义

Phase 1.1 建立以下定义：

| access_mode | 人类用户使用权限 | 人类用户管理权限 |
| --- | --- | --- |
| `company` | 同租户 active 人类用户都能使用。 | 创建者 + 组织管理员 / 平台管理员。 |
| `custom` | 同租户 active 人类用户都能使用。 | 创建者 + 组织管理员 / 平台管理员 + 被显式授予 manage 权限的人类用户。 |
| `private` | 仅创建者本人能使用。 | 仅创建者本人能管理。 |

关键规则：`custom` 不再限制哪些人类用户能使用，也不限制哪些 Agent 能调用；它只控制哪些人类用户能共同管理这个 Agent。

关于“所有数字员工”的边界：在 Agent-to-Agent 调用语义里，“所有数字员工”按 PRD 第 9 节的强隔离规则理解为“所有非私人数字员工”。`private` Agent 是真正的个人空间，不进入公司协作网络，也不能主动调用 `company/custom` Agent。

同一个创建者名下的多个 `private` Agent 仍然可以互相看到和调用。这个互通只发生在创建者个人空间内，不扩展到公司协作网络。

## 核心后端判断函数

Phase 1.1 应该新增清晰的判断函数，不再继续把所有语义塞进 `check_agent_access()`。

### async / sync 约定

权限判断分两层写：

- 纯内存规则判断使用同步函数。只依赖已经传入的 `user`、`agent`、`source_agent`、`target_agent` 等对象，不查数据库，不做网络请求。
- 需要查数据库、或作为 API / service 层统一入口的函数使用 `async def`。调用方用 `await` 调用，方便后续加入数据库查询而不阻塞服务。

不要在正常后端请求链路里用 `asyncio.run()` 把异步函数强行转同步。FastAPI 和 async SQLAlchemy 已经运行在事件循环里，强行转同步容易报错或阻塞服务。

建议结构：

```python
def can_use_agent_static(user: User | None, agent: Agent) -> bool:
    """纯规则判断，不查库，可被同步测试直接调用。"""
    ...


async def can_use_agent(db: AsyncSession, user: User | None, agent: Agent) -> bool:
    """异步入口。当前可以只包一层 static，后续需要查库时可扩展。"""
    return can_use_agent_static(user, agent)
```

### 人类用户使用权

```python
async def can_use_agent(db: AsyncSession, user: User | None, agent: Agent) -> bool:
    ...
```

规则：

- `user` 缺失、未激活、或与 Agent 不同租户时返回 `False`。
- 创建者可以使用自己创建的所有 Agent。
- `company` 和 `custom` 可以被同租户任意 active 人类用户使用。
- `private` 只能被创建者使用。
- 当前规则大多可以由 `can_use_agent_static()` 完成；保留 async 入口是为了和 service/API 调用方式一致，也方便未来接入租户状态、封禁状态等数据库判断。

### 人类用户管理权

```python
async def can_manage_agent(db: AsyncSession, user: User | None, agent: Agent) -> bool:
    ...
```

规则：

- `user` 缺失、未激活、或与 Agent 不同租户时返回 `False`。
- 创建者可以管理自己创建的所有 Agent。
- `private` 只能被创建者管理。
- 组织管理员 / 平台管理员可以管理 `company` 和 `custom`。
- `custom` 还可以被显式授权的人类用户管理：存在匹配的 `AgentPermission(scope_type="user", access_level="manage")`。
- `company` 不应把普通 user permission 行当作管理授权，除非后续迁移明确要求这样做。

### 直接迁移 access_level 语义

本阶段不做旧语义向前兼容。`"manage"`、`"use"` 和 `None` 的含义直接迁移到拆分后的新模型：

- `"manage"`：人类用户拥有管理权。
- `"use"`：人类用户没有管理权，但拥有使用权。
- `None`：人类用户既不能管理，也不能使用。

`get_agent_access_level_for_user_id()` 可以作为过渡期函数名保留，但语义必须直接改成新模型，不再保留旧的 `custom` 使用白名单语义：

```python
async def get_agent_access_level_for_user_id(db, user_id, agent) -> str | None:
    if await can_manage_agent(...):
        return "manage"
    if await can_use_agent(...):
        return "use"
    return None
```

如果某个调用点依赖旧的 `custom` 使用白名单行为，应在本阶段同步改掉，而不是在 helper 里保留旧行为分支。

### `company_access_level` 处理

`company_access_level` 是旧权限模型里的公司范围默认访问级别：当 Agent 是 `company` 时，它曾表示同租户人类用户拿到的是 `"use"` 还是 `"manage"`。

新模型不再支持“全公司人类用户都能管理某个 Agent”这个隐式能力。Phase 1.1 的处理原则：

- 新权限判断不再读取 `company_access_level`。
- `company/custom` 的人类使用权统一按同租户 active 用户可使用计算。
- `company` 的管理权只来自创建者和组织管理员 / 平台管理员。
- `custom` 的管理权来自创建者、组织管理员 / 平台管理员、以及显式 `AgentPermission(scope_type="user", access_level="manage")`。
- 历史上 `company_access_level="manage"` 的 Agent，不自动把全公司人类用户升级为管理者。
- 字段本身 Phase 1.1 暂不删除，保留在数据库和 schema 中，等后续 cleanup 再决定删除或降级为旧数据展示字段。

## 通讯录可见性判断

通讯录可见性是 Agent 对目标对象的可见性，不依赖当前和这个 Agent 对话的人是谁。它必须是静态规则，只由 source Agent 自己的 `access_mode` 和目标对象决定。

在 Agent 通讯录语义里，**可见性等于权限层面的可调用性**：如果一个目标对象对 source Agent 可见，说明 source Agent 有权限选择它并发起协作；如果不可见，就不能被推荐、不能被选择、也不能被调用。

`can_contact` 只表示运行时状态是否允许“现在联系”。因此：

- `visible=true, can_contact=true`：有权限看到，也可以当前联系。
- `visible=true, can_contact=false`：有权限看到，但目标暂时不可联系，例如 stopped、error、expired、inactive。
- `visible=false`：没有权限看到，也不能通过 `include_uncontactable` 泄漏出来。

### Agent 目标可见性

```python
def evaluate_roster_agent_visibility(source_agent: Agent, target_agent: Agent) -> dict:
    ...
```

返回结构：

```json
{
  "visible": true,
  "can_contact": true,
  "unavailable_reason": null
}
```

规则：

- source Agent 自己永远不返回为可见目标。
- 跨租户目标不可见。
- 如果 source 是 `company` 或 `custom`：
  - 可见目标是同租户的 `company` 和 `custom` Agent。
  - `private` 目标对 `company/custom` source 不可见，即便它和 source 是同一个创建者。
- 如果 source 是 `private`：
  - 可见目标是同创建者的其它 `private` Agent。
  - 同创建者的多个 `private` Agent 可以互相调用。
  - `company` 和 `custom` 目标不可见。
  - 其它创建者的 `private` Agent 不可见。
  - 这表示 private Agent 不能调用任何公司协作网络里的 Agent，即便目标是同创建者创建的 `company/custom` Agent。
- 如果目标 Agent stopped、error 或 expired：
  - 只要它原本满足可见规则，`visible` 仍然是 `true`。
  - `can_contact` 变成 `false`。
  - `unavailable_reason` 写明原因，例如 `target_status_stopped`、`target_status_error`、`target_expired`。

这个区分用于后续 `query_roster(include_uncontactable=true)`：可以展示“本来可见但暂时不可联系”的对象，但不能泄漏 private 或跨租户对象。

### 人类目标可见性

```python
def evaluate_roster_human_visibility(source_agent: Agent, member: OrgMember) -> dict:
    ...
```

规则：

- 跨租户人类成员不可见。
- 如果 source 是 `company` 或 `custom`：
  - 同租户人类成员可见。
  - inactive 成员在默认查询中不可联系；当 `include_uncontactable=true` 时可以返回为 `visible=true, can_contact=false`。
- 如果 source 是 `private`：
  - 只看到创建者本人对应的人类身份。
  - 不能看到公司其它同事。

实现细节：第一版优先用 `OrgMember.user_id == source_agent.creator_id` 识别创建者本人。渠道身份、飞书身份等可以等身份映射更稳定后再扩展。

第一版接受一个简单边界：如果创建者没有对应的 `OrgMember` 映射，`query_roster` 可以不返回创建者这个 human 条目，只返回同创建者的 private Agent。不要为了补齐这个场景在 Phase 1.1 引入复杂身份匹配。

## 需要直接迁移的现有调用点

Phase 1.1 新增 helper 和测试后，相关调用点应直接迁移到新语义，不保留旧权限分支。

应迁移：

- `build_visible_agents_query()`：最终应表示“人类用户可使用 / 可列表访问”的 Agent，所以 `custom` 必须对同租户 active 人类用户可见。
- `check_agent_access()`：应先用 `can_manage_agent()` 返回 `"manage"`，再用 `can_use_agent()` 返回 `"use"`。
- `user_can_manage_agent_id()`：应直接调用 `can_manage_agent()`。
- `get_agent_accessible_user_ids()`：`company` 和 `custom` 都返回同租户 active 人类用户；`private` 只返回创建者。
- A2A 和未来的 `query_roster`：应使用通讯录可见性 helper，不再使用 `AgentAgentRelationship` 作为新链路准入条件。

Phase 1.1 不物理删除旧关系表，但新链路不再读取旧关系表作为准入条件。旧表只作为历史数据保留，等待后续清理。

Phase 1.1 也不隐藏旧 Relationships tab 或旧关系 API。先完成后端权限拆分；等 Phase 1 后续的 roster 工具和 A2A 新链路稳定后，再统一处理旧 UI/API 下线。

`AgentPermission(scope_type="company")` 暂不在 Phase 1.1 中迁移或删除。新权限判断不读取它；创建 / 更新接口是否停止写入新的 company scope permission，放到迁移阶段和 API 调整阶段一起处理。

## 最小测试矩阵

Phase 1.1 至少覆盖以下测试：

| 场景 | 预期 |
| --- | --- |
| 同租户 active 人类用户使用 `company` Agent | 允许 |
| 同租户 active 人类用户在没有 permission 行时使用 `custom` Agent | 允许 |
| 同租户 active 人类用户使用别人创建的 `private` Agent | 拒绝 |
| 创建者使用并管理自己的 `private` Agent | 允许 |
| 组织管理员管理 `company` Agent | 允许 |
| 组织管理员管理 `custom` Agent | 允许 |
| 组织管理员管理别人创建的 `private` Agent | 拒绝 |
| `custom` 的显式 manage permission 授予管理权 | 允许 |
| `custom` 的 use-only permission 不授予管理权 | 管理拒绝；使用因同租户规则允许 |
| `company` source 看到 `company` / `custom` 目标 Agent | 可见且可联系 |
| `company` source 看不到 `private` 目标 | 不可见 |
| `custom` source 看到 `company` / `custom` 目标 Agent | 可见且可联系 |
| `private` source 看到同创建者 private 目标 | 可见且可联系 |
| 同创建者的多个 `private` Agent 互相调用 | 允许 |
| `private` source 看不到 `company` / `custom` 目标 | 不可见 |
| 任意 source 看不到跨租户目标 | 不可见 |
| 可见但 expired / stopped 的目标 | 可见但不可联系 |

## 非目标

Phase 1.1 不实现以下内容：

- `query_roster`
- `send_message_to_agent(target_agent_id=...)`
- system prompt 移除完整 Relationships 列表
- 删除关系表
- 通讯录 UI
- 部门管理
- 第三方组织架构映射

这些属于 Phase 1 后续子阶段。
