# Clawith 群聊

这个目录是 Clawith 群聊功能的本地工作区。

当前文档：

- [prd.md](prd.md)：群聊 v1 产品开发基线，记录已确定规则、边界和后续规划。
- [group-collaboration-mechanisms.md](group-collaboration-mechanisms.md)：在 PRD 基础上后续逐项确认的 Planning v2、自组织公开交接，以及共享 Runtime ADR 对 Group 的继承边界；其明确修订项优先于旧技术实现描述。
- [technical-design.md](technical-design.md)：群聊 v1 技术设计基线，包含技术建模、上下文构造、LangGraph 主 Runtime、群 workspace 和落地分期；与已确认 Runtime ADR 或协作机制修订冲突的段落需要随实现同步更新。
- [chat-model-refactor.md](chat-model-refactor.md)：统一 `chat_sessions`、`chat_messages` 和群聊天数据模型的规范来源。
- [context-compression.md](context-compression.md)：群上下文压缩与加载策略调研记录；最终实现以技术设计为准。
- [agent-mention-queue-research.md](agent-mention-queue-research.md)：Agent 被 @ 后的异步执行与队列处理历史调研；最终实现以技术设计为准。

固定开发顺序：Schema/迁移与依赖基线 → Single 后端 → Group 后端 → 后端整体验证与旧循环清理 → 前端统一更新。

文档冲突时的顺序：当前明确产品决策 → `prd.md` → 已确认的协作机制修订 → 共享 Runtime ADR → 技术设计与审计记录。技术审计可以发现实现风险，但不能自行改变 PRD 产品语义。
