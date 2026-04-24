# 企业微信多账号支持（Multi-account）功能设计文档

## 一、需求背景

### 1.1 当前问题
- Clawith 当前仅支持单账号企业微信配置
- 无法支持企业多部门/多业务线的独立账号隔离
- Webhook 路径无法区分不同账号的回调

### 1.2 目标
完全对标 OpenClaw wocom 插件的多账号功能，实现：
- 支持无限扩展的账号矩阵（Multi-account）
- 每个 `accountId` 是独立的通道
- Webhook 路径支持按账号区分：`/api/channel/wecom/bot/{accountId}` 和 `/api/channel/wecom/agent/{accountId}`
- 代码和UI风格符合 Clawith 项目规范（不使用 emoji，使用 Tabler 图标）

---

## 二、架构设计

### 2.1 核心概念

#### accountId（账号标识）
- 唯一标识一个企业微信账号
- 格式：字符串（例如 `"default"`, `"ops"`, `"sales"`）
- 默认账号：`"default"`

#### 账号矩阵结构
```
channels.wecom.accounts {
  "default": {    // 默认账号
    "bot_id": "aibxxx",
    "bot_secret": "xxx",
    "connection_mode": "websocket"
  },
  "ops": {        // 运维专用账号
    "bot_id": "aibyyy",
    "bot_secret": "yyy",
    "connection_mode": "websocket"
  }
}
```

### 2.2 配置存储结构

#### ChannelConfig.extra_config 新增字段
```typescript
{
  "account_id": "default",              // 当前使用的账号ID，默认 "default"
  "accounts": {                        // 多账号配置
    "default": {
      "bot_id": "aibxxx",
      "bot_secret": "xxx",
      "connection_mode": "websocket",
      "wecom_agent_id": "",           // Webhook 模式专用
      "corp_id": "",                  // Webhook 模式专用
      "secret": "",                   // Webhook 模式专用
      "token": "",                    // Webhook 模式专用
      "encoding_aes_key": ""           // Webhook 模式专用
    },
    "ops": {
      "bot_id": "aibyyy",
      "bot_secret": "yyy",
      "connection_mode": "websocket"
    }
  },
  "default_account": "default"          // 默认使用的账号ID（可选）
}
```

### 2.3 Webhook 路径设计

#### 当前路径（向后兼容）
- `/api/channel/wecom/{agent_id}/webhook` → 默认使用 `"default"` 账号

#### 新路径（多账号支持）
- `/api/channel/wecom/{agent_id}/bot/{account_id}/webhook` → 指定账号的 Bot 模式
- `/api/channel/wecom/{agent_id}/agent/{account_id}/webhook` → 指定账号的 Agent 模式

#### 路径解析逻辑
```python
# 示例：/api/channel/wecom/{agent_id}/bot/ops/webhook
# agent_id = UUID
# account_id = "ops"
# mode = "bot"
```

---

## 三、受影响的文件清单

### 3.1 后端文件

| 文件路径 | 修改类型 | 受影响的函数/模块 |
|---------|---------|-----------------|
| `backend/app/api/wecom.py` | 修改 | `configure_wecom_channel()` - 支持多账号配置 |
| `backend/app/api/wecom.py` | 修改 | `get_wecom_webhook_url()` - 支持返回多账号 URL |
| `backend/app/api/wecom.py` | 修改 | `wecom_verify_webhook()` - 支持多账号路径 |
| `backend/app/api/wecom.py` | 修改 | `wecom_event_webhook()` - 支持多账号路径 |
| `backend/app/api/wecom.py` | 新增 | `_get_account_config()` - 从 extra_config 中提取指定账号配置 |
| `backend/app/models/channel_config.py` | 修改 | `extra_config` 字段的文档注释 |
| `backend/app/services/wecom_stream.py` | 修改 | `start_client()` - 支持启动多个账号的 WebSocket 客户端 |
| `backend/app/services/wecom_stream.py` | 修改 | `_process_wecom_stream_message()` - 接收 account_id 参数 |
| `backend/app/services/wecom_stream.py` | 修改 | `WeComStreamManager` - 支持管理多个账号的客户端 |

### 3.2 前端文件

