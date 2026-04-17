# Clawith 多用户 Workspace 隔离方案

## 问题分析

当前 Clawith 的架构问题：
1. 每个 agent 只有一个 workspace：`/data/agents/{agent_id}/`
2. 多个用户与同一个 agent 对话时，共享同一个 workspace
3. 导致 session 污染：用户 A 上传的文件，用户 B 也能看到

## 解决方案

### 方案 1：用户级 Workspace 子目录（推荐）

为每个用户在 agent workspace 下创建独立的子目录：

```
/data/agents/{agent_id}/
├── skills/              # 共享技能目录
├── memory/              # 共享记忆目录
├── soul.md              # 共享人格定义
├── workspace/           # 共享工作区
└── users/               # 新增：用户隔离目录
    ├── {user_id_1}/     # 用户 1 的独立空间
    │   ├── files/       # 用户上传的文件
    │   ├── sessions/    # 用户会话数据
    │   └── memory.md    # 用户个人记忆
    └── {user_id_2}/     # 用户 2 的独立空间
        ├── files/
        ├── sessions/
        └── memory.md
```

**优点**：
- 保持 agent 共享知识（skills, soul, memory）
- 隔离用户私有文件和数据
- 最小化代码改动

**需要修改的文件**：

1. **agent_tools.py** - `ensure_workspace` 函数
   - 添加用户级目录初始化
   - 修改文件工具使用用户级路径

2. **feishu.py** - 消息处理
   - 在 `resolve_channel_user` 后获取用户 workspace 路径
   - 传递给工具执行上下文

3. **channel_session.py** - Session 管理
   - 添加用户 workspace 路径映射

### 方案 2：完全独立的 Agent 实例

为每个用户创建独立的 agent 副本：

```
/data/agents/
├── {agent_id}/           # 原始 agent（模板）
├── {agent_id}_{user_id_1}/  # 用户 1 的独立 agent
└── {agent_id}_{user_id_2}/  # 用户 2 的独立 agent
```

**优点**：
- 完全隔离
- 每个用户有独立的记忆、技能、配置

**缺点**：
- 资源浪费
- 数据同步复杂
- 需要修改 agent 创建逻辑

### 方案 3：Session 级上下文隔离

保持现有 workspace 结构，但在 LLM 调用时注入用户级上下文：

```python
# 在 build_agent_context 时添加用户隔离
async def build_agent_context(agent_id, user_id, ...):
    # 加载共享 context
    context = load_shared_context(agent_id)
    
    # 加载用户私有 context
    user_context = load_user_context(agent_id, user_id)
    
    # 合并注入
    return context + user_context
```

**优点**：
- 最小改动
- 不影响文件系统

**缺点**：
- 文件上传仍然共享
- 不是真正的隔离

## 推荐实施方案 1

### 修改清单

#### 1. 修改 `agent_tools.py`

```python
# 在 ensure_workspace 函数中添加用户目录
async def ensure_workspace(agent_id: uuid.UUID, user_id: uuid.UUID = None, tenant_id: str | None = None) -> Path:
    """Initialize agent workspace with standard structure."""
    ws = WORKSPACE_ROOT / str(agent_id)
    ws.mkdir(parents=True, exist_ok=True)
    
    # 创建共享目录
    (ws / "skills").mkdir(exist_ok=True)
    (ws / "memory").mkdir(exist_ok=True)
    (ws / "workspace").mkdir(exist_ok=True)
    
    # 新增：创建用户级目录
    if user_id:
        user_ws = ws / "users" / str(user_id)
        user_ws.mkdir(parents=True, exist_ok=True)
        (user_ws / "files").mkdir(exist_ok=True)
        (user_ws / "sessions").mkdir(exist_ok=True)
        
        # 创建用户记忆文件
        user_memory = user_ws / "memory.md"
        if not user_memory.exists():
            user_memory.write_text(f"# User Memory\n\n用户 ID: {user_id}\n", encoding="utf-8")
    
    return ws / "users" / str(user_id) if user_id else ws
```

#### 2. 修改文件上传工具

```python
# 在 _upload_file 等工具中使用用户级路径
async def _upload_file(agent_id, user_id, file_data):
    # 使用用户级 workspace
    user_ws = await ensure_workspace(agent_id, user_id)
    file_path = user_ws / "files" / filename
    ...
```

#### 3. 修改飞书消息处理

```python
# 在 feishu.py 消息处理中传递 user_id
user_id = platform_user.id
user_ws = await ensure_workspace(agent_id, user_id)

# 在工具执行上下文中使用 user_ws
```

### 数据库修改

在 `chat_sessions` 表中添加用户 workspace 路径：

```sql
ALTER TABLE chat_sessions ADD COLUMN user_workspace_path TEXT;
```

## 实施步骤

1. **备份现有数据**
2. **修改 `agent_tools.py`** - 添加用户级 workspace 支持
3. **修改文件工具** - 使用用户级路径
4. **修改消息处理** - 传递 user_id 和 user_ws
5. **测试** - 验证多用户隔离
6. **迁移脚本** - 为现有用户创建独立目录

## 预期效果

- ✅ 用户 A 上传的文件，用户 B 不可见
- ✅ 用户 A 的记忆，用户 B 不可见
- ✅ agent 共享知识（skills, soul）仍然共享
- ✅ 不同用户与同一 agent 对话，session 不污染
