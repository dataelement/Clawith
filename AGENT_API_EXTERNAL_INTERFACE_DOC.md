# Clawith Agent External API Interface

本文档描述当前已实现的 Clawith Agent 外部调用接口。当前版本复用现有 Clawith JWT、Agent、Task、Files、Tools、Skills、WebSocket 接口，并新增 `agent-trace` 日志查询接口；不是旧计划中的 `/api/v1/agent/*` 独立路由。

## 1. 基本信息

- HTTP Base URL: `http://<host>:<port>/api`
- WebSocket Base URL: `ws://<host>:<port>`
- 线上示例: `http://192.168.106.163:3008`
- 鉴权: `Authorization: Bearer <JWT>`
- WebSocket 鉴权: query string `?token=<JWT>`
- 响应格式: JSON，文件下载接口除外

所有需要登录的 HTTP 请求都应带：

```http
Authorization: Bearer eyJ...
Content-Type: application/json
```

## 2. 获取 JWT

### 2.1 登录

```http
POST /api/auth/login
```

请求体：

```json
{
  "login_identifier": "user@example.com",
  "password": "password",
  "tenant_id": "optional-tenant-uuid"
}
```

成功响应：

```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "user": {
    "id": "user-uuid",
    "tenant_id": "tenant-uuid",
    "role": "org_admin"
  },
  "needs_company_setup": false
}
```

curl 示例：

```bash
curl -sS -X POST 'http://192.168.106.163:3008/api/auth/login' \
  -H 'Content-Type: application/json' \
  -d '{"login_identifier":"user@example.com","password":"password"}' \
  | jq -r '.access_token'
```

如果一个账号属于多个公司，登录响应会返回：

```json
{
  "requires_tenant_selection": true,
  "login_identifier": "user@example.com",
  "tenants": [
    {
      "tenant_id": "tenant-uuid",
      "tenant_name": "Company",
      "tenant_slug": "company"
    }
  ]
}
```

此时重新调用 `/api/auth/login` 并传入目标 `tenant_id`。

### 2.2 浏览器获取

如果已经在前端登录，可在浏览器 console 读取：

```js
localStorage.getItem('token')
```

## 3. 资源发现

创建 Agent 前通常需要发现可用 LLM、工具和 Skill。

### 3.1 查询 LLM 模型

```http
GET /api/enterprise/llm-models
Authorization: Bearer <JWT>
```

常用字段：

```json
[
  {
    "id": "model-uuid",
    "provider": "openai",
    "model": "gpt-5-mini",
    "label": "GPT-5 Mini",
    "enabled": true,
    "supports_vision": false,
    "max_output_tokens": 4096
  }
]
```

外部创建 Agent 时可选择两个不同的 enabled model，分别作为：

- `primary_model_id`
- `fallback_model_id`

### 3.2 查询工具

```http
GET /api/tools
Authorization: Bearer <JWT>
```

常用字段：

```json
[
  {
    "id": "tool-uuid",
    "name": "browser_click",
    "display_name": "AgentBay: Browser Click",
    "category": "browser",
    "enabled": true,
    "is_default": false,
    "source": "builtin"
  }
]
```

创建 Agent 后使用 `PUT /api/tools/agents/{agent_id}` 绑定工具。

### 3.3 查询 Skill

```http
GET /api/skills/
Authorization: Bearer <JWT>
```

常用字段：

```json
[
  {
    "id": "skill-uuid",
    "name": "Complex Task Executor",
    "folder_name": "complex-task-executor",
    "is_builtin": true,
    "is_default": true
  }
]
```

创建 Agent 时通过 `skill_ids` 复制到 Agent workspace。

## 4. 创建 Agent

```http
POST /api/agents/
Authorization: Bearer <JWT>
Content-Type: application/json
```

请求体示例：