| 文件路径 | 修改类型 | 受影响的组件/函数 |
|---------|---------|-----------------|
| `frontend/src/components/ChannelConfig.tsx` | 修改 | `wecom` channel 配置 - 添加多账号管理 UI |
| `frontend/src/i18n/zh.json` | 修改 | 新增多账号相关的翻译 key |
| `frontend/src/i18n/en.json` | 修改 | 新增多账号相关的翻译 key |

### 3.3 数据库
无需修改 - 使用现有 `ChannelConfig` 表的 `extra_config` JSON 字段

---

## 四、实现细节

### 4.1 后端 API 修改

#### 4.1.1 配置 API (`/agents/{agent_id}/wecom-channel`)

**请求格式：**
```json
{
  "account_id": "default",
  "accounts": {
    "default": {
      "bot_id": "aibxxx",
      "bot_secret": "xxx",
      "connection_mode": "websocket"
    },
    "ops": {
      "bot_id": "aibyyy",
      "bot_secret": "yyy",
      "connection_mode": "websocket"
    }
  }
}
```

**兼容性处理：**
- 如果请求中只包含 `bot_id`/`bot_secret` 等旧字段（没有 `accounts`），自动转换为 `accounts.default` 结构
- 保持向后兼容，避免破坏现有配置

#### 4.1.2 Webhook 路径获取 (`/agents/{agent_id}/wecom-channel/webhook-url`)

**新增参数：**
```
GET /api/agents/{agent_id}/wecom-channel/webhook-url?account_id=ops
```

**响应格式：**
```json
{
  "webhook_urls": {
    "default": "https://example.com/api/channel/wecom/{agent_id}/webhook",  // 兼容旧路径
    "default_bot": "https://example.com/api/channel/wecom/{agent_id}/bot/default/webhook",
    "default_agent": "https://example.com/api/channel/wecom/{agent_id}/agent/default/webhook",
    "ops_bot": "https://example.com/api/channel/wecom/{agent_id}/bot/ops/webhook",
    "ops_agent": "https://example.com/api/channel/wecom/{agent_id}/agent/ops/webhook"
  }
}
```

#### 4.1.3 Webhook 路由修改

**当前路由：**
```python
@router.get("/channel/wecom/{agent_id}/webhook")
@router.post("/channel/wecom/{agent_id}/webhook")
```

**新增路由：**
```python
@router.get("/channel/wecom/{agent_id}/bot/{account_id}/webhook")
@router.post("/channel/wecom/{agent_id}/bot/{account_id}/webhook")
@router.get("/channel/wecom/{agent_id}/agent/{account_id}/webhook")
@router.post("/channel/wecom/{agent_id}/agent/{account_id}/webhook")
```

**路由解析逻辑：**
```python
async def wecom_event_webhook(
    agent_id: uuid.UUID,
    account_id: str = "default",  # 默认使用 default 账号
    request: Request,
    ...
):
    # 从 extra_config 中提取指定账号的配置
    account_config = _get_account_config(config, account_id)
```

### 4.2 WebSocket 客户端管理

#### 4.2.1 WeComStreamManager 修改

**当前结构：**
```python
self._clients: Dict[uuid.UUID, object] = {}  # agent_id -> client
self._tasks: Dict[uuid.UUID, asyncio.Task] = {}  # agent_id -> task
```

**修改后结构：**
```python
self._clients: Dict[Tuple[uuid.UUID, str], object] = {}  # (agent_id, account_id) -> client
self._tasks: Dict[Tuple[uuid.UUID, str], asyncio.Task] = {}  # (agent_id, account_id) -> task
```

#### 4.2.2 启动多账号客户端

**关键点：避免 N+1 查询和会话关闭后访问问题**

```python
async def start_all(self):
    """启动所有已配置账号的 WebSocket 客户端"""
    async with async_session() as db:
        # 使用 eager loading 避免懒加载问题
        # extra_config 是 JSON 字段，不需要预加载关联对象
        result = await db.execute(
            select(ChannelConfig).where(
                ChannelConfig.is_configured == True,
                ChannelConfig.channel_type == "wecom",
            )
        )
        configs = result.scalars().all()

        # ✅ 在会话关闭前提取所有需要的数据
        # extra_config 是 JSON 字段，访问不会触发数据库查询
        account_configs = []
        for config in configs:
            extra = config.extra_config or {}
            accounts = extra.get("accounts", {})
            for account_id, account_config in accounts.items():
                if account_config.get("connection_mode") == "websocket":
                    bot_id = account_config.get("bot_id", "")
                    bot_secret = account_config.get("bot_secret", "")
                    if bot_id and bot_secret:
                        account_configs.append({
                            "agent_id": config.agent_id,
                            "account_id": account_id,
                            "bot_id": bot_id,
                            "bot_secret": bot_secret,
                        })

    started = 0
    # ✅ 使用提取的数据启动客户端，避免会话已关闭问题
    for cfg in account_configs:
        await self.start_client(
            cfg["agent_id"],
            cfg["account_id"],
            cfg["bot_id"],
            cfg["bot_secret"],
        )
        started += 1

    logger.info(f"[WeCom Stream] Started {started} WeCom AI Bot client(s)")
```

