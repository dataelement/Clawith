# 默认 Agent 一次性初始化技术方案

> 状态：待实现
>
> 范围：Morty、Meeseeks 的首次创建、升级兼容和存储自愈

## 1. 业务语义

Morty 和 Meeseeks 是租户首次完成平台初始化时创建的默认 Agent。

初始化成功后，平台必须尊重用户对这两个 Agent 的生命周期操作：

- 用户删除后，后续启动、重启和升级不得重新创建。
- 用户重命名后，不得因为默认名称消失而创建同名副本。
- 用户仅停止 Agent 时，不得创建副本。
- 未删除的默认 Agent 如果 workspace 或 Skills 存储损坏，启动时仍可执行非覆盖式修复。

因此，“是否创建默认 Agent”与“是否修复默认 Agent 存储”必须是两项独立判断。

## 2. 当前实现与问题

### 2.1 当前调用链

`seed_default_agents()` 在两个入口运行：

- 后端启动流程：`backend/app/main.py`
- 首个平台注册用户创建完成后：`backend/app/api/auth.py`

重复调用本身是允许的，前提是 seeder 具备可靠的一次性语义。

### 2.2 当前创建判据

当前 seeder 按以下条件查找已有默认 Agent：

```python
Agent.tenant_id == admin.tenant_id
Agent.name.in_(["Morty", "Meeseeks"])
Agent.agent_type == "native"
Agent.status != "stopped"
```

如果对应名称不在查询结果中，就创建新的 Agent。

这个判据把可变运行状态当成了初始化事实：

- 删除接口会保留 Agent 行，同时设置 `deleted_at` 和 `status="stopped"`。
- 停止接口也会设置 `status="stopped"`。
- 重命名会改变 `name`。

因此删除、停止和重命名都可能被错误解释为“从未初始化”。

### 2.3 现有 seed marker

存储中已有 `_bootstrap/.seeded`，但默认 Agent seeder 当前只写入、不读取该标记。该文件不能作为新的唯一事实源：部署可能更换或丢失存储，而数据库仍然保留。

### 2.4 必须保留的存储自愈

现有 `_repair_default_agent_storage()` 会为仍存在的默认 Agent 修复缺失的根目录和 Skills 目录，并避免覆盖用户文件。这个能力必须保留，不能恢复成“发现 seed marker 后整段 seeder 直接返回”。

## 3. 技术目标

1. 使用租户级、持久、与名称和运行状态无关的初始化事实。
2. 默认 Agent 每个租户最多自动初始化一次。
3. 删除、停止、重命名均不触发重新创建。
4. 对未删除的默认 Agent 保留存储自愈。
5. 兼容没有数据库初始化标记的现有部署。
6. 多实例同时启动时不得重复创建。
7. 不增加依赖，优先复用现有表和数据库锁模式。

## 4. 数据事实源

### 4.1 新的规范事实

复用现有 `tenant_settings` 表，不新增表和 Alembic migration。

建议设置项：

```text
key = "bootstrap:default_agents:v1"
```

建议 value：

```json
{
  "initialized": true,
  "agents": {
    "morty": "<uuid-or-null>",
    "meeseeks": "<uuid-or-null>"
  },
  "source": "created|legacy_marker|database_history"
}
```

语义：

- 设置项存在即表示该租户已经完成过默认 Agent 初始化；`initialized=true` 用于校验和诊断。即使 value 损坏，也必须保守地停止自动创建并记录告警。
- Agent ID 是稳定身份，用于后续存储修复；不再通过名称反查身份。
- ID 对应 Agent 已删除或物理不存在时，也不得重新创建。
- `source` 仅用于诊断和升级审计，不参与业务判断。

### 4.2 删除事实

`Agent.deleted_at` 是 Agent 是否被用户逻辑删除的事实源。

- `deleted_at is None`：Agent 仍存在，可以检查和修复存储。
- `deleted_at is not None`：Agent 已删除，跳过存储修复，也不得补建。
- `status` 只描述运行状态，不参与初始化或删除判断。

