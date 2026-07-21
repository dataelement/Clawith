# Agent 通讯录 Phase 1.2 - query_roster 工具设计

## 目标

Phase 1.2 在 Phase 1.1 的权限拆分基础上，新增内置工具 `query_roster`。

目标是让 Agent 不再依赖 system prompt 中预先拼接的完整 Relationships 列表，而是在需要找同事、推荐同事、联系同事时，实时查询自己可见的通讯录对象。

本阶段只实现查询工具本身，不改 `send_message_to_agent` 的目标参数，也不移除 system prompt 中的完整 Relationships 列表。发送链路 ID 化和 prompt 瘦身放到 Phase 1.3 / Phase 1.4。

## 前置依赖

Phase 1.2 依赖 Phase 1.1 已完成或同步实现的判断函数：

- `evaluate_roster_agent_visibility(source_agent, target_agent)`
- `evaluate_roster_human_visibility(source_agent, member)`

`query_roster` 不应该自己重新实现一套权限规则。它只消费 Phase 1.1 的可见性判断结果。

## 工具定义

```json
{
  "name": "query_roster",
  "description": "Query the people and digital employees this agent can see in its roster. Use this before recommending or contacting a colleague.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Optional fuzzy search keyword for name, role, title, department, or skill."
      },
      "member_type": {
        "type": "string",
        "enum": ["all", "agent", "human"],
        "description": "Filter by member type. Defaults to all."
      },
      "include_uncontactable": {
        "type": "boolean",
        "description": "Whether to include members that are visible but currently unavailable. Defaults to false. This never returns invisible members."
      },
      "limit": {
        "type": "integer",
        "minimum": 1,
        "maximum": 50,
        "description": "Maximum number of members to return. Defaults to 20."
      },
      "offset": {
        "type": "integer",
        "minimum": 0,
        "description": "Number of matching members to skip. Defaults to 0."
      }
    },
    "required": []
  }
}
```

### 参数规则

- `query` 为空时返回可见通讯录的默认排序结果。
- `member_type` 默认 `all`。
- `include_uncontactable` 默认 `false`。
- `limit` 默认 `20`，最大 `50`。
- `offset` 默认 `0`。
- 所有参数错误都应返回结构化错误，不抛给模型一段 Python 异常。

## 返回结构

工具返回 JSON 字符串。顶层结构：

```json
{
  "ok": true,
  "source_agent_id": "agent_uuid",
  "query": "okr",
  "member_type": "all",
  "include_uncontactable": false,
  "returned_count": 2,
  "limit": 20,
  "offset": 0,
  "has_more": false,
  "members": []
}
```

### Agent 成员

```json
{
  "member_type": "agent",
  "target_agent_id": "agent_uuid",
  "display_name": "OKR 助手",
  "role_description": "帮助团队收集、整理和追踪 OKR 进展",
  "capabilities": [],
  "department": null,
  "skills": [],
  "access_mode": "company",
  "can_contact": true,
  "contact_tools": ["send_message_to_agent"],
  "unavailable_reason": null
}
```

字段说明：

- `target_agent_id` 使用现有 `agents.id`，不新增 ID。模型后续调用 `send_message_to_agent` / `send_file_to_agent` 时直接使用这个字段。
- `display_name` 使用 `Agent.name`。
- `role_description` 使用 `Agent.role_description`。
- `capabilities` 第一版可以为空数组；后续可从模板能力、技能、工具或人工维护字段补充。它用于帮助模型判断该找谁协作。
- `department` 第一版允许为 `null`，因为当前 Agent 模型还没有正式的花名册部门字段。
- `skills` 第一版可以为空数组；后续可从技能绑定或工具能力里补。
- `access_mode` 返回 `company` / `custom` / `private`。
- `can_contact=false` 时，`contact_tools` 必须为空数组。
- `unavailable_reason` 使用稳定枚举。Agent V1 可返回 `agent_stopped`、`agent_error`、`agent_expired`，可联系时返回 `null`。
- V1 不返回 Agent 原始 `status` 字段。工具结果只暴露 `can_contact` 和 `unavailable_reason`；真正发送时由 `send_message_to_agent` / `send_file_to_agent` 再做一次硬校验。

