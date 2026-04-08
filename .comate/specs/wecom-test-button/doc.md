# WeCom 测试按钮与消息调试功能

## 问题描述

用户在使用企业微信多模式支持时遇到两个核心问题：

1. **缺少测试按钮**：用户输入 Token + EncodingAESKey 后，需要先保存才能看到 Webhook URL，这导致用户必须：
   - 保存 → 获取 URL → 去企业微信验证 → 回来填 Bot ID → 再次保存
   - 如果验证失败，需要删除重新配置

2. **消息无响应**：配置完成后，企业微信发送消息没有任何反应

## 需求场景

### 智能机器人-短链接 配置流程

用户期望的流程：
1. 在企业微信获取 Token + EncodingAESKey
2. 在 Clawith 填写 Token + EncodingAESKey
3. 点击**测试按钮**（或自动预览），显示 Webhook URL
4. 复制 URL 到企业微信配置页面
5. 企业微信验证成功后显示 Bot ID
6. 在 Clawith 填写 Bot ID 并保存

### 消息接收问题排查

需要确认：
1. WebSocket 长连接是否正常启动
2. Webhook 端点是否正常接收请求
3. 消息处理流程是否正确

## 技术方案

### 1. 添加测试按钮功能

**修改文件**: `frontend/src/components/WeComAccountManager.tsx`

**实现方案**:
- 添加 `previewWebhookUrl` 状态来显示临时的 Webhook URL 预览
- 当用户填写 Token + EncodingAESKey 后，显示测试按钮
- 点击测试按钮：
  - 生成临时账户 ID（如果还没有）
  - 显示 Webhook URL 预览
  - 不需要调用后端保存

**UI 变化**:
```
┌─────────────────────────────────────────────────────────────┐
│ 智能机器人-短链接                                           │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Token:          [________________________]              │ │
│ │ EncodingAESKey: [________________________]              │ │
│ │ Bot ID (验证后): [________________________]             │ │
│ │                                                         │ │
│ │ [测试按钮]                                              │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│ ┌─── 点击测试后显示 ───────────────────────────────────────┐ │
│ │ Webhook URL:                                            │ │
│ │ https://xxx/api/channel/wecom/{agent_id}/bot/{id}/webhook│ │
│ │                                          [复制]         │ │
│ └─────────────────────────────────────────────────────────┘ │
│                                                             │
│                                    [保存账号]               │
└─────────────────────────────────────────────────────────────┘
```

### 2. 修复消息接收问题

**修改文件**: `backend/app/api/wecom.py`

**检查项**:
1. 确认 WebSocket 客户端在保存后正确启动
2. 确认 webhook 端点路由正确注册
3. 添加更详细的日志记录

**可能的问题**:
- 数据保存后没有触发 WebSocket 客户端启动
- Webhook URL 路由参数不匹配
- 消息解密失败

### 3. 数据流分析

```
前端保存 → POST /api/agents/{id}/wecom-channel
         → 后端验证数据格式
         → 保存到 channel_config 表
         → 启动 WebSocket 客户端（如果启用）
         
企业微信发消息 → POST /api/channel/wecom/{agent_id}/bot/{account_id}/webhook
              → 验证签名
              → 解密消息
              → 处理消息并回复
```

## 受影响文件

| 文件 | 修改类型 | 说明 |
|------|----------|------|
| `frontend/src/components/WeComAccountManager.tsx` | 修改 | 添加测试按钮和 URL 预览功能 |
| `backend/app/api/wecom.py` | 修改 | 添加日志、检查启动逻辑 |
| `backend/app/services/wecom_stream.py` | 检查 | WebSocket 客户端启动逻辑 |

## 实现细节

### 测试按钮逻辑

```typescript
// WeComAccountManager.tsx 新增

const [previewUrl, setPreviewUrl] = useState<string | null>(null);

const handleTestWebhook = () => {
    // 生成临时 ID（如果没有）
    const tempId = formData.id || generateAccountId();
    setFormData({ ...formData, id: tempId });
    
    // 生成预览 URL
    const url = `${window.location.origin}/api/channel/wecom/${agentId}/bot/${tempId}/webhook`;
    setPreviewUrl(url);
};
```

### 保存按钮逻辑

保存时应该：
1. 如果已经有 `previewUrl`，使用预览时生成的 ID
2. 调用 `onAccountsChange` 触发实际保存
3. 保存成功后关闭模态框

### 后端日志增强

```python
# wecom.py 中添加详细日志

@router.post("/agents/{agent_id}/wecom-channel")
async def configure_wecom_channel(...):
    logger.info(f"[WeCom] Configuring channel for agent {agent_id}")
    logger.info(f"[WeCom] Accounts: {list(accounts.keys())}")
    # ... 保存逻辑
    logger.info(f"[WeCom] Channel saved, starting WebSocket clients...")
```

## 边界条件

1. 用户多次点击测试按钮：应使用同一个临时 ID
2. 用户取消编辑：清除预览状态
3. 用户保存后重新编辑：应使用已保存的账户 ID

## 预期结果

1. 用户可以在不保存的情况下预览 Webhook URL
2. 保存后数据正确存储到数据库
3. WebSocket 长连接正常启动
4. Webhook 消息正确接收和处理