# WeCom 测试按钮与消息调试任务计划

- [x] Task 1: 添加测试按钮和 Webhook URL 预览功能
    - 1.1: 在 WeComAccountManager 中添加 `previewUrl` 和 `tempAccountId` 状态
    - 1.2: 实现测试按钮点击处理函数 `handleTestWebhook`
    - 1.3: 在 bot_webhook_enabled 模式下显示测试按钮
    - 1.4: 点击测试后显示 Webhook URL 预览区域
    - 1.5: 保存时使用预览生成的临时 ID（如有）

- [x] Task 2: 增强后端日志和调试信息
    - 2.1: 在 wecom.py 的 configure_wecom_channel 添加详细日志
    - 2.2: 记录收到的账户配置信息
    - 2.3: 记录 WebSocket 客户端启动状态

- [x] Task 3: 检查并修复 WebSocket 客户端启动逻辑
    - 3.1: 检查 wecom_stream.py 中的客户端启动代码
    - 3.2: 确保保存配置后正确触发客户端启动
    - 3.3: 添加启动失败的错误处理和日志

- [x] Task 4: 验证 Webhook 端点路由
    - 4.1: 检查 webhook 端点路由定义
    - 4.2: 确认 URL 参数与前端生成的 URL 匹配
    - 4.3: 添加 webhook 请求接收日志

- [x] Task 5: 测试完整流程
    - 5.1: 重新构建并部署前端
    - 5.2: 测试测试按钮功能
    - 5.3: 测试保存功能
    - 5.4: 检查日志确认消息接收