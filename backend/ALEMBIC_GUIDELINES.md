# Alembic 数据库迁移管理规范

## 1. 概述

本规范旨在确保团队在使用 Alembic 进行数据库迁移管理时的一致性和可靠性，特别是在开发过程中对数据库模型的变更进行版本控制。

## 2. 版本管理规范

### 2.1 迁移文件命名规则

**统一采用以下命名格式**：
```
<timestamp>_<description>.py
```

- **timestamp**：使用 `YYYYMMDDHHMM` 格式的时间戳，确保迁移文件按时间顺序排序
- **description**：使用小写字母、数字和下划线，简洁描述迁移内容

**示例**：
- `202603131430_add_user_email_column.py`
- `202603140915_modify_agent_table.py`

### 2.2 版本历史管理

- **保持版本历史清晰**：每个迁移文件对应一个具体的数据库变更
- **避免合并迁移**：不要将多个不相关的变更合并到一个迁移文件中
- **版本回滚**：确保每个迁移都有对应的回滚逻辑
- **版本标记**：在重要的发布版本处添加标记，便于追踪

## 3. 开发流程规范

### 3.1 模型变更流程

1. **修改模型**：在 `app/models/` 目录中修改或添加模型
2. **生成迁移**：运行 `alembic revision --autogenerate -m "描述变更内容"`
3. **检查迁移**：手动检查生成的迁移文件，确保逻辑正确
4. **应用迁移**：运行 `alembic upgrade head` 应用到本地数据库
5. **测试验证**：确保应用正常运行，数据迁移正确
6. **提交代码**：将模型变更和迁移文件一起提交

### 3.2 协作开发流程

1. **拉取最新代码**：在开始工作前，确保拉取最新的代码和迁移文件
2. **分支管理**：在各自的分支上进行模型变更
3. **解决冲突**：如果遇到迁移文件冲突，手动解决并确保逻辑正确
4. **代码审查**：迁移文件需要经过代码审查，确保质量
5. **部署前验证**：在部署到生产环境前，在测试环境验证迁移

## 4. 最佳实践

### 4.1 迁移文件编写

- **保持简洁**：每个迁移文件只包含一个逻辑变更
- **添加注释**：对复杂的迁移逻辑添加注释说明
- **使用批量操作**：对于大量数据的迁移，使用批量操作提高性能
- **处理默认值**：为新增字段提供合理的默认值
- **考虑数据完整性**：确保迁移过程中数据的完整性

### 4.2 性能优化

- **索引创建**：在迁移中合理创建索引，提高查询性能
- **分批处理**：对于大型表的变更，使用分批处理避免锁表
- **事务管理**：合理使用事务，确保迁移的原子性

### 4.3 安全性

- **避免破坏性操作**：谨慎使用 `drop_table` 等破坏性操作
- **数据备份**：在执行重要迁移前，确保数据已经备份
- **权限控制**：确保迁移操作使用适当的数据库权限

## 5. 命令参考

### 5.1 常用命令

- **生成迁移**：
  ```bash
  alembic revision --autogenerate -m "描述变更内容"
  ```

- **应用迁移**：
  ```bash
  alembic upgrade head
  ```

- **回滚迁移**：
  ```bash
  alembic downgrade -1  # 回滚一个版本
  alembic downgrade base  # 回滚到初始状态
  ```

- **查看迁移历史**：
  ```bash
  alembic history
  ```

- **查看当前版本**：
  ```bash
  alembic current
  ```

### 5.2 环境变量

- 数据库连接字符串通过 `app/config.py` 中的 `DATABASE_URL` 配置
- 开发环境和生产环境应使用不同的数据库连接

## 6. 故障处理

### 6.1 迁移失败

1. **分析错误**：查看错误信息，确定失败原因
2. **回滚操作**：如果迁移失败，使用 `alembic downgrade` 回滚到上一个版本
3. **修复问题**：修复模型或迁移文件中的问题
4. **重新迁移**：再次运行迁移命令

### 6.2 数据丢失

- **立即停止**：发现数据丢失时立即停止操作
- **恢复备份**：使用最近的数据库备份恢复数据
- **重新迁移**：在恢复数据后，重新执行迁移

## 7. 版本控制集成

- **迁移文件**：将所有迁移文件纳入版本控制
- **忽略文件**：不要将 `alembic/versions/` 目录中的 `.pyc` 文件纳入版本控制
- **提交信息**：在提交迁移文件时，使用清晰的提交信息描述变更

## 8. 文档维护

- **更新文档**：当数据库结构发生重大变更时，更新相关文档
- **模型文档**：为复杂的模型添加文档说明
- **迁移记录**：保持迁移历史的清晰记录，便于后续维护

