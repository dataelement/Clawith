# Phase 2.4 - prompt/schema/test 收口

## 目标

在 Phase 2.1 到 2.3 代码能力完成后，再调整模型可见的工具 schema、持久化 seed schema 和 prompt，让模型默认走 roster-first。

Phase 2.4 只负责“改引导、改描述、补测试”。旧入口和旧参数仍作为兼容兜底保留；删除旧入口、旧提示和旧关系路径放到 Phase 3。

## tool schema 调整

以下 schema 调整要同时覆盖两处：

- 运行时 `backend/app/services/agent_tools.py` 里的 `AGENT_TOOLS`。
- 持久化内置工具 `backend/app/services/tool_seeder.py` 里的 seed 定义，避免已有数据库工具描述仍停留在旧 `member_name` / Relationships 路径。

### query_roster

暴露：

```json
{
  "target_member_id": {
    "type": "string",
    "description": "Optional exact human member ID returned by query_roster. Use this to verify one specific person."
  }
}
```

### send_platform_message

新增：

```json
{
  "target_member_id": {
    "type": "string",
    "description": "Human member ID returned by query_roster."
  },
  "platform_user_id": {
    "type": "string",
    "description": "Platform user ID returned by query_roster."
  }
}
```

`username` 标成 legacy fallback。

如果继续保留 `username`，工具描述必须明确：这是旧参数兜底，新调用应优先使用 `target_member_id` 或 `platform_user_id`。

### send_channel_message

新增：

```json
{
  "target_member_id": {
    "type": "string",
    "description": "Human member ID returned by query_roster."
  }
}
```

`member_name` 标成 legacy fallback。工具描述改为第三方 IM channel 统一入口，由后端按 provider/channel 分发到 Feishu、DingTalk、WeCom、Slack、Teams、WeChat。

`provider_user_id` 如果保留，也只作为 legacy fallback，不作为新主路径向模型推荐。新调用应优先使用 `target_member_id`。

### send_feishu_message

保留为 legacy shortcut，不作为 Phase 2 新主路径。

- prompt 不再主推。
- tool schema 可以暂时不新增 `target_member_id`。
- 如果后续为了兼容内部复用而新增，也必须标成 legacy / shortcut，不应让模型优先选择它。
- `tool_seeder.py` 中的描述也要同步标记为 legacy，不能继续写“Can only message people in your relationships”作为主语义。

## prompt 调整

新增或替换人类联系规则：

```text
When contacting human colleagues:
1. Use query_roster(member_type="human", query="...") first.
2. Use the returned stable IDs.
3. For platform users, call send_platform_message(platform_user_id="..." or target_member_id="...").
4. For third-party channel users, call send_channel_message(target_member_id="...", channel="...").
5. Do not guess names or IDs.
6. Do not use send_feishu_message as the primary path; use send_channel_message for Feishu too.
```

Phase 1.4 保留的 `## 人类同事背景` 可以先保留为背景信息，但不能继续作为发送入口。

明确规则：

- `## 人类同事背景` 只用于理解上下文。
- 联系人类前仍必须重新调用 `query_roster(member_type="human", query="...")`。
- 不能直接拿背景里的名字调用 `send_platform_message(username=...)`、`send_channel_message(member_name=...)` 或 `send_feishu_message(member_name=...)`。
- 非普通 agent prompt 也要检查，例如 `task_executor.py` 里“联系人就用 `send_feishu_message`”这类旧指引要改成 roster-first。

## 测试收口

建议补充或更新：

- `test_query_roster_tool.py`
  - schema 暴露 `target_member_id`
  - 精确查询
  - invalid UUID
  - private/inactive 行为

- `test_a2a_msg_type.py` 或新增 human messaging 测试文件
  - platform ID 发送
  - channel target member ID 发送
  - Feishu 通过 `send_channel_message(..., channel="feishu")` 分发
  - 重名旧参数歧义
  - 跨租户
  - private 限制
  - 缺 provider/channel config

- prompt 测试
  - 不再指导模型直接按 `member_name` 主路径发送。
  - 明确人类联系人也要先 `query_roster`。
  - `## 人类同事背景` 只作为背景存在。

- seed/schema 一致性测试
  - `tool_seeder.py` 中 `send_platform_message` 暴露 `target_member_id` / `platform_user_id`。
  - `tool_seeder.py` 中 `send_channel_message` 暴露 `target_member_id`，并把 `member_name` / `provider_user_id` 标为 legacy fallback。
  - `tool_seeder.py` 中 `send_feishu_message` 描述为 legacy shortcut，不再作为新主路径。

## 不做

- 不要求删除 `## 人类同事背景`。
- 不要求删除旧参数。
- 不删除 `send_feishu_message`。
- 不删除 legacy fallback 的 `username` / `member_name` / `provider_user_id`。
- 不要求修改 OKR 专用 prompt。
- 不要求改 UI。

以上删除和旧路径下线统一放到 Phase 3。
