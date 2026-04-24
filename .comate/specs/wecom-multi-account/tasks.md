# 企业微信多账号支持（Multi-account）任务计划

## 开发策略

采用**增量开发 + 阶段性测试**策略：
1. 每完成 1-2 个 Task 就进行部署测试
2. 优先完成后端 API 和基础功能
3. 最后完成前端 UI 和翻译
4. 每个阶段都邀请用户参与实际测试

---

## 阶段一：后端基础 API 改造（可独立测试）

- [ ] Task 1: 后端配置 API 支持多账号结构
    - 1.1: 修改 `configure_wecom_channel()` API，支持新的多账号配置结构（accounts 对象）
    - 1.2: 实现旧格式到新格式的自动转换逻辑（向后兼容）
    - 1.3: 添加配置验证逻辑（账号 ID 唯一性、至少保留一个账号）
    - 1.4: 修改 WebSocket 客户端启动逻辑，遍历 accounts 中的所有账号

- [ ] Task 2: 新增账号配置查询辅助函数
    - 2.1: 实现 `_get_account_config()` 函数，从 extra_config 中提取指定账号配置
    - 2.2: 实现账号配置解析逻辑，支持 account_id 默认值处理
    - 2.3: 添加错误处理，处理不存在的 account_id 情况

**阶段一测试点**：
- 使用 curl 测试配置保存和读取
- 验证旧格式配置自动转换
- 验证多账号配置保存

---

## 阶段二：Webhook 路由扩展（可独立测试）

- [ ] Task 3: Webhook 路由扩展支持多账号路径
    - 3.1: 新增 GET `/channel/wecom/{agent_id}/bot/{account_id}/webhook` 路由
    - 3.2: 新增 POST `/channel/wecom/{agent_id}/bot/{account_id}/webhook` 路由
    - 3.3: 修改现有路由函数，支持 account_id 参数（默认值 "default"）
    - 3.4: 在 webhook 处理函数中调用 `_get_account_config()` 获取账号配置

- [ ] Task 4: 修改 Webhook URL 查询 API
    - 4.1: 修改 `get_wecom_webhook_url()` 返回格式，支持多账号 URL
    - 4.2: 实现动态生成所有账号的 Webhook URL 逻辑
    - 4.3: 返回包含所有账号 URL 的 JSON 对象（包括默认 URL）

**阶段二测试点**：
- 使用 curl 测试新的 Webhook URL 格式
- 验证不同 account_id 的消息路由
- 验证旧路径向后兼容

---

## 阶段三：WebSocket 多账号支持（可独立测试）

- [ ] Task 5: WeComStreamManager 支持多账号客户端管理
    - 5.1: 修改 `WeComStreamManager` 的 `_clients` 和 `_tasks` 数据结构，使用 (agent_id, account_id) 作为键
    - 5.2: 更新 `start_client()` 方法，接收 account_id 参数
    - 5.3: 修改 `stop_client()` 方法，接收 account_id 参数
    - 5.4: 更新 `status()` 方法，返回所有账号的连接状态

- [ ] Task 6: 实现多账号 WebSocket 客户端启动逻辑
    - 6.1: 修改 `start_all()` 方法，遍历 extra_config.accounts 中的所有账号
    - 6.2: 在数据库会话关闭前提取所有账号配置数据（agent_id, account_id, bot_id, bot_secret），避免懒加载问题
    - 6.3: 使用提取的数据列表启动 WebSocket 客户端，避免访问已关闭会话的 ORM 对象
    - 6.4: 添加账号过滤逻辑，仅启动 connection_mode 为 "websocket" 的账号
    - 6.5: 添加启动日志，显示已启动的账号列表

- [ ] Task 7: WebSocket 消息处理支持 account_id
    - 7.1: 修改 `_process_wecom_stream_message()` 函数，接收 account_id 参数
    - 7.2: 在 WebSocket 消息处理器中传递 account_id
    - 7.3: 更新会话 ID 生成逻辑，包含 account_id 信息（可选，用于区分）
    - 7.4: 添加日志，记录 account_id

**阶段三测试点**：
- 配置多个 WebSocket 账号，验证都能连接
- 测试不同账号的消息接收
- 验证账号隔离

---

## 阶段四：前端多账号 UI（可独立测试）

- [ ] Task 8: 前端 ChannelConfig 组件支持多账号 UI
    - 8.1: 修改 WeCom channel 配置，添加账号列表显示区域
    - 8.2: 实现 "Add Account" 按钮，弹出账号添加表单
    - 8.3: 实现账号编辑功能（Edit 按钮）
    - 8.4: 实现账号删除功能（Delete 按钮）
    - 8.5: 添加表单验证（账号 ID 唯一性、必填字段）
    - 8.6: 实现删除最后一个账号的限制逻辑

- [ ] Task 9: 前端 Webhook URL 显示区域
    - 9.1: 修改 Webhook URL 显示逻辑，支持多账号 URL 列表
    - 9.2: 使用可展开/折叠的方式显示所有账号 URL
    - 9.3: 为每个 URL 添加复制按钮（使用现有的 LinearCopyButton 组件）
    - 9.4: 添加账号 ID 标签，便于区分

- [ ] Task 10: 前端配置表单适配
    - 10.1: 修改表单提交逻辑，支持多账号结构（accounts 对象）
    - 10.2: 实现单账号配置的向后兼容逻辑（仅配置 bot_id/bot_secret 时自动转换为 accounts.default）
    - 10.3: 添加 account_id 字段输入框
    - 10.4: 更新 API 调用，传递新的配置结构

**阶段四测试点**：
- 验证前端界面显示正常
- 测试添加/编辑/删除账号功能
- 验证 Webhook URL 显示

---

## 阶段五：翻译和最终测试

- [ ] Task 11: 添加中文翻译
    - 11.1: 在 `frontend/src/i18n/zh.json` 中添加多账号相关的翻译 key
    - 11.2: 添加翻译：账号管理、添加账号、编辑账号、删除账号、账号 ID 等
    - 11.3: 添加错误提示翻译：账号 ID 重复、不能删除最后一个账号等

- [ ] Task 12: 添加英文翻译
    - 12.1: 在 `frontend/src/i18n/en.json` 中添加多账号相关的翻译 key
    - 12.2: 翻译所有新增的中文 key
    - 12.3: 确保翻译准确，符合项目风格

- [ ] Task 13: 集成测试和 Bug 修复
    - 13.1: 完整功能测试（配置、Webhook、WebSocket）
    - 13.2: 向后兼容性测试
    - 13.3: 多账号并发测试
    - 13.4: 修复发现的问题

**阶段五测试点**：
- 完整功能测试
- 用户实际使用测试
- 性能测试

---

## 测试检查清单

### 每个阶段都需要验证：
- [ ] 代码可以正常编译/运行
- [ ] 没有破坏现有功能
- [ ] 日志输出正常
- [ ] 错误处理完善

### 最终验收标准：
- [ ] 支持配置多个企业微信账号
- [ ] Webhook 路径区分不同账号
- [ ] WebSocket 支持多账号并行连接
- [ ] 向后兼容旧的单账号配置
- [ ] UI 符合 Clawith 项目风格（无 emoji，使用 Tabler 图标）
- [ ] 中英文翻译完整