## 9. 数据初始化（Seeder）规范

### 9.1 架构说明

系统启动时会执行数据初始化（Seeder），用于创建系统运行所需的基础数据。Seeder 分为两类：

| 类型 | 职责 | 幂等方式 | 删除后是否重建 |
|------|------|---------|-------------|
| **配置同步 Seeder** | 每次部署同步最新的工具/模板/技能定义 | DB 查询（按 name/folder_name） | ✅ 是（系统基础设施） |
| **一次性初始化 Seeder** | 首次部署创建默认数据（如默认 Agent） | DB 标记（system_settings） | ❌ 否（尊重用户操作） |

### 9.2 现有 Seeder 清单

| Seeder | 文件 | DB 标记 key | 删除后重建？ |
|--------|------|------------|------------|
| 内置工具 | `tool_seeder.py` | `builtin_tools_seeded` | ❌ 不重建 |
| Atlassian 配置 | `tool_seeder.py` | `atlassian_rovo_config_seeded` | ❌ 不重建 |
| Agent 模板 | `template_seeder.py` | `builtin_templates_seeded` | ❌ 不重建 |
| 内置技能 | `skill_seeder.py` | `builtin_skills_seeded` | ❌ 不重建 |
| 默认 Agent | `agent_seeder.py` | `default_agents_seeded` | ❌ 不重建 |
| 技能推送 | `skill_seeder.py` | （依赖 `builtin_skills_seeded` 标记状态） | — |

### 9.3 开发规范

#### 新增"配置同步"类数据（工具/模板/技能）

直接在对应 seeder 的定义列表中添加，无需创建 Alembic migration：

```python
# 例：在 tool_seeder.py 的 BUILTIN_TOOLS 列表中添加新工具
{
    "name": "new_tool_name",
    "display_name": "新工具",
    "description": "工具描述",
    "category": "类别",
    ...
}
```

**要求：**
- 必须使用唯一标识字段（name / folder_name）作为幂等判断依据
- 已存在时执行**更新**（同步最新定义），不存在时执行**插入**
- 禁止在 seeder 中使用 `INSERT` 不带 `ON CONFLICT` 或存在性检查
- 用户手动删除后，下次启动**会重建**（这是预期行为——系统基础设施不应缺失）

#### 新增"一次性初始化"类数据（默认用户/Agent/配置项）

使用 `system_settings` 表的 DB 标记模式：

```python
# 1. 检查 DB 标记
marker = await db.execute(
    select(SystemSetting).where(SystemSetting.key == "xxx_seeded")
)
if marker.scalar_one_or_none() is not None:
    return  # 已执行过，永远不再执行

# 2. 检查数据是否已存在（兼容旧版本 / 远程 DB）
existing = await db.execute(select(Model).where(...))
if existing.scalars().first() is not None:
    # 补写标记，跳过创建
    db.add(SystemSetting(key="xxx_seeded", value={...}))
    await db.commit()
    return

# 3. 创建数据 + 写入标记
...
db.add(SystemSetting(key="xxx_seeded", value={...}))
await db.commit()
```

**要求：**
- 必须使用 `system_settings` 表作为标记，禁止使用文件标记（文件不跟随数据库，换环境会失效）
- 标记写入必须在 `db.commit()` 同一个事务中（原子性）
- 标记存在时永远不再执行，即使用户手动删除了数据（尊重用户操作）
- 必须包含"兼容检查"——DB 中已有数据但无标记时，补写标记并跳过

#### 一次性数据变更（回填/迁移/修正）

使用 Alembic data migration（参考本文档第 3 节），不要放在 startup seeder 中：

```bash
alembic revision -m "backfill_xxx_column"
```

```python
def upgrade():
    conn = op.get_bind()
    # 带幂等检查的数据操作
    conn.execute(sa.text("UPDATE ... WHERE ... AND new_column IS NULL"))

def downgrade():
    conn.execute(sa.text("UPDATE ... SET new_column = NULL WHERE ..."))
```

### 9.4 禁止事项

| 禁止行为 | 原因 | 正确做法 |
|---------|------|---------|
| 使用文件标记（`.seeded`）判断是否已初始化 | 文件不跟随 DB，换环境/重建容器失效 | 使用 `system_settings` DB 标记 |
| Seeder 中不做存在性检查直接 INSERT | 重复启动会创建重复数据 | 先查询再插入，或使用 `ON CONFLICT` |
| 在 startup seeder 中做数据回填/修正 | 每次启动都执行，性能浪费且逻辑混乱 | 使用 Alembic data migration |
| Seeder 依赖执行顺序但不显式声明 | 换顺序后默默失败 | 在 `main.py` 中用注释标明依赖关系 |