**设计要点：**
1. **在数据库会话关闭前提取所有需要的数据**（agent_id, account_id, bot_id, bot_secret）
2. **使用纯数据结构（字典）存储配置**，避免访问已关闭会话的 ORM 对象
3. **`extra_config` 是 JSON 字段**，直接访问不会触发懒加载，但在会话关闭前访问更安全
4. **批量提取所有配置后**，再启动 WebSocket 客户端（这是异步操作，可能耗时较长）

### 4.3 前端 UI 设计

#### 4.3.1 账号管理界面

**布局结构：**
```
┌─────────────────────────────────────────────────┐
│ WeCom Channel Configuration                 │
├─────────────────────────────────────────────────┤
│ Connection Mode: [WebSocket ▼]            │
├─────────────────────────────────────────────────┤
│ Accounts Management                        │
│ ┌─────────────────────────────────────┐     │
│ │ ☑ default (default account)       │     │
│ │   Bot ID: aibxxx                │     │
│ │   Bot Secret: ***                │     │
│ │   [Edit] [Delete]               │     │
│ └─────────────────────────────────────┘     │
│ ┌─────────────────────────────────────┐     │
│ │ ☑ ops                          │     │
│ │   Bot ID: aibyyy                │     │
│ │   Bot Secret: ***                │     │
│ │   [Edit] [Delete]               │     │
│ └─────────────────────────────────────┘     │
│                                          │
│ [+ Add Account]                          │
├─────────────────────────────────────────────────┤
│ Webhook URLs                             │
│ default: https://.../webhook             │
│ default_bot: https://.../bot/default/...  │
│ ops_bot: https://.../bot/ops/...         │
└─────────────────────────────────────────────────┘
```

**交互逻辑：**
1. 显示账号列表，每个账号显示配置状态（☑ 已配置 / ☐ 未配置）
2. 点击 `[Edit]` 弹出编辑表单（修改 Bot ID/Secret/Agent ID 等）
3. 点击 `[Delete]` 删除账号（如果只有一个账号，禁止删除）
4. 点击 `[+ Add Account]` 添加新账号（弹出表单）

**图标选择：**
- 使用 Tabler Icons（项目已有）：`<IconTrash />`、`<IconEdit />`、`<IconPlus />`
- 禁止使用 emoji

#### 4.3.2 配置表单

**新增字段：**
- `account_id`: 账号 ID（文本输入框，必填）
- `is_default`: 是否设为默认账号（复选框）

**表单验证：**
- `account_id` 不能为空
- `account_id` 不能重复（区分大小写）
- 至少保留一个已配置的账号

---

## 五、边界条件和异常处理

### 5.1 边界条件

1. **账号 ID 重复**
   - 前端：添加账号时检查 `account_id` 是否已存在
   - 后端：配置时验证唯一性，返回 400 错误

2. **删除最后一个账号**
   - 前端：禁用删除按钮，显示提示 "Cannot delete the last account"
   - 后端：配置验证，至少保留一个账号

3. **不存在的 account_id**
   - Webhook 路由：返回 404 错误，日志记录
   - WebSocket 启动：跳过该账号，记录警告日志

4. **向后兼容**
   - 旧配置（只有 `bot_id`/`bot_secret`）自动转换为 `accounts.default`
   - 旧 Webhook 路径 `/api/channel/wecom/{agent_id}/webhook` 继续有效，使用 `default` 账号

### 5.2 异常处理

1. **WebSocket 连接失败**
   - 记录错误日志
   - 自动重连（已有逻辑）
   - 不影响其他账号的 WebSocket 客户端

2. **配置加载失败**
   - 捕获 `extra_config` 解析异常
   - 记录错误日志
   - 返回部分错误信息给前端