### 4.3 legacy marker 的角色

`_bootstrap/.seeded` 只用于现有部署的兼容识别和运维诊断，不再作为长期唯一事实源。

后续如仍需写入 legacy marker，必须使用追加/合并方式，不能覆盖 `okr_agent` 等其他 seed 信息。

## 5. 核心流程

### 5.1 并发边界

进入租户默认 Agent 初始化流程后，先获取租户级 PostgreSQL transaction advisory lock。锁键建议包含租户 ID和固定命名空间：

```text
default-agent-bootstrap:<tenant_id>
```

锁内重新读取 `tenant_settings`，避免多个后端实例同时判断“未初始化”并重复创建。

### 5.2 已有数据库标记

如果 `bootstrap:default_agents:v1` 已存在：

1. 不执行任何默认 Agent 创建。
2. 按设置中保存的 Agent ID 查询数据库，查询必须包含 stopped 和逻辑删除行。
3. 对 `deleted_at is None` 的 Agent 调用 `_repair_default_agent_storage()`。
4. 对已删除或不存在的 Agent 直接跳过。

### 5.3 新租户首次初始化

如果数据库标记不存在，并且兼容识别没有发现历史初始化事实：

1. 创建 Morty 和 Meeseeks。
2. 创建 Participant、权限、默认工具和相互关系。
3. 初始化 workspace 和 Skills。
4. 在同一数据库事务中写入 `bootstrap:default_agents:v1`，保存两个 Agent ID。
5. 提交事务。
6. 数据库提交成功后，以追加方式更新 legacy marker；marker 写入失败只记录告警，不回滚已经成立的数据库事实。

数据库中的 Agent 和初始化设置必须一起提交，避免出现“Agent 已创建但初始化设置缺失”的中间状态。

## 6. 现有部署兼容

### 6.1 是否必须回填

如果采用 `tenant_settings` 作为新的规范事实，现有租户必须建立这个事实，否则“数据库标记不存在”仍可能被错误理解为全新租户。

但不需要：

- 新增 Alembic 数据迁移；
- 单独执行离线回填脚本；
- 人工逐租户处理。

采用 seeder 首次运行时的懒回填即可。也就是说，兼容回填是逻辑上必须的，但不需要独立发布步骤。

### 6.2 懒回填顺序

数据库标记不存在时，按以下顺序识别历史初始化：

1. 读取 legacy marker 中的 `morty`、`meeseeks` ID。
2. 校验 marker 指向的 Agent 是否属于当前租户；查询包含已删除和 stopped 行。
3. 如果 marker 无法使用，则查询当前租户所有历史 Agent 行，包括已删除和 stopped 行，查找曾存在的 canonical 名称 Morty/Meeseeks。
4. 发现任一可信历史证据，就写入 `bootstrap:default_agents:v1`，`source` 分别记录为 `legacy_marker` 或 `database_history`，不创建缺失 Agent。
5. 只有完全没有数据库标记、有效 legacy marker 和历史 Agent 证据时，才执行首次创建。

这里采用保守策略：有历史证据时宁可不自动创建，也不能覆盖用户删除意图。

### 6.3 无法完全恢复的历史状态

如果现有部署同时满足以下条件：

- legacy marker 已丢失；
- 默认 Agent 已被重命名；
- 数据库中没有可识别的 canonical 名称历史；
- 数据库初始化标记尚未建立；

系统无法只根据现有数据可靠证明该 Agent 曾由默认 seeder 创建。不得通过角色描述、Bio 或 workspace 内容做模糊猜测。

该极端状态只能通过运维确认后补写租户设置。修复上线后，新数据库标记会消除后续同类歧义。

### 6.4 已被旧逻辑重新创建的 Agent

升级兼容过程不自动删除当前活跃 Agent。系统无法可靠判断用户是否已经开始使用旧逻辑重新创建出的对象。

