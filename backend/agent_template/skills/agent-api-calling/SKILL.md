# Agent API Calling

## When to Use This Skill

当你需要通过代码（而非 `send_message_to_agent` 工具）调用其他 Agent 时使用此技能。适用场景包括：

- 在 `execute_code` 中批量调用多个 Agent 并汇总结果
- 编写自动化脚本需要跨 Agent 协作
- 需要同步等待另一个 Agent 的完整回复

> **注意：** 简单的一对一消息传递优先使用 `send_message_to_agent` 工具。API 调用适合需要在代码逻辑中嵌入 Agent 调用的场景。

---

## 如何获取目标 Agent 的 ID

你的 **关系网络**（System Prompt 中的 `## Relationships` 部分）会列出所有可调用的数字员工同事，每个条目包含：

```
### 小助手 — 数据分析助手
- Agent ID：`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
- 关系：协作伙伴
```

从中提取 `Agent ID` 字段即可。**只有在你关系列表中的 Agent 才可以被调用。**

---

## API 调用方法

### 基本信息

- **接口地址：** `POST {API_BASE_URL}/api/v1/agent/chat`
- **认证方式：** Bearer Token（使用你的 Token Key，已在 System Prompt 中提供）
- **超时时间：** 最长 1 小时

### Python 代码示例

```python
import requests

# 你的 Token Key（从 System Prompt 中的 "Platform Agent API" 部分获取）
TOKEN_KEY = "clw_你的token_key"

# 目标 Agent 的 ID（从关系网络中获取）
TARGET_AGENT_ID = "目标agent的uuid"

response = requests.post(
    "{API_BASE_URL}/api/v1/agent/chat",
    headers={
        "Authorization": f"Bearer {TOKEN_KEY}",
        "Content-Type": "application/json",
    },
    json={
        "agent_id": TARGET_AGENT_ID,
        "prompt": "请帮我分析一下最近的销售数据趋势",
    },
    timeout=3600,  # 最长等待1小时
)

result = response.json()
reply = result["reply"]  # Agent 的回复内容
print(reply)
```

### 批量调用多个 Agent

```python
import requests
from concurrent.futures import ThreadPoolExecutor

TOKEN_KEY = "clw_你的token_key"
API_URL = "{API_BASE_URL}/api/v1/agent/chat"
HEADERS = {
    "Authorization": f"Bearer {TOKEN_KEY}",
    "Content-Type": "application/json",
}

# 从关系网络中获取的 Agent 列表
tasks = [
    {"agent_id": "agent-uuid-1", "prompt": "分析销售数据"},
    {"agent_id": "agent-uuid-2", "prompt": "生成周报摘要"},
    {"agent_id": "agent-uuid-3", "prompt": "检查库存预警"},
]

def call_agent(task):
    resp = requests.post(API_URL, headers=HEADERS, json=task, timeout=3600)
    return {"agent_id": task["agent_id"], "reply": resp.json()["reply"]}

# 并行调用
with ThreadPoolExecutor(max_workers=3) as pool:
    results = list(pool.map(call_agent, tasks))

for r in results:
    print(f"Agent {r['agent_id']}: {r['reply'][:100]}...")
```

---

## 响应格式

```json
{
  "reply": "Agent 的完整回复文本",
  "usage": {}
}
```

---

## 错误处理

| HTTP 状态码 | 含义 | 处理方式 |
|---|---|---|
| 401 | Token Key 无效或缺失 | 检查 Authorization header |
| 403 | 无权调用（不在关系列表中，或目标已过期） | 确认目标 Agent 在你的关系网络中 |
| 404 | 目标 Agent 不存在 | 检查 Agent ID 是否正确 |
| 400 | 目标 Agent 未配置 LLM 模型 | 联系管理员配置模型 |
| 422 | 请求参数错误（如 prompt 为空） | 检查请求体格式 |
| 502 | LLM 调用失败 | 重试或联系管理员 |

```python
response = requests.post(API_URL, headers=HEADERS, json=payload, timeout=3600)
if response.status_code != 200:
    print(f"调用失败: {response.status_code} - {response.json().get('detail', '')}")
else:
    print(response.json()["reply"])
```

---

## What NOT to Do

- 不要硬编码 Agent ID，从你的关系网络中动态获取
- 不要调用不在你关系列表中的 Agent，会返回 403
- 不要在日志或回复中暴露你的 Token Key
- 不要设置过短的超时时间，复杂任务可能需要较长处理时间
- 对于简单的一对一对话，优先使用 `send_message_to_agent` 工具而不是 API