3. **Webhook 路径验证失败**
   - 签名验证失败：返回 403
   - 账号不存在：返回 404
   - 其他错误：返回 500 + 错误日志

---

## 六、数据流路径

### 6.0 异步 SQLAlchemy 问题说明

在实现多账号功能时，必须严格遵守以下原则以避免异步会话问题：

#### 6.0.1 会话关闭后访问 ORM 对象
❌ **错误模式**：
```python
async with async_session() as db:
    configs = result.scalars().all()

# ⚠️ 会话已关闭！访问 ORM 对象可能触发懒加载
for config in configs:
    extra = config.extra_config  # 有风险！
```

✅ **正确模式**：
```python
async with async_session() as db:
    configs = result.scalars().all()
    
    # 在会话关闭前提取所有需要的数据
    extracted_data = []
    for config in configs:
        extra = config.extra_config or {}
        extracted_data.append({
            "agent_id": config.agent_id,
            "account_id": extra.get("account_id", "default"),
            # ... 其他字段
        })

# 使用提取的纯数据，避免 ORM 对象
for data in extracted_data:
    process(data["agent_id"], data["account_id"])
```

#### 6.0.2 企业微信相关需要修复的问题点
1. **wecom_stream.py**: `_process_wecom_stream_message()` 缺少 account_id 参数
   - 位置: `backend/app/services/wecom_stream.py:266-272`
   - 问题: WebSocket 消息处理时无法区分账号
   - 修复建议: 添加 `account_id` 参数，并传递给相关函数

2. **wecom.py**: Webhook 路由需要支持 account_id 路径参数
   - 位置: `backend/app/api/wecom.py:277-357`
   - 问题: 当前路由不支持 `/channel/wecom/{agent_id}/bot/{account_id}/webhook` 格式
   - 修复建议: 添加 account_id 参数，并调用 `_get_account_config()` 获取配置

**注意**: feishu_ws.py 虽然存在类似的会话管理问题，但属于飞书模块，不在本次企业微信多账号功能开发范围内，暂不修改。

---

## 六、数据流路径

### 6.1 配置流程

```
用户在 AgentDetail 页面点击 "Configure WeCom"
  ↓
前端调用 POST /api/agents/{agent_id}/wecom-channel
  ↓
后端配置 API：
  1. 解析请求体（支持旧格式和新格式）
  2. 转换为统一的多账号结构
  3. 保存到 ChannelConfig.extra_config
  4. 如果是 WebSocket 模式，触发 wecom_stream_manager.start_client()
  ↓
返回配置成功
```

### 6.2 消息接收流程

```
企业微信发送消息到 Webhook
  ↓
POST /api/channel/wecom/{agent_id}/bot/ops/webhook
  ↓
后端 wecom_event_webhook():
  1. 提取 agent_id 和 account_id ("ops")
  2. 从 extra_config.accounts["ops"] 中读取配置
  3. 解密消息
  4. 调用 _process_wecom_text() 处理消息
  ↓
调用 LLM 生成回复
  ↓
通过 WeCom API 发送回复
```

### 6.3 WebSocket 连接流程

```
系统启动或配置更新
  ↓
wecom_stream_manager.start_all()
  ↓
遍历所有已配置的 WeCom ChannelConfig
  ↓
对每个 WebSocket 模式的账号：
  1. 提取 account_id, bot_id, bot_secret
  2. 启动独立的 WebSocket 客户端
  3. 注册事件处理器（on_text, on_image, on_file）
  ↓
客户端连接成功，准备接收消息
```

---

## 七、测试方案

### 7.1 单元测试

1. **配置转换测试**
   - 测试旧格式自动转换为 `accounts.default`
   - 测试新格式保存正确

2. **账号管理测试**
   - 测试添加/编辑/删除账号
   - 测试账号 ID 重复验证
   - 测试删除最后一个账号的限制

3. **路由解析测试**
   - 测试多账号路径正确解析
   - 测试默认路径向后兼容

### 7.2 集成测试

1. **多账号 Webhook 测试**
   - 配置两个账号（default 和 ops）
   - 分别向两个账号的 Webhook 发送消息
   - 验证消息正确路由到对应账号

2. **WebSocket 多客户端测试**
   - 启动两个账号的 WebSocket 客户端
   - 同时向两个账号发送消息
   - 验证回复正确