```json
{
  "name": "External API Agent",
  "role_description": "Complete external API tasks and write concise results.",
  "bio": "Created by external API smoke test.",
  "personality": "precise, brief, verification-oriented",
  "boundaries": "never expose secrets; never mutate unrelated files",
  "primary_model_id": "primary-model-uuid",
  "fallback_model_id": "fallback-model-uuid",
  "permission_scope_type": "company",
  "permission_access_level": "manage",
  "skill_ids": ["skill-uuid-1", "skill-uuid-2"],
  "autonomy_policy": {
    "mode": "test",
    "max_rounds": 3
  }
}
```

成功响应核心字段：

```json
{
  "id": "agent-uuid",
  "name": "External API Agent",
  "primary_model_id": "primary-model-uuid",
  "fallback_model_id": "fallback-model-uuid",
  "personality": "precise, brief, verification-oriented",
  "boundaries": "never expose secrets; never mutate unrelated files"
}
```

## 5. 配置 Agent 工具

### 5.1 更新工具绑定

```http
PUT /api/tools/agents/{agent_id}
Authorization: Bearer <JWT>
Content-Type: application/json
```

请求体：

```json
[
  {
    "tool_id": "tool-uuid-1",
    "enabled": true
  },
  {
    "tool_id": "tool-uuid-2",
    "enabled": true
  }
]
```

响应：

```json
{
  "ok": true
}
```

### 5.2 查询 Agent 工具

```http
GET /api/tools/agents/{agent_id}
Authorization: Bearer <JWT>
```

用于验证工具是否已启用。

## 6. 写入心智文件

创建 Agent 时的 `personality` 和 `boundaries` 会存到 Agent 字段。额外的 soul/memory 可写入 workspace 文件。

### 6.1 写 `soul.md`

```http
PUT /api/agents/{agent_id}/files/content?path=soul.md
Authorization: Bearer <JWT>
Content-Type: application/json
```

请求体：

```json
{
  "content": "# Soul\nPrefer direct verification and concise results.",
  "autosave": false
}
```

### 6.2 写 memory

```http
PUT /api/agents/{agent_id}/files/content?path=memory/memory.md
Authorization: Bearer <JWT>
Content-Type: application/json
```

请求体：

```json
{
  "content": "Remember this agent was created by an external API flow.",
  "autosave": false
}
```

### 6.3 读取文件

```http
GET /api/agents/{agent_id}/files/content?path=soul.md
Authorization: Bearer <JWT>
```

响应：

```json
{
  "path": "soul.md",
  "content": "# Soul\n...",
  "version_token": "..."
}
```

## 7. 同步运行 Agent Chat

同步调用使用 WebSocket。

```text
ws://<host>/ws/chat/{agent_id}?token=<JWT>&lang=zh
```

HTTPS 部署时使用：

```text
wss://<host>/ws/chat/{agent_id}?token=<JWT>&lang=zh
```

发送消息：

```json
{
  "content": "请用一句话回复 smoke test",
  "display_content": "请用一句话回复 smoke test",
  "model_id": "optional-model-uuid"
}
```

常见服务端事件：

```json
{"type": "connected", "session_id": "session-uuid"}
```

```json
{"type": "chunk", "content": "partial text"}
```

```json
{
  "type": "tool_call",
  "name": "read_file",
  "status": "done",
  "args": {},
  "result": "..."
}
```

```json
{
  "type": "done",
  "role": "assistant",
  "content": "final answer",
  "trace_id": "trace-id"
}
```

注意：新 session 可能先返回 welcome message，对应 `done` 事件可能没有 `trace_id`。调用方应等待带 `trace_id` 的最终 `done`。

## 8. 异步运行 Task

异步 run 复用 Agent Task 接口。`type=todo` 的 task 创建后会自动触发后台执行。

### 8.1 创建 run

```http
POST /api/agents/{agent_id}/tasks/
Authorization: Bearer <JWT>
Content-Type: application/json
```

请求体：

```json
{
  "title": "Smoke external API run",
  "description": "请完成任务并给出执行结果",
  "type": "todo",
  "priority": "medium"
}
```

