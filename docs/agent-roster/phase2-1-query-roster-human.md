# Phase 2.1 - query_roster human 精确查询

## 目标

让 `query_roster` 支持按 `target_member_id` 精确回查一个人类成员，为后续发送工具 ID 化提供稳定入口。

## 当前代码现状

- `query_roster` 已支持 `member_type="human"` 和模糊 `query`。
- human 返回里已经有：
  - `target_member_id`
  - `platform_user_id`
  - `provider`
  - `contact_tools`
  - `can_contact`
  - `unavailable_reason`
- 但当前不能按 `target_member_id` 精确查询。

## 工具参数

新增可选参数：

```json
{
  "target_member_id": "org_member_uuid"
}
```

完整参数示例：

```json
{
  "query": "张三",
  "member_type": "human",
  "target_member_id": "org_member_uuid",
  "include_uncontactable": false,
  "limit": 20,
  "offset": 0
}
```

## 行为规则

- `target_member_id` 是精确查询条件。
- `target_member_id` 存在时，只查询 human，不返回 agent。
- `member_type="agent"` 与 `target_member_id` 同时出现时返回结构化错误。
- `target_member_id` 非法 UUID 时返回结构化错误。
- 查询结果仍必须通过 `evaluate_roster_human_visibility(source_agent, member)`。
- `include_uncontactable=false` 时，不返回不可联系成员。
- `include_uncontactable=true` 时，可以返回 visible 但不可联系成员，并带 `unavailable_reason`。

## 返回结构

沿用 Phase 1.2 human schema：

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

## 不做

- 不改发送工具。
- 不改 prompt。
- 不新增 `department.path`。
- 不返回 `unionid`。
- 不返回 `total`。
- 不新增部门过滤，除非实现时发现现有查询结构顺手支持。

## 测试点

- tool schema 暴露 `target_member_id`。
- 非法 `target_member_id` 返回 `invalid_target_member_id`。
- `member_type="agent" + target_member_id` 返回错误。
- company/custom source 可以精确查同租户 active human。
- private source 只能查创建者对应 human。
- inactive human 默认不返回。
- inactive human 在 `include_uncontactable=true` 时返回并带 `member_inactive`。