### Human 成员

```json
{
  "member_type": "human",
  "target_member_id": "org_member_uuid",
  "platform_user_id": "user_uuid_or_null",
  "display_name": "张三",
  "title": "产品经理",
  "department": {
    "id": "department_uuid",
    "name": "产品部"
  },
  "can_contact": true,
  "contact_tools": ["send_platform_message", "send_feishu_message"],
  "provider": {
    "provider_id": "provider_uuid_or_null",
    "provider_type": "feishu",
    "open_id": "ou_xxx_or_null",
    "external_id": "user_xxx_or_null"
  },
  "unavailable_reason": null
}
```

字段说明：

- `target_member_id` 第一版使用 `org_members.id`。它只用于标识 roster 结果中的人类成员；Phase 1.2 不要求人类发送工具改成 `target_member_id` 参数。
- `platform_user_id` 使用 `OrgMember.user_id`。如果该人类成员已映射到平台账号，后续平台内消息应优先使用这个 ID，而不是名字。
- `display_name` 使用 `OrgMember.name`。
- `title` 使用 `OrgMember.title`。
- `department.id` 使用 `OrgMember.department_id`。
- `department.name` 优先从关联部门取；没有关联部门时可用 `OrgMember.department_path` 兜底生成一个简单名称，或返回 `null`。
- V1 不返回 `department.path`。后续做部门层级或完整组织花名册时再加入。
- `provider` 表示第三方组织来源身份，来自 `OrgMember.provider_id -> IdentityProvider.provider_type` 以及 `OrgMember.open_id/external_id`。飞书、钉钉、企业微信、Teams 等都使用这一套通用结构，不新增 `feishu_open_id`、`dingtalk_user_id` 这类 channel 专属字段。没有第三方来源时可以返回 `null`。V1 不返回 `provider_name`，展示名称由前端或调用方根据 `provider_type` 映射。
- V1 不返回 `unionid`。`unionid` 主要用于跨应用身份合并和去重，给模型直接使用的价值不高；后续 V2 如需做身份合并解释或高级去重再考虑暴露。
- `contact_tools` 根据平台账号、`provider.provider_type`、可用 ID 和当前已启用工具共同推导：
  - 有 `user_id` 时可以包含 `send_platform_message`。
  - `provider_type="feishu"` 且存在 `external_id` 或 `open_id`，并且工具可用时，可以包含 `send_feishu_message`。
  - 如果两个条件都满足，固定按 `send_platform_message`、`send_feishu_message` 的顺序返回。
  - 钉钉、企业微信、Teams 等其它 provider 第一版不返回联系工具，等对应发送工具和参数校验完成配套后再开放。
- `provider_type` 只能说明这个成员来自哪个第三方系统，不能单独保证可发送；还需要有可用的目标 ID，并且对应发送工具在当前 Agent 的工具集中可用。
- 名字只用于展示和搜索，不应用作最终鉴权或唯一定位。
- 第一版不要求把 human 发送工具全部改成 ID 参数；`query_roster` 先提供可见对象、可用工具提示和稳定身份。后续发送工具 ID 化时应优先消费这些字段，避免重名误发。
- `unavailable_reason` 使用稳定枚举。Human V1 可返回 `member_inactive`、`missing_contact_target`、`contact_tool_unavailable`，可联系时返回 `null`。
- V1 不返回 human 原始 `status` 字段。工具结果只暴露 `can_contact` 和 `unavailable_reason`；真正发送时由对应消息工具再次校验成员状态、渠道身份和发送权限。

### 重名处理