3. **向后兼容测试**
   - 使用旧格式配置（单账号）
   - 验证功能正常
   - 升级到多账号配置
   - 验证数据迁移正确

### 7.3 手动测试

1. **UI 测试**
   - 打开 Agent 设置页面
   - 配置多个 WeCom 账号
   - 验证 Webhook URL 正确显示
   - 测试账号添加/编辑/删除功能

2. **端到端测试**
   - 在企业微信中配置多个 Bot
   - 分别配置到 Clawith 的不同账号
   - 发送消息，验证回复正确

---

## 八、预期结果

### 8.1 功能目标

- ✅ 支持配置多个企业微信账号
- ✅ Webhook 路径区分不同账号
- ✅ WebSocket 支持多账号并行连接
- ✅ 向后兼容旧的单账号配置
- ✅ UI 符合 Clawith 项目风格（无 emoji，使用 Tabler 图标）

### 8.2 非功能目标

- ✅ 配置更新实时生效（无需重启服务）
- ✅ WebSocket 客户端自动重连
- ✅ 错误处理完善，日志清晰
- ✅ 前后端分离，API 设计清晰

---

## 九、前端实现设计（Tasks 8-12）

### 9.1 组件架构

#### 9.1.1 WeCom 账号列表组件

**位置**: `frontend/src/components/ChannelConfig.tsx`

**功能需求**:
1. 显示当前已配置的所有账号列表
2. 每个账号显示：
   - Account ID
   - Bot ID（脱敏显示）
   - Connection Mode（WebSocket/Webhook）
   - 操作按钮：Edit / Delete
3. "Add Account" 按钮
4. 表单验证：
   - Account ID 唯一性
   - Bot ID 和 Bot Secret 必填
   - 不能删除最后一个账号

**UI 结构**:
```tsx
<div className="wecom-accounts-list">
  {accounts.map(account => (
    <div key={account.id} className="account-item">
      <div className="account-info">
        <span className="account-id">{account.id}</span>
        <span className="bot-id">{account.bot_id}</span>
        <span className="mode">{account.connection_mode}</span>
      </div>
      <div className="account-actions">
        <button onClick={() => onEdit(account)}>Edit</button>
        <button onClick={() => onDelete(account.id)}>Delete</button>
      </div>
    </div>
  ))}
  <button onClick={onAddAccount}>Add Account</button>
</div>
```

#### 9.1.2 账号编辑对话框

**功能需求**:
1. 弹窗显示账号表单
2. 表单字段：
   - Account ID（编辑时禁用）
   - Bot ID（WebSocket 模式）
   - Bot Secret（WebSocket 模式，密码类型）
   - Connection Mode（WebSocket/Webhook 切换）
   - Webhook 模式字段（CorpID, AgentID, Secret, Token, EncodingAESKey）
3. 保存/取消按钮
4. 表单验证

#### 9.1.3 Webhook URL 显示区域

**功能需求**:
1. 可展开/折叠显示所有账号的 Webhook URL
2. 每个 URL 包含：
   - Account ID 标签
   - 完整 URL
   - 复制按钮
3. 格式：
   ```
   [default] https://domain.com/api/channel/wecom/{agent_id}/bot/default/webhook
   [ops] https://domain.com/api/channel/wecom/{agent_id}/bot/ops/webhook
   ```

**UI 结构**:
```tsx
<details className="wecom-webhook-urls">
  <summary>Webhook URLs ({accounts.length} accounts)</summary>
  {accounts.map(account => (
    <div key={account.id} className="webhook-url-item">
      <span className="account-badge">{account.id}</span>
      <code>{webhookUrl}</code>
      <LinearCopyButton textToCopy={webhookUrl} />
    </div>
  ))}
</details>
```

### 9.2 数据流

#### 9.2.1 加载配置（Read）

```typescript
// 1. Query: GET /agents/{agentId}/wecom-channel
const { data: wecomConfig } = useQuery({
  queryKey: ['wecom-channel', agentId],
  queryFn: () => fetchAuth(`/agents/${agentId}/wecom-channel`),
});

// 2. 解析 extra_config
const accounts = wecomConfig?.extra_config?.accounts || {};
const accountsList = Object.entries(accounts).map(([id, config]) => ({
  id,
  ...config
}));

// 3. 渲染账号列表
```

#### 9.2.2 保存配置（Write）

