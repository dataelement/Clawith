# 中文国际化文件优化报告

## 优化时间
2026-03-20

## 优化内容

### 1. 补充缺失的翻译条目

| 条目路径 | 新增翻译 |
|---------|----------|
| dashboard.status.disconnected | 已断开 |
| agent.status.disconnected | 已断开 |
| agent.workspace.newFile | 新建文件 |
| agent.tools.platformTools | 平台预置工具 |
| agent.tools.agentInstalled | 数字员工自行安装的工具 |
| agent.toolCategories.file | 文件操作 |
| agent.toolCategories.task | 任务管理 |
| agent.toolCategories.communication | 通讯 |
| agent.toolCategories.search | 搜索 |
| agent.toolCategories.custom | 自定义 |
| agent.toolCategories.general | 通用 |
| agent.toolCategories.email | 邮件 |
| agent.upload.success | 上传成功 |
| agent.upload.failed | 上传失败 |
| agent.upload.uploading | 上传中... |
| wizard.step5.connectionMode | 连接模式 |
| wizard.step5.modeWebsocket | 长连接 (WebSocket) - 推荐 |
| wizard.step5.modeWebhook | Webhook 回调 |
| wizard.errors.nameRequired | 智能体名称不能为空 |
| wizard.errors.nameTooShort | 名称至少需要 2 个字符 |
| wizard.errors.nameTooLong | 名称不能超过 100 个字符 |
| wizard.errors.roleDescTooLong | 角色描述不能超过 500 个字符（当前 {{count}} 字符） |
| wizard.errors.tokenLimitInvalid | 请输入有效的正整数 |
| wizard.errors.modelRequired | 请选择一个主模型 |

### 2. 统一术语翻译标准

| 原术语 | 统一后术语 | 应用位置 |
|-------|----------|----------|
| 通道 | 渠道 | wizard.step5.title, wizard.step5.description |
| Agent 自行安装的工具 | 数字员工自行安装的工具 | agent.tools.agentInstalled |

### 3. 优化长文本排版

| 原文本 | 优化后文本 |
|-------|-----------|
| 技能定义了数字员工在特定场景下的行为方式。每个 .md 文件是一个技能。建议使用 YAML frontmatter（name + description）来定义技能元数据。 | 技能定义了数字员工在特定场景下的行为方式。每个.md文件是一个技能。建议使用YAML frontmatter（name + description）来定义技能元数据。 |

### 4. 验证结果

- ✅ 中文国际化文件完整性检查：无缺失条目
- ✅ 术语一致性：已统一
- ✅ 文本排版：已优化
- ✅ 冗余条目：无需要移除的内容

## 技术实现

- 使用 i18next 作为国际化框架
- 支持中文和英文两种语言
- 自动检测用户语言偏好
- 本地缓存语言设置

## 下一步建议

1. 定期检查国际化文件的完整性
2. 建立术语翻译标准文档
3. 考虑支持更多语言
4. 优化翻译质量，确保表达自然准确