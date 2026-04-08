# WeCom 多模式支持重构任务计划

## 阶段 1：后端数据结构重构

- [x] Task 1: 更新 `_get_account_config()` 支持新格式并添加迁移逻辑
    - 1.1: 修改函数返回新的嵌套结构
    - 1.2: 添加旧格式到新格式的迁移逻辑
    - 1.3: 测试向后兼容性

- [x] Task 2: 更新 `get_wecom_webhook_url()` 返回新格式
    - 2.1: 返回 bot 和 agent 两种类型的 Webhook URL
    - 2.2: 只返回已启用的连接类型 URL

- [x] Task 3: 添加新的 Webhook 路由
    - 3.1: 添加 `/bot/{account_id}/webhook` 路由（智能机器人短链接）
    - 3.2: 添加 `/agent/{account_id}/webhook` 路由（企业应用）
    - 3.3: 保留旧路由向后兼容

## 阶段 2：后端消息处理

- [x] Task 4: 更新 `_process_wecom_text()` 消息回复逻辑
    - 4.1: 根据配置决定使用 Bot API 还是企业应用 API
    - 4.2: 支持智能机器人短链接回复
    - 4.3: 支持企业应用回复

- [x] Task 5: 更新 `_wecom_event_webhook_handler()` 处理新路由
    - 5.1: 区分 bot 和 agent 类型的请求
    - 5.2: 使用对应的配置验证签名和解密消息

- [x] Task 6: 更新 WebSocket 连接管理 `wecom_stream.py`
    - 6.1: 更新数据结构存储 bot 配置
    - 6.2: 检查 `bot_websocket_enabled` 决定是否启动连接
    - 6.3: 更新 `start_all()` 遍历逻辑

## 阶段 3：前端 UI 重构

- [x] Task 7: 重构 `WeComAccountManager.tsx` 数据类型
    - 7.1: 定义新的 `WeComAccount` 接口（嵌套 bot/agent 对象）
    - 7.2: 定义三个启用开关：bot_websocket_enabled, bot_webhook_enabled, agent_webhook_enabled

- [x] Task 8: 重构账号表单 UI
    - 8.1: 添加三种通信模式复选框（独立启用）
    - 8.2: 智能机器人配置区块（Bot ID + Secret，Webhook模式需要 Token + AESKey）
    - 8.3: 企业应用配置区块（CorpID + AgentID + Secret + Token + AESKey）

- [x] Task 9: 更新账号列表显示
    - 9.1: 显示已启用的通信模式标签
    - 9.2: 显示账号昵称

- [x] Task 10: 更新 Webhook URLs 显示
    - 10.1: 按类型分组（智能机器人、企业应用）
    - 10.2: 只显示已启用且有完整配置的 URL

- [x] Task 11: 更新 `ChannelConfig.tsx` 数据映射
    - 11.1: 更新账号数据解析逻辑（从嵌套结构解析）
    - 11.2: 更新账号数据保存逻辑（保存到嵌套结构）
    - 11.3: 适配新的 webhookUrls 格式

## 阶段 4：测试与验证

- [x] Task 12: 构建部署并测试
    - 12.1: 前端构建无错误 ✅
    - 12.2: 后端语法检查通过 ✅
    - 12.3: 向后兼容性通过迁移逻辑实现
    - 12.4: 需要用户实际测试新配置功能