```typescript
// 1. 构建多账号 payload
const buildMultiAccountPayload = (form: AccountForm, accounts: Account[]) => {
  const accountsObj = accounts.reduce((acc, account) => {
    acc[account.id] = {
      bot_id: account.bot_id,
      bot_secret: account.bot_secret,
      connection_mode: account.connection_mode,
    };
    return acc;
  }, {});

  return {
    channel_type: 'wecom',
    extra_config: {
      accounts: accountsObj,
    },
  };
};

// 2. Mutation: POST /agents/{agentId}/wecom-channel
saveMutation.mutate({
  data: buildMultiAccountPayload(formData, accountsList),
});
```

#### 9.2.3 Webhook URL 加载

```typescript
// 1. Query: GET /agents/{agentId}/wecom-channel/webhook-url
const { data: wecomWebhook } = useQuery({
  queryKey: ['wecom-webhook-url', agentId],
  queryFn: () => fetchAuth(`/agents/${agentId}/wecom-channel/webhook-url`),
});

// 2. 解析响应（后端返回多账号 URL）
// {
//   default: "https://domain.com/api/channel/wecom/{agent_id}/bot/default/webhook",
//   ops: "https://domain.com/api/channel/wecom/{agent_id}/bot/ops/webhook"
// }
const webhookUrls = wecomWebhook?.urls || {};

// 3. 渲染 Webhook URL 列表
```

### 9.3 API 调用

#### 9.3.1 读取配置

```typescript
GET /agents/{agentId}/wecom-channel

Response: {
  id: "uuid",
  channel_type: "wecom",
  is_configured: true,
  app_id: "wwxxxxx",  // 旧格式兼容
  app_secret: "xxx",
  extra_config: {
    accounts: {
      "default": {
        bot_id: "aibxxx",
        bot_secret: "xxx",
        connection_mode: "websocket",
      },
      "ops": {
        bot_id: "aibyyy",
        bot_secret: "yyy",
        connection_mode: "webhook",
        corp_id: "wwyyyyy",
        wecom_agent_id: "1",
        secret: "zzz",
        token: "aaa",
        encoding_aes_key: "bbb",
      },
    },
  },
}
```

#### 9.3.2 保存配置

```typescript
POST /agents/{agentId}/wecom-channel

Request: {
  channel_type: "wecom",
  extra_config: {
    accounts: {
      "default": {
        bot_id: "aibxxx",
        bot_secret: "xxx",
        connection_mode: "websocket",
      },
      "ops": {
        bot_id: "aibyyy",
        bot_secret: "yyy",
        connection_mode: "webhook",
        corp_id: "wwyyyyy",
        wecom_agent_id: "1",
        secret: "zzz",
        token: "aaa",
        encoding_aes_key: "bbb",
      },
    },
  },
}

Response: {
  id: "uuid",
  channel_type: "wecom",
  is_configured: true,
  extra_config: { ... },
}
```

#### 9.3.3 获取 Webhook URL

```typescript
GET /agents/{agentId}/wecom-channel/webhook-url

Response: {
  urls: {
    "default": "https://domain.com/api/channel/wecom/{agent_id}/bot/default/webhook",
    "ops": "https://domain.com/api/channel/wecom/{agent_id}/bot/ops/webhook",
  },
}
```

### 9.4 向后兼容

#### 9.4.1 旧格式自动转换

当检测到旧格式配置时（存在 `bot_id` 和 `bot_secret`，但不存在 `accounts` 字段）：

```typescript
// 检测旧格式
const isLegacy = wecomConfig?.extra_config?.bot_id &&
                !wecomConfig?.extra_config?.accounts;

// 自动转换为新格式
let accountsList = [];
if (isLegacy) {
  accountsList = [{
    id: 'default',
    bot_id: wecomConfig.extra_config.bot_id,
    bot_secret: wecomConfig.extra_config.bot_secret,
    connection_mode: 'websocket',
  }];
} else {
  accountsList = Object.entries(wecomConfig.extra_config?.accounts || {}).map(
    ([id, config]) => ({ id, ...config })
  );
}
```

#### 9.4.2 单账号配置简化

对于只有一个账号（default）的情况，可以简化显示：

```typescript
// 仅显示单个账号，不显示列表
if (accountsList.length === 1 && accountsList[0].id === 'default') {
  // 显示简化表单
  return <SimpleAccountForm account={accountsList[0]} />;
} else {
  // 显示账号列表
  return <MultiAccountList accounts={accountsList} />;
}
```