成功响应：

```json
{
  "id": "task-uuid",
  "agent_id": "agent-uuid",
  "title": "Smoke external API run",
  "description": "请完成任务并给出执行结果",
  "type": "todo",
  "status": "pending",
  "priority": "medium"
}
```

### 8.2 查询 run 状态

```http
GET /api/agents/{agent_id}/tasks/
Authorization: Bearer <JWT>
```

可选 query：

- `status_filter=pending|doing|done`
- `type_filter=todo|supervision`

调用方可在列表中按 `id == task_id` 找到目标 run。

状态：

- `pending`: 已创建，等待或准备执行
- `doing`: 执行中
- `done`: 执行完成

### 8.3 查询 run 进度日志

```http
GET /api/agents/{agent_id}/tasks/{task_id}/logs
Authorization: Bearer <JWT>
```

响应：

```json
[
  {
    "id": "log-uuid",
    "task_id": "task-uuid",
    "content": "🤖 开始执行任务...",
    "created_at": "2026-06-08T..."
  },
  {
    "id": "log-uuid",
    "task_id": "task-uuid",
    "content": "✅ 任务完成\n\n...",
    "created_at": "2026-06-08T..."
  }
]
```

错误通常会以 `❌` 开头写入 task log。

## 9. 查询运行 Trace 日志

Trace 日志包含 LLM loop 的 prompt、response、tool_call、tool_result，以及 task_start/task_end 等事件。

```http
GET /api/logs/agent-trace
Authorization: Bearer <JWT>
```

至少需要传一个过滤条件：

- `trace_id`: WebSocket `done.trace_id`
- `task_id`: 异步 run 的 task id
- `agent_id`: Agent id

其他 query：

- `act=agent_loop`
- `limit=200`

### 9.1 按 chat trace 查询

```bash
curl -sS 'http://192.168.106.163:3008/api/logs/agent-trace?trace_id=5cce840b-135&limit=200' \
  -H "Authorization: Bearer $JWT"
```

### 9.2 按 task 查询

```bash
curl -sS 'http://192.168.106.163:3008/api/logs/agent-trace?task_id=task-uuid&limit=200' \
  -H "Authorization: Bearer $JWT"
```

响应示例：

```json
[
  {
    "time": "2026-06-08T...",
    "level": "INFO",
    "message": "agent_loop prompt",
    "act": "agent_loop",
    "event": "prompt",
    "trace_id": "trace-id",
    "task_id": "task-uuid",
    "agent_id": "agent-uuid",
    "provider": "openai",
    "model": "gpt-5-mini",
    "round": 1,
    "messages": [
      {
        "role": "system",
        "content": "..."
      },
      {
        "role": "user",
        "content": "..."
      }
    ]
  },
  {
    "time": "2026-06-08T...",
    "level": "INFO",
    "message": "agent_loop response",
    "act": "agent_loop",
    "event": "response",
    "trace_id": "trace-id",
    "task_id": "task-uuid",
    "agent_id": "agent-uuid",
    "content": "final or intermediate response",
    "tool_calls_count": 0,
    "usage": {}
  }
]
```

常见 event：

- `ws_message`
- `task_start`
- `prompt`
- `response`
- `tool_call`
- `tool_result`
- `task_end`

## 10. 获取 workspace 结果

当前没有单独的服务端 workspace zip 接口。调用方可通过文件列表和下载接口递归打包 workspace。

### 10.1 列文件

```http
GET /api/agents/{agent_id}/files/?path=
Authorization: Bearer <JWT>
```

响应：

```json
[
  {
    "path": "workspace/output.txt",
    "name": "output.txt",
    "is_dir": false,
    "size": 128,
    "updated_at": "2026-06-08T..."
  }
]
```

如果 `is_dir=true`，继续以该 `path` 递归调用列表接口。

### 10.2 下载文件

```http
GET /api/agents/{agent_id}/files/download?path=workspace/output.txt
Authorization: Bearer <JWT>
```