用户可在修复上线后再次删除该 Agent；数据库初始化事实已经建立，后续不会再次创建。

## 7. 代码改动范围

### 7.1 `backend/app/services/agent_seeder.py`

- 引入 `TenantSetting`。
- 增加默认 Agent 设置 key 和 value 解析函数。
- 增加 legacy marker 解析和懒回填函数。
- 增加租户级 transaction advisory lock。
- 将 `seed_default_agents()` 拆为：
  - 初始化事实解析；
  - 首次创建；
  - 现存 Agent 存储修复。
- 移除以 `name + status != stopped` 作为创建判据的逻辑。
- 保留 `_repair_default_agent_storage()` 的非覆盖语义。
- legacy marker 改为追加/合并写入，避免覆盖其他 seeder 条目。

### 7.2 `backend/tests/test_agent_seeder_storage_repair.py`

扩展现有测试覆盖初始化状态、兼容回填和删除语义。

不需要修改前端、Agent 删除接口或数据库结构。

## 8. 测试设计

### 8.1 首次创建

- 没有设置、marker 和历史 Agent 时创建两个默认 Agent。
- 创建与租户初始化设置在同一事务提交。
- 初始化失败时不留下 `initialized=true`。

### 8.2 已初始化

- 两个 Agent 都存在：不创建，继续执行存储健康检查。
- Morty 已删除：不创建 Morty，只检查未删除的 Meeseeks。
- 两个都已删除：不创建，也不修复存储。
- Agent 仅 stopped、未删除：不创建副本，仍允许存储修复。
- Agent 已重命名：按 ID 识别，不创建 canonical 名称副本。
- 设置中的 Agent ID 已不存在：不创建。

### 8.3 兼容回填

- legacy marker 有效：写入租户设置，不创建。
- marker 指向已删除 Agent：仍视为已初始化，不创建。
- marker 缺失但数据库存在历史 canonical Agent：写入租户设置，不创建。
- marker 来自其他租户或格式损坏：忽略 marker，继续数据库历史判断。
- 完全没有历史证据：执行首次创建。

### 8.4 并发

- 两个 seeder 并发进入时，只有锁内第一个流程可以创建。
- 第二个流程取得锁后重新读取设置并进入已初始化分支。

### 8.5 回归验证

- 运行 `backend/tests/test_agent_seeder_storage_repair.py`。
- 运行与 Agent 删除、列表可见性相关的 scoped tests。
- 对修改文件运行 Ruff。
- 验证现有存储漂移修复测试继续通过。

## 9. 验收标准

- 新租户仍自动获得 Morty 和 Meeseeks。
- 删除任一默认 Agent 后，连续重启两次均不出现新副本。
- 重命名任一默认 Agent 后，连续重启两次均不出现 canonical 名称副本。
- stop 后重启不产生副本。
- 未删除默认 Agent 的 workspace/Skills 丢失后仍能被修复。
- 现有部署无需人工脚本即可自动建立数据库初始化事实。
- 多实例同时启动不会重复创建默认 Agent。

## 10. 非目标与风险

- 本次不自动清理旧版本已经创建的重复 Agent。
- 本次不改变普通 Agent 的删除、停止或重命名接口。
- 本次不把 Morty/Meeseeks 改成不可删除的 system Agent。
- 本次不以名称、Bio、角色描述等可变内容作为长期身份。
- legacy marker 丢失且历史 Agent 已重命名的极端部署，需要运维确认；不做推测性自动修复。

## 11. 实施顺序

1. 先补删除、停止、重命名和 legacy 回填的失败测试。
2. 增加租户初始化设置和兼容解析函数。
3. 加入租户级并发锁。
4. 拆分首次创建与存储修复路径。
5. 运行 scoped tests 和 Ruff。
6. 使用本地数据库验证首次初始化与删除后重启。
7. 部署前检查目标环境当前 marker、历史默认 Agent 行和重复 Agent 状态，不自动清理数据。