### 9.5 表单验证

#### 9.5.1 Account ID 唯一性

```typescript
const validateAccountId = (accountId: string, accounts: Account[]) => {
  const existing = accounts.find(a => a.id === accountId);
  if (existing) {
    return 'Account ID already exists';
  }
  return null;
};
```

#### 9.5.2 必填字段验证

```typescript
const validateRequired = (field: string, value: string, account: Account) => {
  if (account.connection_mode === 'websocket') {
    if (field === 'bot_id' && !value) return 'Bot ID is required';
    if (field === 'bot_secret' && !value) return 'Bot Secret is required';
  } else {
    // Webhook mode validation
    if (field === 'corp_id' && !value) return 'CorpID is required';
    if (field === 'secret' && !value) return 'Secret is required';
    // ...
  }
  return null;
};
```

#### 9.5.3 删除最后一个账号限制

```typescript
const canDeleteAccount = (accountId: string, accounts: Account[]) => {
  if (accounts.length <= 1) {
    return 'Cannot delete the last account';
  }
  return null;
};
```

### 9.6 翻译文件更新

#### 9.6.1 中文翻译（zh.json）

```json
{
  "agent": {
    "settings": {
      "channel": {
        "wecomMultiAccount": {
          "title": "企业微信多账号配置",
          "accounts": "账号列表",
          "addAccount": "添加账号",
          "editAccount": "编辑账号",
          "deleteAccount": "删除账号",
          "accountId": "账号 ID",
          "accountIdPlaceholder": "例如：default, ops, sales",
          "botId": "Bot ID",
          "botSecret": "Bot Secret",
          "connectionMode": "连接模式",
          "webhookUrls": "Webhook URLs",
          "webhookUrlsCount": "Webhook URLs ({count} 个账号)",
          "accountUrls": {
            "bot": "Bot 模式",
            "agent": "Agent 模式"
          },
          "errors": {
            "accountIdRequired": "账号 ID 不能为空",
            "accountIdDuplicate": "账号 ID 已存在",
            "cannotDeleteLastAccount": "不能删除最后一个账号",
            "botIdRequired": "Bot ID 不能为空",
            "botSecretRequired": "Bot Secret 不能为空",
            "corpIdRequired": "CorpID 不能为空",
            "secretRequired": "Secret 不能为空"
          }
        }
      }
    }
  }
}
```

#### 9.6.2 英文翻译（en.json）

```json
{
  "agent": {
    "settings": {
      "channel": {
        "wecomMultiAccount": {
          "title": "WeCom Multi-Account Configuration",
          "accounts": "Accounts",
          "addAccount": "Add Account",
          "editAccount": "Edit Account",
          "deleteAccount": "Delete Account",
          "accountId": "Account ID",
          "accountIdPlaceholder": "e.g.: default, ops, sales",
          "botId": "Bot ID",
          "botSecret": "Bot Secret",
          "connectionMode": "Connection Mode",
          "webhookUrls": "Webhook URLs",
          "webhookUrlsCount": "Webhook URLs ({count} account(s))",
          "accountUrls": {
            "bot": "Bot Mode",
            "agent": "Agent Mode"
          },
          "errors": {
            "accountIdRequired": "Account ID is required",
            "accountIdDuplicate": "Account ID already exists",
            "cannotDeleteLastAccount": "Cannot delete the last account",
            "botIdRequired": "Bot ID is required",
            "botSecretRequired": "Bot Secret is required",
            "corpIdRequired": "CorpID is required",
            "secretRequired": "Secret is required"
          }
        }
      }
    }
  }
}
```

### 9.7 实现优先级

1. **Task 8**: 基础多账号 UI（账号列表、添加/编辑/删除）
2. **Task 9**: Webhook URL 显示（多账号 URL 列表）
3. **Task 10**: 表单适配（多账号 payload 构建）
4. **Task 11**: 中文翻译
5. **Task 12**: 英文翻译

---

## 十、后续阶段预览

- **阶段 B**: 全模态消息支持（图片/文件/语音/视频）
- **阶段 C**: 主动推送能力（部门/标签/群/用户）
- **阶段 D**: 交互卡片（A2UI - template_card）
- **阶段 E**: 定时任务集成（Cronjob + 广播 API）