`query_roster` 可以按名字搜索，但返回结果不能只给名字。人类和 Agent 都必须返回稳定 ID：

- Agent 使用 `target_agent_id`。
- Human 使用 `target_member_id`，并在可用时附带 `platform_user_id` 和通用 `provider` 身份。

当出现重名时，结果中通过 `title`、`department`、`platform_user_id`、`provider` 等字段帮助模型和用户区分。后续真正发送时，应使用平台 ID、provider ID、open_id、external_id 等稳定标识，不能只靠 `display_name`。

## 可见性与可联系性

`query_roster` 必须遵守 Phase 1.1 的规则：

- `visible=false` 的对象永远不返回。
- `include_uncontactable=false` 时，只返回 `visible=true, can_contact=true` 的对象。
- `include_uncontactable=true` 时，可以返回 `visible=true, can_contact=false` 的对象。
- `include_uncontactable=true` 不能泄漏 private、跨租户、或其它不可见对象。

因此：

| 状态 | 默认返回 | include_uncontactable=true |
| --- | --- | --- |
| `visible=true, can_contact=true` | 返回 | 返回 |
| `visible=true, can_contact=false` | 不返回 | 返回 |
| `visible=false` | 不返回 | 不返回 |

`unavailable_reason` 只解释 `visible=true, can_contact=false` 的当前不可联系原因，不参与权限判断。不可见对象不会出现在结果里，因此也不返回 private、跨租户、无权限等不可见原因，避免通过错误原因泄漏通讯录边界。

## 查询范围

### source 是 company/custom Agent

返回范围：

- 同租户 `company/custom` Agent，排除 source 自己。
- 同租户人类成员。

不返回：

- 任何 `private` Agent。
- 跨租户 Agent。
- 跨租户人类成员。

### source 是 private Agent

返回范围：

- 同创建者的其它 `private` Agent。
- 创建者本人对应的人类成员，第一版仅按 `OrgMember.user_id == source_agent.creator_id` 匹配。

不返回：

- `company/custom` Agent。
- 其它创建者的 `private` Agent。
- 公司其它人类成员。

如果创建者没有对应的 `OrgMember` 映射，可以不返回 human 创建者条目。

## 搜索与排序

Phase 1.2 V1 不做高频使用排序、智能推荐、复杂部门导航或缓存快照。第一版只做一个简单、稳定、可实现的查询策略：

- 默认返回前 `limit` 个可联系对象。
- 用户或 Agent 知道大概找谁时，用 `query` 缩小范围。
- 需要只找数字员工或只找人时，用 `member_type` 缩小范围。
- 结果用稳定排序，保证同样条件下返回顺序可预测。
- 需要更多结果时，用 `offset` 翻页。

这意味着 V1 的重点不是“自动推荐最合适的人”，而是提供一个可靠的结构化通讯录查询入口。高频使用、最近联系、部门筛选、能力相关性排序等放到后续版本。

### 搜索字段

`query` 使用精确优先的简单包含匹配，不做复杂 fuzzy。V1 至少匹配：

- Agent: `name`、`role_description`
- Human: `name`、`title`、`department_path`

查询方式可以使用数据库层面的 `ILIKE '%query%'`，但排序应尽量让更精确的结果靠前：

1. 名字完全等于 `query`
2. 名字以 `query` 开头
3. 名字包含 `query`
4. 角色、职位、部门等其它字段包含 `query`
5. 默认稳定排序

V1 可选增强，但不作为必须完成项：

- Human transliteration 字段：`name_translit_full`、`name_translit_initial`
- Agent skills：等技能字段来源确定后再加

### 分页语义

V1 不返回精确总数，不做 `total/count(*)` 承诺。工具只返回本次实际返回数量和是否还有更多：

- `returned_count` 表示本次 `members` 数量。
- `has_more=true` 表示同样查询条件下继续增加 `offset` 可能拿到更多结果。
- 实现上可以查询 `limit + 1` 条来判断 `has_more`，最终只返回前 `limit` 条。

