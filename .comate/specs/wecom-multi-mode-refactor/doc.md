# WeCom 多模式支持重构方案

## 背景

当前系统粗暴地区分 WebSocket 模式和 Webhook URL 模式，没有考虑到企业微信实际存在的三种通讯方式：

| 方式 | 入口 | 连接类型 | 必需配置 | 标识符 | 消息接收 | 消息回复 |
|------|------|----------|----------|--------|----------|----------|
| **智能机器人-长连接** | 安全与管理→智能机器人 | WebSocket | Bot ID + Bot Secret | Bot ID | 长连接自动接收 | 通过同一连接回复 |
| **智能机器人-短链接** | 安全与管理→智能机器人 | Webhook | Bot ID + Bot Secret + Token + AESKey + URL | Bot ID | 企业微信 POST 到 URL | 通过企业微信 API 发送 |
| **企业应用** | 应用管理→创建应用 | Webhook | CorpID + AgentID + Secret + Token + AESKey + URL | AgentID | 企业微信 POST 到 URL | 通过企业微信 API 发送 |

## 目标

1. 支持三种通讯方式的独立配置
2. 同一账号可配置多种方式（智能机器人长连接 + 短链接 + 企业应用）
3. 每种连接方式可独立启用/禁用
4. Webhook URLs 按配置类型分组显示
5. 保持向后兼容

## 数据结构设计

### 新的 WeComAccount 接口

```typescript
interface WeComAccount {
    id: string;                    // 系统生成: wecom_xxx
    nickname: string;              // 用户定义昵称
    
    // 智能机器人配置（长连接和短链接共用 Bot ID）
    bot?: {
        id: string;                // Bot ID
        secret: string;            // Bot Secret
        // 短链接专用配置（可选）
        token?: string;
        encoding_aes_key?: string;
    };
    
    // 企业应用配置
    agent?: {
        corp_id: string;
        agent_id: string;          // 企业微信应用 AgentID
        secret: string;
        token: string;
        encoding_aes_key: string;
    };
    
    // 连接状态控制
    bot_websocket_enabled: boolean;  // 智能机器人长连接开关
    bot_webhook_enabled: boolean;    // 智能机器人短链接开关
    agent_webhook_enabled: boolean;  // 企业应用开关
}
```

### 数据库存储格式 (extra_config.accounts)

```json
{
    "wecom_abc123": {
        "nickname": "客服机器人",
        "bot": {
            "id": "aibXXXXXXXXXXXX",
            "secret": "xxx",
            "token": "xxx",
            "encoding_aes_key": "xxx"
        },
        "agent": {
            "corp_id": "wwXXX",
            "agent_id": "100001",
            "secret": "xxx",
            "token": "xxx",
            "encoding_aes_key": "xxx"
        },
        "bot_websocket_enabled": true,
        "bot_webhook_enabled": false,
        "agent_webhook_enabled": true
    }
}
```

## Webhook URL 格式

```
# 智能机器人短链接
/api/channel/wecom/{agent_id}/bot/{account_id}/webhook

# 企业应用
/api/channel/wecom/{agent_id}/agent/{account_id}/webhook
```

## 修改文件清单

### 前端

#### 1. `WeComAccountManager.tsx` (主要修改)
**修改内容：**
- 重构表单，分为三个配置区块：
  - 智能机器人配置（Bot ID + Secret，Token + AESKey 可选）
  - 企业应用配置（CorpID + AgentID + Secret + Token + AESKey）
- 添加三个开关控制：`bot_websocket_enabled`、`bot_webhook_enabled`、`agent_webhook_enabled`
- 重构账号列表显示，显示已启用的连接类型标签
- Webhook URLs 按类型分组显示，只在对应配置启用时显示

**UI 结构：**
```
┌─────────────────────────────────────────────────────────────┐
│ 账号列表 (2)                                    [+ 添加账号] │
├─────────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ 客服机器人                                               │ │
│ │ [WS] [Bot-Webhook] [Agent-Webhook]     [编辑] [删除]    │ │
│ │ Bot: aibXXX...                                          │ │
│ └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Webhook URLs                                                │
├─────────────────────────────────────────────────────────────┤
│ ▼ 智能机器人 (1)                                            │
│   客服机器人                                                │
│   Bot: http://xxx/api/channel/wecom/xxx/bot/wecom_xxx/webhook│
├─────────────────────────────────────────────────────────────┤
│ ▼ 企业应用 (1)                                              │
│   客服机器人                                                │
│   Agent: http://xxx/api/channel/wecom/xxx/agent/wecom_xxx/webhook│
└─────────────────────────────────────────────────────────────┘
```