响应为文件 bytes。

下载接口也支持 query token，适用于浏览器直链场景：

```text
/api/agents/{agent_id}/files/download?path=workspace/output.txt&token=<JWT>
```

### 10.3 客户端 zip

测试客户端 `AgentApiClient.export_workspace(agent_id)` 会递归调用：

- `GET /api/agents/{agent_id}/files/`
- `GET /api/agents/{agent_id}/files/download`

然后在客户端内存中生成 zip bytes。

## 11. 完整调用流程

1. `POST /api/auth/login` 获取 JWT。
2. `GET /api/enterprise/llm-models` 选择两个不同的 enabled LLM。
3. `GET /api/tools` 选择要启用的工具。
4. `GET /api/skills/` 选择心智/Skill。
5. `POST /api/agents/` 创建 Agent，传入 `primary_model_id`、`fallback_model_id`、`personality`、`boundaries`、`skill_ids`。
6. `PUT /api/tools/agents/{agent_id}` 启用工具。
7. `PUT /api/agents/{agent_id}/files/content?path=soul.md` 写 soul。
8. `PUT /api/agents/{agent_id}/files/content?path=memory/memory.md` 写 memory。
9. WebSocket `/ws/chat/{agent_id}?token=<JWT>` 跑同步 chat，拿 `trace_id`。
10. `GET /api/logs/agent-trace?trace_id=<trace_id>` 验证 chat prompt/response。
11. `POST /api/agents/{agent_id}/tasks/` 创建异步 run。
12. 轮询 `GET /api/agents/{agent_id}/tasks/`，直到目标 task `status=done`。
13. `GET /api/agents/{agent_id}/tasks/{task_id}/logs` 获取 run 进度日志。
14. `GET /api/logs/agent-trace?task_id=<task_id>` 获取 run prompt/response/tool trace。
15. 递归 `files` + `download` 接口导出 workspace zip。

## 12. Live Smoke 脚本

仓库提供完整 live smoke 脚本：

```bash
python backend/scripts/smoke_agent_external_api.py \
  --base-url http://192.168.106.163:3008 \
  --login-identifier user@example.com \
  --password 'password'
```

也可以直接传 JWT：

```bash
python backend/scripts/smoke_agent_external_api.py \
  --base-url http://192.168.106.163:3008 \
  --jwt "$JWT"
```

该脚本会自动执行：

- health check
- JWT 登录
- logs route 鉴权检查
- 发现 LLM / tools / skills
- 创建 Agent
- 验证不同 LLM、工具、心智文件
- WebSocket chat run
- trace prompt/response 验证
- task run
- task logs 和 task trace 验证
- workspace zip 导出

## 13. 状态码与错误

- `200`: 请求成功
- `201`: 创建成功
- `204`: 成功且无响应体
- `401`: 未登录、JWT 缺失或无效
- `403`: 无权限、租户不匹配、账号或公司被禁用
- `404`: 资源不存在，或部署镜像不包含目标 route
- `409`: 资源冲突
- `422`: 请求参数校验失败
- `500`: 服务端异常

调用方建议：

- 对 task run 设置超时。
- task log 出现 `❌` 时按失败处理。
- `GET /api/logs/agent-trace` 必须传 `trace_id`、`task_id` 或 `agent_id` 之一。
- WebSocket 只把带 `trace_id` 的 `done` 当作本轮最终结果。

## 14. 当前限制

- 异步 run 当前复用 `Task` 模型，状态只有 `pending/doing/done`，没有独立的 external run 表。
- workspace zip 当前由客户端递归文件 API 生成，不是服务端单接口生成。
- WebSocket 使用 query token，调用方需要避免把完整 URL 写入不安全日志。
- `agent-trace` 日志来自 backend JSONL 日志文件；容器重建或日志轮转策略会影响可查询窗口。
- task trace 按 `task_id` 查询 prompt/response 依赖后端包含 task_id 透传修复的版本。