模型需要更多候选项时，应保持同样的 `query/member_type/include_uncontactable/limit`，并增加 `offset` 继续查询。

### 排序

第一版建议稳定排序：

1. `can_contact=true` 排在 `can_contact=false` 前面。
2. `member_type="agent"` 排在人类前面，便于模型优先选择数字员工协作。
3. `display_name` 升序。
4. `created_at` / `synced_at` 作为稳定兜底。

如果 `query` 非空，V1 使用前文定义的“名字完全匹配 / 前缀匹配 / 包含匹配 / 其它字段匹配”精确优先排序，不做 embedding、LLM rerank 或高频联系人排序。

## 性能策略

V1 不做全量 roster 快照缓存，也不做 Python 层全量过滤。实现时必须尽量把过滤放在数据库层：

- 先按 source Agent 的规则确定基础 SQL 范围：
  - `company/custom` source：同租户 `company/custom` Agent + 同租户 human。
  - `private` source：同创建者其它 `private` Agent + 创建者 human 映射。
- `query` 在 SQL 层做 `ilike` 过滤。
- `member_type` 在查询分支上过滤，不查无关类型。
- `limit/offset` 在 SQL 层分页，避免一次拉出全租户数据。
- Python 层只做结果格式化、`can_contact` / `unavailable_reason` 映射，以及少量最终排序合并。

V1 的性能目标：不追求智能排序，但避免“每次工具调用全量拉取公司所有人和 Agent 后再过滤”。

## 错误返回

错误也返回 JSON 字符串：

```json
{
  "ok": false,
  "error": {
    "code": "source_agent_not_found",
    "message": "Source agent was not found."
  }
}
```

建议错误码：

| error.code | 含义 |
| --- | --- |
| `source_agent_not_found` | 当前调用工具的 Agent 不存在。 |
| `invalid_member_type` | `member_type` 不是 `all/agent/human`。 |
| `invalid_limit` | `limit` 不在允许范围。 |
| `invalid_offset` | `offset` 小于 0。 |
| `query_roster_failed` | 未预期错误，日志中记录具体异常。 |

## 与其它阶段的边界

Phase 1.2 不做：

- 不把 `send_message_to_agent` 改成 `target_agent_id` 参数。
- 不删除 `agent_name` 发送路径。
- 不移除 system prompt 中的完整 Relationships 列表。
- 不隐藏旧 Relationships tab / API。
- 不迁移或删除旧关系表。
- 不实现完整组织花名册 UI。
- 不实现部门管理。

Phase 1.2 完成后，模型可以通过 `query_roster` 看到结构化通讯录；Phase 1.3 再把 A2A 发送链路改成 ID 优先。

## 最小测试矩阵

| 场景 | 预期 |
| --- | --- |
| `company` source 查询 all | 返回同租户 `company/custom` Agent + 同租户 human，不返回 private |
| `custom` source 查询 all | 返回同租户 `company/custom` Agent + 同租户 human，不返回 private |
| `private` source 查询 all | 返回同创建者其它 private Agent + 创建者 human（如果有映射） |
| `private` source 查询 all | 不返回 company/custom Agent |
| 跨租户 Agent / human | 不返回 |
| `include_uncontactable=false` | 不返回 expired/stopped/inactive 对象 |
| `include_uncontactable=true` | 返回 visible 但不可联系对象，并带 `unavailable_reason` |
| `query` 命中 Agent 名称 | 只返回匹配结果 |
| `query` 命中 human 姓名 / 职位 / 部门 | 只返回匹配结果 |
| `member_type="agent"` | 只返回 Agent |
| `member_type="human"` | 只返回 human |
| `limit/offset` | 返回分页结果，`has_more` 正确 |
| source Agent 不存在 | 返回 `ok=false, error.code=source_agent_not_found` |
