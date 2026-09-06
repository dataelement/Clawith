# Accepted Backend Capability Coverage Matrix

This matrix freezes current mounted Backend capability and lifecycle coverage before the clean rewrite. It preserves product behavior requirements, not route compatibility. Before deleting each current API, implementation inspects Frontend and dynamic/external consumers to capture required behavior and then verifies the replacement target contract.

Status: accepted — Phase 0 recorded endpoint-level disposition, consumer evidence, and a planned replacement/removal gate for every frozen endpoint and lifecycle row. The 401 accepted decisions are the current G002 source-disposition authority; full product contracts remain per-module gates.

## Mounted API surfaces

| Current surface | Disposition | Target owner | Replacement/removal gate |
|---|---|---|---|
| `api/auth.py`, `api/sso.py` | rewrite | Identity/Auth | Account/Membership/Principal auth flows and real-entry tests exist |
| `api/tenants.py`, `api/users.py`, `api/organization.py` | rewrite | Identity/Tenant | Tenant/Membership administration and switching tests exist |
| `api/admin.py` | rewrite | Platform Administration | Platform Principal target-Tenant APIs and Audit tests exist |
| `api/enterprise.py` | split/rewrite | Model, Credential, Identity Provider, Organization, Enterprise Settings, Tenant Knowledge | every endpoint assigned to one target owner; EnterpriseInfo retained until Tenant Knowledge decision |
| `api/google_workspace.py` | reuse/rewrite | Identity Provider/Organization adapter | OAuth/sync protocol moved behind Tenant Credential and owner tests |
| `api/agents.py` | split | Agent/Permission; delete start/stop/API-key/approval | Agent identity/config and visibility APIs exist; removed routes have negative source guards |
| `api/advanced.py` | split | Agent Template/Observability/A2A; delete creator handover | templates, metrics, and cross-Agent collaboration behavior assigned; handover absent and created-by immutable |
| `api/agent_credentials.py` | rewrite | Credential | unified Credential APIs and owner-matrix tests exist |
| `api/activity.py` | rewrite | Observability | bounded Agent activity/history projection exists |
| `api/directory.py` | rewrite | Directory/Permission | visible-Agent and member directory queries use current resolver |
| `api/chat_sessions.py`, `api/websocket.py`, `api/upload.py` | rewrite | Direct Session | human Session Input, streaming, upload/product input, reply, cancellation tests exist |
| `api/messages.py` | rewrite | Session/Notification | inbox/unread has one target owner and bounded query |
| `api/group_websocket.py`, `api/groups.py` | rewrite | Group | membership, Session, Workspace, announcement, realtime and Run tests exist |
| `api/tasks.py` | delete | Task Tool replaces persistence | no persistent Task model/route/import remains |
| `api/relationships.py` | split | Permission visibility producer; delete relationship labels/Memory/creator semantics | explicit Membership/Agent grants exist; obsolete relations absent |
| `api/files.py` list/read/preview/download | rewrite | Workspace | bounded preview/read contract passes |
| `api/files.py` human write/delete/lock/revision/restore | delete/rewrite | Workspace mutation service | no first-release human mutation API; Agent Tools and later Frontend CAS contract replace behavior |
| `api/files.py` Skill import paths | rewrite | Capability Market/Workspace Skill | controlled install/import contract passes |
| `api/files.py` Enterprise KB paths | defer/rewrite | Tenant Knowledge | retained until owner decision and isolation/model-source tests pass |
| `api/skills.py` | rewrite | Capability Market/Workspace Skill | controlled install/search/read exists; Agent-authored mutation absent |
| `api/tools.py` | rewrite | Tool/Capability | Registry, Definition, Grant, MCP connection and Market APIs pass |
| `api/experience.py` | delete | Workspace Memory replaces it | no Experience/RAG authority remains |
| `api/triggers.py`, `api/schedules.py`, `api/webhooks.py` | consolidate/rewrite | Trigger | schedule/webhook/polling Trigger sources and result records pass |
| `api/focus.py` | defer/rewrite | Focus product module | owner/API defined before removal |
| `api/feishu.py` | reuse/rewrite | Feishu Channel/Identity adapter | inbound/outbound/credential/delivery tests pass |
| `api/dingtalk.py` | reuse/rewrite | DingTalk Channel adapter | inbound/outbound/credential/delivery tests pass |
| `api/wecom.py` | reuse/rewrite | WeCom Channel adapter | inbound/outbound/credential/delivery tests pass |
| `api/wechat.py` | reuse/rewrite | WeChat Channel adapter | QR/poll/inbound/outbound tests pass |
| `api/slack.py` | reuse/rewrite | Slack Channel adapter | webhook/config/delivery tests pass |
| `api/discord_bot.py` | reuse/rewrite | Discord Channel adapter | gateway/webhook/config/delivery tests pass |
| `api/teams.py` | reuse/rewrite | Teams Channel adapter | webhook/config/delivery tests pass |
| `api/atlassian.py` | reuse/rewrite | Atlassian Channel/Tool adapter | config/test/delivery contracts pass |
| `api/whatsapp.py` | delete unless consumer proved | Channel | currently unmounted; explicit consumer evidence required to restore |
| `api/gateway.py` | delete | A2A uses target product owner | no OpenClaw/Gateway polling/report/send path remains |
| `api/notification.py` | defer/rewrite | Notification | owner and bounded query/delivery tests exist |
| `api/onboarding.py` | defer/rewrite | Onboarding | Account/Membership/Agent onboarding contract exists |
| `api/okr.py` | defer/rewrite | OKR | objectives/KR/alignment/progress/report/collection flows pass |
| `api/pages.py` | defer/rewrite | Published Page | private/public page contracts pass |
| `api/plaza.py` | defer/rewrite | Plaza | post/comment/like permission and pagination tests pass |
| `api/agentbay_control.py` | reuse/rewrite | Sandbox/AgentBay product adapter | control actions use target Agent/Permission and bounded Sandbox contracts |

