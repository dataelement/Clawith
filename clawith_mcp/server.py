"""Clawith MCP Server — call Clawith agents like functions from Cursor or any MCP client.

Usage:
    CLAWITH_URL=http://localhost:8000 CLAWITH_API_KEY=cw-xxx python server.py

Cursor config (~/.cursor/mcp.json):
    {
      "mcpServers": {
        "clawith": {
          "command": "python",
          "args": ["/path/to/clawith_mcp/server.py"],
          "env": {
            "CLAWITH_URL": "http://your-clawith-server:8000",
            "CLAWITH_API_KEY": "cw-your-api-key-here"
          }
        }
      }
    }
"""

import asyncio
import os

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

CLAWITH_URL = os.environ.get("CLAWITH_URL", "http://localhost:8000").rstrip("/")
CLAWITH_API_KEY = os.environ.get("CLAWITH_API_KEY", "")

app = Server("clawith")


def _headers() -> dict:
    return {
        "X-Api-Key": CLAWITH_API_KEY,
        "Content-Type": "application/json",
    }


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_agents",
            description="列出你有权限访问的所有 Clawith 智能体",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="call_agent",
            description="向指定的 Clawith 智能体发送消息，等待并返回完整回复",
            inputSchema={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "智能体的 UUID（可通过 list_agents 获取）",
                    },
                    "message": {
                        "type": "string",
                        "description": "要发送给智能体的消息内容",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "（可选）指定会话 ID，不传则自动复用最近会话",
                    },
                },
                "required": ["agent_id", "message"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if not CLAWITH_API_KEY:
        raise ValueError("CLAWITH_API_KEY 未设置，请在环境变量中配置")

    async with httpx.AsyncClient(
        base_url=CLAWITH_URL,
        headers=_headers(),
        timeout=120,  # 智能体响应可能需要较长时间
    ) as client:

        if name == "list_agents":
            return await _list_agents(client)

        if name == "call_agent":
            return await _call_agent(client, arguments)

        raise ValueError(f"未知工具: {name}")


async def _list_agents(client: httpx.AsyncClient) -> list[types.TextContent]:
    resp = await client.get("/api/agents/")
    resp.raise_for_status()
    agents = resp.json()

    if not agents:
        return [types.TextContent(type="text", text="暂无可用智能体")]

    lines = ["可用智能体列表：\n"]
    for a in agents:
        status = "🟢" if a.get("status") == "running" else "⚪"
        lines.append(f"{status} {a['name']}")
        lines.append(f"   ID: {a['id']}")
        if a.get("role_description"):
            lines.append(f"   职能: {a['role_description'][:80]}")
        lines.append("")

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _call_agent(client: httpx.AsyncClient, arguments: dict) -> list[types.TextContent]:
    agent_id = arguments["agent_id"]
    message = arguments["message"]
    session_id = arguments.get("session_id")

    payload: dict = {"message": message}
    if session_id:
        payload["session_id"] = session_id

    resp = await client.post(f"/api/agents/{agent_id}/chat", json=payload)

    if resp.status_code == 404:
        raise ValueError(f"智能体 {agent_id} 不存在，请用 list_agents 确认 ID")
    if resp.status_code == 403:
        raise ValueError("无权访问该智能体，请检查 API Key 或智能体权限")
    resp.raise_for_status()

    data = resp.json()
    reply = data.get("reply", "")
    session = data.get("session_id", "")

    result = reply
    if session:
        result += f"\n\n---\n_session_id: {session}_"

    return [types.TextContent(type="text", text=result)]


if __name__ == "__main__":
    asyncio.run(stdio_server(app))
