# MCP Tool Installer

## When to Use This Skill
Use this skill when a user wants to add a new tool or integration (e.g., GitHub, Brave Search, Notion, etc.) that isn't currently available but can be imported from the MCP registry or via a direct URL.

---

## Step-by-Step Protocol

### Step 1 — Search first
```
discover_resources(query="<what the user wants>", max_results=5)
```
Show the results and let the user pick. Note the `ID` field (e.g. `github`).

### Step 2 — Determine import method

**Method A: Smithery Import** (tool found on Smithery with remote hosting support 🌐)
- Requires Smithery API Key (one-time per agent)
- Individual tool tokens NOT needed — Smithery handles auth via OAuth

**Method B: Direct URL Import** (tool NOT on Smithery, but has public HTTP/SSE endpoint)
- User provides the MCP server URL directly
- May require tool-specific API key

**Not importable** (💻 local-only tools)
- Requires local Docker/process — inform user these cannot be imported automatically

---

### Method A: Smithery Import

#### Check Smithery API Key
If no Smithery key is configured, explain Smithery and guide the user. Use the following talking points (adapt to context, don't read verbatim):

> **Smithery** (smithery.ai) 是一个 MCP 工具市场，类似于"应用商店"。通过它，我可以帮你一键安装各种第三方工具（如 GitHub、Notion、Slack 等），并自动完成认证。
>
> **为什么需要注册？**
> Smithery 用 API Key 来识别你的身份，这样安装的工具会关联到你的账号，认证信息也会安全保存。
>
> **注册一次后有什么好处？**
> - 🔑 只需提供一次 Key，后续安装其他工具时我会自动帮你配置
> - 🔐 不需要为每个工具单独创建 Token（如 GitHub PAT），OAuth 一键授权
> - 📦 支持上千种 MCP 工具，随时可以扩展你的能力
>
> **获取步骤：**
> 1. 访问 https://smithery.ai 注册/登录
> 2. 前往 https://smithery.ai/account/api-keys 创建 API Key
> 3. 将 Key 提供给我

#### Import
```
import_mcp_server(
  server_id="<qualified_name>",
  config={"smithery_api_key": "<key>"}  # first time only
)
```

#### Handle OAuth
Some tools return an OAuth authorization URL. Tell the user to visit the link.

**Important:** Do NOT ask for individual tool tokens (GitHub PAT, Notion API key, etc.) when using Smithery — OAuth handles this automatically.

---

### Method B: Direct URL Import

When a tool is not available on Smithery but the user has a public MCP endpoint:
```
import_mcp_server(
  server_id="<server name>",
  config={
    "mcp_url": "https://my-mcp-server.com/sse",
    "api_key": "<optional tool-specific key>"
  }
)
```
The system will connect to the URL, discover available tools, and register them.

---

## What NOT to Do
- ❌ Don't ask for GitHub PAT, Notion key etc. when using Smithery — OAuth handles these
- ❌ Don't tell users to go to Settings — handle everything in chat
- ❌ Don't echo API keys back in your response
- ❌ Don't skip the search step — always verify the server exists before importing
- ❌ Don't import local-only tools — inform users they require local installation
