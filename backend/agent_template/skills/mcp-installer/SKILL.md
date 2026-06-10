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

**Method A: Smithery Import** (tool found on Smithery with remote hosting support)
- Usually uses an already-configured Smithery API Key (company/admin key or an existing agent key)
- Individual tool tokens NOT needed; Smithery handles auth via OAuth

**Method B: Direct URL Import** (tool NOT on Smithery, but has public HTTP/SSE endpoint)
- User provides the MCP server URL directly
- May require tool-specific API key

**Not importable** (local-only tools)
- Requires local Docker/process; inform user these cannot be imported automatically

---

### Method A: Smithery Import

#### Try import first
Always try the import tool directly before asking the user for any Smithery credential:

```
import_mcp_server(
  server_id="<qualified_name>"
)
```

The platform may already have a company-level or agent-level Smithery key configured. Do **not** assume the user needs to provide one.

#### Only ask for a Smithery API Key if the tool explicitly says none is configured
If `import_mcp_server` explicitly returns that no Smithery API Key is configured, then explain Smithery and guide the user. Use the following talking points (adapt to context, don't read verbatim):

> **Smithery** (smithery.ai) is an MCP tool marketplace, similar to an app store. Through it, I can install third-party tools such as GitHub, Notion, and Slack, and Smithery can handle the authentication flow.
>
> **Why is an account or API key needed?**
> Smithery uses the API key to identify your account, associate installed tools with that account, and store authorization information securely.
>
> **What do you get after setting it up once?**
> - Provide the key once; future tool imports can reuse the saved configuration
> - No need to create separate tokens for each tool, such as GitHub PATs; supported tools use OAuth
> - Access a large catalog of MCP tools and extend your capabilities when needed
>
> **How to get a key:**
> 1. Sign up or log in at https://smithery.ai
> 2. Go to https://smithery.ai/account/api-keys and create an API key
> 3. Provide the key to me only when the import tool explicitly says no Smithery key is configured

#### Import
```
import_mcp_server(
  server_id="<qualified_name>",
  config={"smithery_api_key": "<key>"}  # only when the tool explicitly says no key is configured
)
```

#### Handle OAuth
Some tools return an OAuth authorization URL. Tell the user to visit the link.

**Important:** Do NOT ask for individual tool tokens (GitHub PAT, Notion API key, etc.) when using Smithery; OAuth handles this automatically.

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
- Don't ask for GitHub PAT, Notion key etc. when using Smithery; OAuth handles these
- Don't ask for a Smithery API Key before trying `import_mcp_server` directly; a company/admin key may already exist
- Don't tell users to go to Settings; handle everything in chat
- Don't echo API keys back in your response
- Don't skip the search step; always verify the server exists before importing
- Don't import local-only tools; inform users they require local installation