#### 2. `ChannelConfig.tsx` (数据映射修改)
**修改内容：**
- 更新 `WeComAccount` 类型导入
- 更新账号数据解析逻辑，适配新的嵌套结构
- 更新账号数据保存逻辑

### 后端

#### 3. `wecom.py` (API 处理修改)
**修改内容：**

##### 3.1 `_get_account_config()` 函数
- 返回新的嵌套结构

##### 3.2 `get_wecom_webhook_url()` 函数
- 返回两种类型的 Webhook URL：
```python
{
    "bot": {
        "wecom_xxx": "http://xxx/api/channel/wecom/{agent_id}/bot/wecom_xxx/webhook"
    },
    "agent": {
        "wecom_xxx": "http://xxx/api/channel/wecom/{agent_id}/agent/wecom_xxx/webhook"
    }
}
```

##### 3.3 Webhook 路由
- 新增 `/api/channel/wecom/{agent_id}/bot/{account_id}/webhook` - 智能机器人短链接
- 新增 `/api/channel/wecom/{agent_id}/agent/{account_id}/webhook` - 企业应用
- 保留旧路由向后兼容

##### 3.4 `_process_wecom_text()` 函数
- 根据 `bot_websocket_enabled`、`bot_webhook_enabled`、`agent_webhook_enabled` 决定回复方式
- 智能机器人回复：使用 Bot API
- 企业应用回复：使用企业微信应用 API

#### 4. `wecom_stream.py` (WebSocket 连接管理)
**修改内容：**

##### 4.1 数据结构
- 更新 `_clients` 和 `_tasks` 的配置存储格式
- 存储 `bot.id` 和 `bot.secret`

##### 4.2 `start_client()` 函数
- 检查 `bot_websocket_enabled` 是否启用
- 只启动启用了长连接的账号

##### 4.3 `start_all()` 函数
- 遍历所有账号，检查 `bot_websocket_enabled` 后启动

## 向后兼容

### 旧数据格式
```json
{
    "accounts": {
        "default": {
            "bot_id": "aibXXX",
            "bot_secret": "xxx",
            "connection_mode": "websocket"
        }
    }
}
```

### 迁移逻辑
```python
def migrate_account_config(old_config):
    """将旧格式转换为新格式"""
    return {
        "nickname": "默认机器人",
        "bot": {
            "id": old_config.get("bot_id", ""),
            "secret": old_config.get("bot_secret", ""),
        },
        "bot_websocket_enabled": old_config.get("connection_mode") == "websocket",
        "bot_webhook_enabled": old_config.get("connection_mode") == "webhook",
        "agent_webhook_enabled": False,
    }
```

## 实施步骤

### 阶段 1：后端数据结构重构
1. 修改 `_get_account_config()` 支持新格式
2. 添加向后兼容的迁移逻辑
3. 更新 `get_wecom_webhook_url()` 返回新格式
4. 添加新的 Webhook 路由

### 阶段 2：后端消息处理
1. 更新 `_process_wecom_text()` 根据配置回复
2. 更新 `_wecom_event_webhook_handler()` 处理新路由
3. 更新 WebSocket 客户端管理

### 阶段 3：前端 UI 重构
1. 重构 `WeComAccountManager.tsx` 表单
2. 更新账号列表显示
3. 更新 Webhook URLs 显示

### 阶段 4：测试与验证
1. 测试向后兼容性
2. 测试三种连接方式独立配置
3. 测试消息收发

## 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 旧数据不兼容 | 现有配置失效 | 实现自动迁移逻辑 |
| UI 复杂度增加 | 用户困惑 | 提供清晰的配置说明 |
| 后端路由冲突 | 请求处理错误 | 保留旧路由向后兼容 |
| WebSocket 连接管理复杂化 | 连接状态不一致 | 完善状态同步机制 |

## 预估工作量

- 后端修改：约 200 行代码
- 前端修改：约 300 行代码
- 测试验证：约 1 小时

## 待确认事项

1. 是否需要支持同一账号同时启用智能机器人长连接和短链接？
是的,需要支持同一账号同时启用智能机器人长连接和短链接
2. 企业应用的回调 URL 验证逻辑是否与智能机器人短链接相同？
目前情况来看，我不确定，需要实际测试，可能不同，因为智能机器人短链接是通过botID进行验证的，而企业应用是通过AgentId进行验证的，
3. 是否需要在 UI 上显示每种连接方式的状态（已连接/未连接）？
是的，需要在 UI 上显示每种连接方式的状态（已连接/未连接）