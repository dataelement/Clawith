# Agent 通讯录 Phase 2 - Human 发送链路 ID 化总览

本文是 Phase 2 的总览和索引。Phase 2 只做“人类联系人发送链路 ID 化”，不做旧关系体系清理、不做通讯录 UI、不做组织架构产品化。

## 背景

Phase 1 已完成数字员工发现与 A2A 调用链路工具化：

- Phase 1.1：权限与可见性判断拆分
- Phase 1.2：`query_roster`
- Phase 1.3：A2A 发送链路 ID 化
- Phase 1.4：prompt 去数字员工 Relationships 依赖

Phase 1 后，数字员工之间的主链路已经变成：

```text
query_roster -> target_agent_id -> send_message_to_agent / send_file_to_agent
```

但联系人类时，主链路仍混有旧方式：

```text
prompt 中的人类 Relationships 背景 -> member_name -> send_* tool
```

Phase 2 要把人类联系也切到：

```text
query_roster(member_type="human", query="...") -> stable ID -> send_platform_message / send_channel_message -> hard check
```

## 目标

把人类联系人从“按名字 + Relationships”切到“`query_roster` 返回稳定 ID + 发送工具硬校验”。

稳定 ID 包括：

- `target_member_id`：组织成员 ID，对应 `OrgMember.id`。
- `platform_user_id`：平台用户 ID，对应 `OrgMember.user_id`。
- provider identity：第三方身份，例如飞书的 `external_id` / `open_id`。

## 拆分文档

- [Phase 2.1 - query_roster human 精确查询](./phase2-1-query-roster-human.md)
- [Phase 2.2 - Human recipient resolver](./phase2-2-human-recipient-resolver.md)
- [Phase 2.3 - Human send tools ID 化](./phase2-3-human-send-tools.md)
- [Phase 2.4 - prompt/schema/test 收口](./phase2-4-prompt-schema-tests.md)

## 本期范围

- `query_roster` 支持按 `target_member_id` 精确查单个人类成员。
- 新增统一 human recipient resolver，统一做租户、可见性、状态、provider、channel 校验。
- `send_platform_message` 支持 `target_member_id` / `platform_user_id`。
- `send_channel_message` 作为第三方渠道统一入口，支持 `target_member_id + channel/provider_type` 并按 provider 分发。
- `send_feishu_message` 保留为 legacy shortcut，但不作为 Phase 2 新主路径。
- prompt 改成 roster-first，不再指导模型直接按 `member_name` 作为主路径发送。

## 非目标

- 不做完整组织花名册 UI。
- 不删除旧关系表。
- 不迁移 OKR 等仍依赖旧关系表的业务逻辑。
- 不做 `department.path`。
- 不做 `unionid` 暴露。
- 不返回 `total`。
- 不长期保留旧参数；旧参数只作为本阶段兜底，Phase 3 再清理。

## 实施顺序

1. Phase 2.1：先让 `query_roster` 可以按 `target_member_id` 精确查人。
2. Phase 2.2：新增 resolver，只做解析和校验，不发送消息。
3. Phase 2.3：把 `send_platform_message` 和 `send_channel_message` 接到 resolver，完成 ID 化发送；`send_feishu_message` 保留为 legacy shortcut。
4. Phase 2.4：再改 prompt、运行时 tool schema、持久化 seed schema 描述和测试覆盖；旧入口/旧参数仍只作为 legacy fallback 保留，删除放到 Phase 3。

## 当前结论

Phase 2 要一起改 human 发送工具，但每一步单独评审、单独提交。代码实现前，先确认 2.1 到 2.4 的文档边界。