### 9.5 Agent 模板目录规范（`agent_template/`）

`agent_template/` 是每个新建 Agent 的**文件系统初始模板**。创建 Agent 时，`agent_manager.py` 会将整个目录 `copytree` 到 Agent 的独立工作区（`{AGENT_DATA_DIR}/{agent_id}/`），然后替换模板变量。

#### 目录结构

```
agent_template/
├── soul.md                    ← Agent 人格定义模板（含 {{agent_name}} 等变量）
├── souls/                     ← 角色专用人格模板（engineer/hr/sales），创建时按角色选用
│   ├── engineer.md
│   ├── hr.md
│   └── sales.md
├── memory/
│   ├── memory.md              ← 长期记忆模板（带分类引导结构）
│   ├── MEMORY_INDEX.md        ← 记忆索引
│   └── curiosity_journal.md   ← 自主探索日志
├── skills/                    ← 预装技能定义（复制到 Agent 后可被 Skills 索引发现）
│   ├── FOLLOW_UP_TASK.md
│   ├── MEETING_MANAGEMENT.md
│   ├── RESEARCH_AND_REPORT.md
│   └── MCP_INSTALLER.md
├── HEARTBEAT.md               ← 心跳唤醒指令
├── state.json                 ← Agent 状态初始模板
├── todo.json                  ← 任务跟踪初始模板
├── enterprise_info/           ← 共享企业信息目录
├── daily_reports/             ← 日报存储目录
└── workspace/                 ← Agent 工作文件目录
```

#### 模板变量

`soul.md` 和 `souls/*.md` 中支持以下变量，创建 Agent 时由 `agent_manager.py` 替换：

| 变量 | 替换为 | 来源 |
|------|--------|------|
| `{{agent_name}}` | Agent 名称 | `Agent.name` |
| `{{role_description}}` | 角色描述 | `Agent.role_description`，默认"通用助手" |
| `{{creator_name}}` | 创建者姓名 | `User.display_name` |
| `{{created_at}}` | 创建日期 | 当前 UTC 日期（YYYY-MM-DD） |

#### 修改规范

| 操作 | 影响范围 | 注意事项 |
|------|---------|---------|
| 修改 `soul.md` | **仅影响新建的 Agent** | 已有 Agent 的 soul.md 不会自动更新 |
| 修改 `skills/*.md` | **仅影响新建的 Agent** | 已有 Agent 需通过 `push_default_skills_to_existing_agents()` 同步 |
| 修改 `HEARTBEAT.md` | **仅影响新建的 Agent** | 已有 Agent 需手动更新或通过迁移脚本批量更新 |
| 修改 `memory/memory.md` | **仅影响新建的 Agent** | 已有 Agent 的记忆由系统自动管理（memory_extractor） |
| 新增 `souls/*.md` | 无直接影响 | 需同步修改 `agent_manager.py` 或 API 以支持角色选择 |

**关键点：模板修改不会影响已创建的 Agent。** 如需批量更新已有 Agent 的模板文件，应编写专用的迁移脚本或在 startup 代码中添加同步逻辑（参考 `push_default_skills_to_existing_agents()` 的模式）。

#### 与 Seeder 的关系

```
agent_template/          ← 文件系统模板（soul.md, skills/, memory/）
  ↓ copytree
{AGENT_DATA_DIR}/{id}/   ← 每个 Agent 的独立工作区

skill_seeder.py          ← DB 中的技能定义（Skill 表）
  ↓ push_default_skills
{AGENT_DATA_DIR}/{id}/skills/  ← Agent 工作区中的技能文件

二者是独立的分发渠道：
- 新建 Agent → 从 agent_template/ 复制
- 已有 Agent 补装新技能 → 从 skill_seeder 推送
```

### 9.6 启动顺序与依赖

```
main.py 启动序列：
│
├─ 1. seed_builtin_tools()        ← 无依赖
├─ 2. seed_agent_templates()      ← 无依赖
├─ 3. seed_skills()               ← 无依赖
│     └─ push_default_skills_to_existing_agents()
├─ 4. seed_default_agents()       ← 依赖：admin 用户、工具、技能必须已存在
│
└─ 注意：1-3 顺序可调，4 必须在 1-3 之后
```

修改启动顺序时，必须确认依赖关系不被打破。

## 10. 附则

本规范适用于所有使用 Alembic 进行数据库迁移管理的开发人员，应严格遵守。如有特殊情况需要偏离本规范，应提前与团队沟通并获得批准。