## Application lifecycles and bootstrap

| Current lifecycle | Disposition | Target owner | Gate |
|---|---|---|---|
| `create_all`, default Tenant creation, inline file/data migration, patch seeders | delete | schema/bootstrap | target startup contains no repair/migration fallback |
| Builtin Tool and template seeding | rewrite | Bootstrap/Capability/Agent Template | idempotent owner bootstrap has deterministic tests |
| default/OKR Agent patch seeders | delete/rewrite | Agent/OKR | target product bootstrap creates normal Agents without `is_system` authority |
| Runtime worker context | delete | Agent Runner | one target Runner lifecycle owns readiness/shutdown |
| Trigger daemon and schedule scheduler | consolidate | Trigger | one Trigger lifecycle and bounded intake |
| realtime Redis subscriber | reuse/rewrite | Realtime transport | committed owner events only, cleanup verified |
| Feishu/DingTalk/WeCom/WeChat/Discord connector managers | reuse/rewrite | Channel modules | independent bounded lifecycle per connector |
| `ss-local` proxy startup | defer/remove by consumer | Discord infrastructure | keep only if mounted Discord deployment still requires it |
| server startup audit | rewrite | Audit | System actor and target Tenant rules applied |

## Persistence and authority inventory

| Current authority | Target |
|---|---|
| Identity/User/Tenant mixed rows | Account/Membership/Tenant plus Principal union |
| overloaded Agent row | narrow Agent plus separate module relations |
| Task/TaskLog | no persistent object; Task Tool/Child Run History |
| AgentRun plus Checkpoint/Command/Event/Ledger | Run/Snapshot/History/Context Projection |
| Tool/AgentTool/Skill tables | Tool Definition/Grant, Capability Market, Workspace Skill package |
| AgentCredential and Secret JSON columns | unified Credential and binding matrix |
| AgentPermission/relationships | minimal RBAC, visibility grants, login-scoped human authorization |
| Experience/SessionContextState | Workspace Memory and Context Projection |
| AgentSchedule | Trigger configuration |
| Approval/quotas/fallback | deleted |

## Reusable implementation families

- Sandbox providers and isolation.
- Local/S3 object operations without fallback paths.
- Conversion, extraction, image and document helpers.
- Provider HTTP/multimodal helpers excluding `llm/caller.py`.
- MCP transport/OAuth behind target Market/Credential.
- Capability-specific external operations excluding `agent_tools.py` facade.
- Channel SDK/webhook/stream mechanics.
- Realtime mechanics, logging, errors, time-zone and business-calendar helpers.

Every reuse item requires a named source function/module, forbidden-import scan, target-owner test, and copy/move record. No authority facade is allowlisted.
