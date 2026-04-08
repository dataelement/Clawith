# WeCom 多模式支持重构总结

## 完成时间
2026-04-08

## 重构目标
将 WeCom（企业微信）从简单的 WebSocket/Webhook 二选一模式，重构为支持三种独立可配置的通信模式：
1. **智能机器人-长连接**：通过 WebSocket 自动接收消息
2. **智能机器人-短链接**：通过 Webhook URL 接收消息
3. **企业应用**：企业内部应用，通过 Webhook URL 接收消息

## 数据结构变更

### 旧格式
```json
{
  "wecom_xxx": {
    "nickname": "客服机器人",
    "connection_mode": "websocket",
    "bot_id": "aibXXX",
    "bot_secret": "xxx"
  }
}
```

### 新格式
```json
{
  "wecom_xxx": {
    "nickname": "客服机器人",
    "bot_websocket_enabled": true,
    "bot_webhook_enabled": false,
    "agent_webhook_enabled": false,
    "bot": {
      "id": "aibXXX",
      "secret": "xxx",
      "token": "",
      "encoding_aes_key": ""
    },
    "agent": {
      "corp_id": "",
      "agent_id": "",
      "secret": "",
      "token": "",
      "encoding_aes_key": ""
    }
  }
}
```

## 修改的文件

### 后端
| 文件 | 修改内容 |
|------|----------|
| `backend/app/api/wecom.py` | 新增 `_migrate_account_config()` 迁移函数；更新 `_get_account_config()` 支持新格式；更新 `get_wecom_webhook_url()` 返回分组 URL；更新 `_process_wecom_text()` 消息回复逻辑；更新 webhook 处理器支持新路由 |
| `backend/app/services/wecom_stream.py` | 更新 `start_all()` 从嵌套 `bot` 对象获取凭证；检查 `bot_websocket_enabled` 标志 |

### 前端
| 文件 | 修改内容 |
|------|----------|
| `frontend/src/components/WeComAccountManager.tsx` | 重构数据类型定义；重构表单 UI 支持三种模式复选框；添加嵌套 bot/agent 配置区块；更新 Webhook URLs 显示逻辑 |
| `frontend/src/components/ChannelConfig.tsx` | 更新账号数据解析逻辑；更新账号数据保存逻辑 |

## 关键特性

### 1. 向后兼容
- 后端 `_migrate_account_config()` 自动将旧格式转换为新格式
- 旧配置在加载时透明迁移，用户无感知

### 2. Webhook URL 分组
```json
{
  "bot": {
    "wecom_xxx": "https://example.com/api/channel/wecom/{agent_id}/bot/wecom_xxx/webhook"
  },
  "agent": {
    "wecom_xxx": "https://example.com/api/channel/wecom/{agent_id}/agent/wecom_xxx/webhook"
  }
}
```

### 3. 消息回复逻辑
- 智能机器人消息：使用 Bot ID + Secret 调用机器人 API
- 企业应用消息：使用 CorpID + AgentID + Secret 调用企业应用 API

### 4. 独立启用控制
每个账号可以独立启用任意组合的通信模式：
- 只启用长连接（最简单）
- 同时启用长连接 + 短链接（双通道）
- 只启用企业应用
- 三种模式同时启用

## 构建验证
- ✅ 前端 TypeScript 编译通过
- ✅ 前端 Vite 构建成功
- ✅ 后端 Python 语法检查通过

## 待测试项
1. 创建新账号，测试三种模式的配置保存
2. 测试 WebSocket 长连接接收消息
3. 测试 Webhook 短链接接收消息
4. 测试企业应用接收消息
5. 验证旧配置自动迁移正常工作