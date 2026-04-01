# Clawith MCP Server

让 Cursor、Claude Desktop 等 MCP 客户端像调用函数一样调用 Clawith 智能体。

## 安装

```bash
cd clawith_mcp
pip install mcp httpx
```

## 获取 API Key

1. 打开 Clawith 网页 → 右上角头像 → **账户设置**
2. 滚动到底部 **API Key（外部集成）** 区块
3. 点击「生成 API Key」，复制保存（只显示一次）

## Cursor 配置

编辑 `~/.cursor/mcp.json`（不存在则新建）：

```json
{
  "mcpServers": {
    "clawith": {
      "command": "python",
      "args": ["/绝对路径/clawith_mcp/server.py"],
      "env": {
        "CLAWITH_URL": "http://your-clawith-server:8000",
        "CLAWITH_API_KEY": "cw-你的key"
      }
    }
  }
}
```

重启 Cursor，Cursor Agent 即可使用以下工具：

## 可用工具

### `list_agents`
列出所有有权限的智能体，返回名称和 ID。

### `call_agent`
向指定智能体发送消息，等待并返回完整回复。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `agent_id` | string | ✅ | 智能体 UUID |
| `message` | string | ✅ | 消息内容 |
| `session_id` | string | — | 指定会话，不传则自动复用最近会话 |

## 团队使用（20 人）

每位成员：
1. 各自生成自己的 API Key
2. 在本机配置 `~/.cursor/mcp.json`
3. 填写指向同一个 Clawith 服务器的 `CLAWITH_URL`

MCP Server 在每人本机独立运行，Clawith 后端统一共享，互不干扰。
