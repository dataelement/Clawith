"""Canonical model-facing definitions and execution policy for builtin tools.

Builtin database rows remain useful for assignment, enablement, configuration,
and UI display.  They are not the model contract: both the startup seeder and
the model-facing resolver derive description/schema from this module.

This is deliberately data plus small conversion helpers.  It is not a plugin
registry and it does not perform provider health checks.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from app.services.llm.finish import FINISH_TOOL_SEED


# Builtin tool definitions — these map to the hardcoded AGENT_TOOLS
_BUILTIN_TOOL_SOURCE = [
    FINISH_TOOL_SEED,
    {
        "name": "list_files",
        "display_name": "List Files",
        "description": "List files and folders in a directory within the workspace. Use this before writing new workspace documents so you can inspect the current folder structure, reuse existing topical subfolders when appropriate, and avoid dumping files directly into the workspace root unless there is a clear reason. Can also list enterprise_info/ for shared company information.",
        "category": "file",
        "icon": "📁",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list, defaults to root (empty string)"}
            },
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "read_file",
        "display_name": "Read File",
        "description": "Read UTF-8 text file contents from the workspace. This tool does not parse binary files such as XLSX, DOCX, PPTX, PDF, images, or archives; use read_document for supported office documents. Can read soul.md, memory/memory.md, skills/, and enterprise_info/. Focus is stored in system tools, not focus.md. Use offset and limit for reading large text files in chunks.",
        "category": "file",
        "icon": "📄",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path, e.g.: soul.md, memory/memory.md"},
                "offset": {"type": "integer", "description": "Starting line number (0-indexed, default 0). Use with limit for pagination."},
                "limit": {"type": "integer", "description": "Maximum number of lines to read (default 2000). Use with offset for pagination."},
            },
            "required": ["path"],
        },
        "config": {"max_file_size_kb": 500},
        "config_schema": {
            "fields": [
                {"key": "max_file_size_kb", "label": "Max file size (KB)", "type": "number", "default": 500},
            ]
        },
    },
    {
        "name": "list_focus_items",
        "display_name": "List Focus Items",
        "description": "List structured Focus items from the system database.",
        "category": "file",
        "icon": "◎",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "include_completed": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether to include completed Focus items. Default false.",
                },
            },
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "upsert_focus_item",
        "display_name": "Upsert Focus Item",
        "description": "Create or update a structured Focus item in the system database.",
        "category": "file",
        "icon": "◎",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Stable short identifier, snake_case preferred."},
                "title": {"type": "string", "description": "Short title (Focus名称)."},
                "description": {"type": "string", "description": "Human-readable description of what is being tracked."},
                "kind": {"type": "string", "enum": ["normal", "system"], "description": "normal or system"},
                "source": {"type": "string", "description": "Optional origin label, e.g. user, trigger, a2a, okr."},
            },
            "required": ["description"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "complete_focus_item",
        "display_name": "Complete Focus Item",
        "description": "Mark a structured Focus item completed.",
        "category": "file",
        "icon": "◎",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Focus item identifier to complete."},
            },
            "required": ["key"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "write_file",
        "display_name": "Write File",
        "description": "Write or update a file in the workspace. Before creating a new document under workspace/, first inspect the relevant directories with list_files, prefer an existing topical subfolder over the workspace root, and create a new subfolder when the content belongs to a new category. Avoid placing standalone document files directly in workspace/ root unless the user explicitly wants that. Can update memory/memory.md, create documents in workspace/, create skills in skills/.",
        "category": "file",
        "icon": "✏️",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path, e.g.: memory/memory.md, workspace/reports/report.md, workspace/knowledge_base/notes.md. Prefer a meaningful subfolder instead of writing loose files into workspace/ root."},
                "content": {"type": "string", "description": "File content to write"},
            },
            "required": ["path", "content"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "delete_file",
        "display_name": "Delete File",
        "description": "Delete a file from the workspace. Cannot delete soul.md or tasks.json.",
        "category": "file",
        "icon": "🗑️",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to delete"}
            },
            "required": ["path"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "move_file",
        "display_name": "Move File",
        "description": "Move or rename a file or folder within the workspace. Use this instead of execute_code for reorganizing workspace files, moving generated documents into subfolders, or renaming files. Cannot move soul.md, tasks.json, or enterprise_info/. If destination_path is an existing folder or ends with '/', the original filename is preserved inside that folder. Does not overwrite by default.",
        "category": "file",
        "icon": "↪",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "Current file or folder path, e.g.: workspace/report.md"},
                "destination_path": {"type": "string", "description": "Destination file/folder path, e.g.: workspace/archive/report.md or workspace/presentations/PPT/"},
                "overwrite": {"type": "boolean", "description": "Replace the destination if it already exists. Default false."},
            },
            "required": ["source_path", "destination_path"],
        },
        "config": {},
        "config_schema": {},
    },
    # --- Enhanced file management tools ---
    {
        "name": "edit_file",
        "display_name": "Edit File",
        "description": "Surgically replace a specific string inside an existing file without rewriting the whole content. Prefer this over write_file when you only need to change one or more sections.",
        "category": "file",
        "icon": "✂️",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to edit, e.g.: memory/memory.md, skills/my-skill/SKILL.md"},
                "old_string": {"type": "string", "description": "Exact text to find and replace. Must match exactly including whitespace and newlines."},
                "new_string": {"type": "string", "description": "Replacement text"},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences if true (default: false)"},
            },
            "required": ["path", "old_string", "new_string"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "search_files",
        "display_name": "Search Files",
        "description": "Search for content patterns across files using regex. Returns matching lines with file paths and line numbers. Results capped at 50 per query.",
        "category": "file",
        "icon": "🔍",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for, e.g.: 'API_KEY', 'def\\\\s+\\\\w+'"},
                "path": {"type": "string", "description": "Directory to search in (default: root)"},
                "file_pattern": {"type": "string", "description": "File pattern to match (default: all files). e.g.: '*.md', '*.py'"},
                "ignore_case": {"type": "boolean", "description": "Case-insensitive search (default: false)"},
            },
            "required": ["pattern"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "find_files",
        "display_name": "Find Files",
        "description": "Find files matching glob patterns. Returns file paths with sizes and modification info. Results capped at 100 per query.",
        "category": "file",
        "icon": "📁",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern to match files, e.g.: '**/*.md', 'skills/*.md'"},
                "path": {"type": "string", "description": "Base directory for search (default: root)"},
            },
            "required": ["pattern"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "read_document",
        "display_name": "Read Document",
        "description": "Read office document contents (PDF, Word, Excel, PPT) and extract text.",
        "category": "file",
        "icon": "📑",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Document file path, e.g.: workspace/report.pdf"}
            },
            "required": ["path"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "convert_csv_to_xlsx",
        "display_name": "CSV to Excel",
        "description": "Convert a CSV source file into an Excel .xlsx file. Create/edit the CSV first, then use this tool.",
        "category": "file",
        "icon": "📊",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "Path to the source CSV file"},
                "target_path": {"type": "string", "description": "Path for the output Excel file (.xlsx)"},
            },
            "required": ["source_path", "target_path"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "convert_html_to_pdf",
        "display_name": "HTML to PDF",
        "description": "Convert an HTML source file into a PDF document. Uses headless Chrome by default for higher-fidelity rendering of modern CSS and screen layouts, with WeasyPrint as a fallback.",
        "category": "file",
        "icon": "📄",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "Path to the source HTML file"},
                "target_path": {"type": "string", "description": "Path for the output PDF file (.pdf)"},
                "design_width": {"type": "number", "description": "Optional browser viewport width in pixels, default 1280"},
                "design_height": {"type": "number", "description": "Optional browser viewport height in pixels, default 720"},
                "pdf_mode": {"type": "string", "enum": ["pages", "single"], "description": "pages outputs paginated PDF, single outputs one long full-page PDF. Default: pages"},
                "scale": {"type": "number", "description": "Optional Chrome PDF scale for paginated output, default 0.64"},
                "paper_width": {"type": "number", "description": "Optional paper width in inches for paginated output, default 8.27"},
                "paper_height": {"type": "number", "description": "Optional paper height in inches for paginated output, default 11.69"},
            },
            "required": ["source_path", "target_path"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "convert_html_to_pptx",
        "display_name": "HTML to PowerPoint",
        "description": "Convert an HTML source file into a PowerPoint .pptx file. By default, render_mode='editable' opens the HTML in headless Chrome, samples real element positions/styles, and maps explicit .slide/data-slide nodes or top-level page sections into editable PPT elements. Use render_mode='visual' as a high-fidelity screenshot fallback when exact visual preservation is more important than editability.",
        "category": "file",
        "icon": "📽️",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "Path to the source HTML file"},
                "target_path": {"type": "string", "description": "Path for the output PowerPoint file (.pptx)"},
                "design_width": {"type": "number", "description": "Optional source design width in pixels, default 1280"},
                "design_height": {"type": "number", "description": "Optional source design height in pixels, default 720"},
                "render_mode": {"type": "string", "enum": ["editable", "visual"], "description": "editable maps HTML/CSS into editable PPT elements using Chrome layout sampling; visual preserves styling with Chrome-rendered screenshots as a fallback. Default: editable"},
                "render_scale": {"type": "number", "description": "Optional Chrome raster scale for screenshots and complex CSS captures. Higher values improve sharpness but increase PPTX size. Default: 2, clamped between 1 and 4"},
            },
            "required": ["source_path", "target_path"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "convert_markdown_to_docx",
        "display_name": "Markdown to Word",
        "description": "Convert a Markdown source file into a Word .docx file.",
        "category": "file",
        "icon": "📝",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "Path to the source Markdown file"},
                "target_path": {"type": "string", "description": "Path for the output Word file (.docx)"},
            },
            "required": ["source_path", "target_path"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "convert_markdown_to_pdf",
        "display_name": "Markdown to PDF",
        "description": "Convert a Markdown source file into a PDF document.",
        "category": "file",
        "icon": "📄",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "Path to the source Markdown file"},
                "target_path": {"type": "string", "description": "Path for the output PDF file (.pdf)"},
            },
            "required": ["source_path", "target_path"],
        },
        "config": {},
        "config_schema": {},
    },
    # --- Aware trigger management tools ---
    {
        "name": "set_trigger",
        "display_name": "Set Trigger",
        "description": "Set a new trigger to wake yourself up at a specific time or condition. Every trigger is attached to a focus item; if focus_ref is omitted, the system creates one from the reason. The reason must be self-contained because it becomes the future Run directive.",
        "category": "aware",
        "icon": "⚡",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Unique name for this trigger"},
                "type": {"type": "string", "enum": ["cron", "once", "interval", "poll", "on_message", "webhook"], "description": "Trigger type"},
                "config": {"type": "object", "description": "Type-specific config. cron: {\"expr\": \"0 9 * * *\"}. once: {\"at\": \"2026-03-10T09:00:00+08:00\"}. interval: {\"minutes\": 30}. poll: {\"url\": \"...\", \"json_path\": \"$.status\"}. on_message: {\"from_agent_name\": \"Morty\"} or {\"from_user_name\": \"张三\"}"},
                "reason": {"type": "string", "minLength": 1, "description": "Self-contained instruction describing exactly what to do when this trigger fires."},
                "focus_ref": {"type": "string", "description": "Optional: which focus item this relates to. If omitted, one is created automatically."},
            },
            "required": ["name", "type", "config", "reason"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "update_trigger",
        "display_name": "Update Trigger",
        "description": "Patch an existing trigger's user configuration or reason. Omitted config keys and internal routing/webhook keys are preserved.",
        "category": "aware",
        "icon": "🔄",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the trigger to update"},
                "config": {"type": "object", "description": "User config fields to patch; this does not replace internal keys."},
                "reason": {"type": "string", "description": "New reason text"},
            },
            "required": ["name"],
            "anyOf": [
                {"required": ["config"]},
                {"required": ["reason"]},
            ],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "cancel_trigger",
        "display_name": "Cancel Trigger",
        "description": "Cancel (disable) a trigger by name. Use when a task is completed.",
        "category": "aware",
        "icon": "⏹️",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the trigger to cancel"},
            },
            "required": ["name"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "list_triggers",
        "display_name": "List Triggers",
        "description": "List all triggers, including active and disabled entries, with name, type, config, reason, fire count, and status.",
        "category": "aware",
        "icon": "📋",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {},
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "send_channel_file",
        "display_name": "Send File",
        "description": "Send a file to a human from query_directory or back to the current conversation. Use query_directory(member_type='human') first, then pass target_member_id.",
        "category": "communication",
        "icon": "📎",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Workspace-relative path to the file"},
                "target_member_id": {"type": "string", "description": "Stable human target_member_id returned by query_directory."},
                "channel": {"type": "string", "enum": ["feishu", "slack"], "description": "Optional channel override when the Directory member has multiple reachable providers."},
                "message": {"type": "string", "description": "Optional message to accompany the file"},
            },
            "required": ["file_path"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "query_directory",
        "display_name": "Query Directory",
        "description": "Query the people and digital employees this agent can see in its Directory. Use this before recommending or contacting a colleague.",
        "category": "communication",
        "icon": "📇",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional search keyword for name, role, title, department, or skill."},
                "target_member_id": {"type": "string", "description": "Optional exact human member ID returned by query_directory. Use this to verify one specific person."},
                "member_type": {"type": "string", "enum": ["all", "agent", "human"], "description": "Filter by member type. Defaults to all."},
                "include_uncontactable": {"type": "boolean", "description": "Whether to include members that are visible but currently unavailable. Defaults to false. This never returns invisible members."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Maximum number of members to return. Defaults to 20."},
                "offset": {"type": "integer", "minimum": 0, "description": "Number of matching members to skip. Defaults to 0."},
            },
            "required": [],
        },
        "config": {},
        "config_schema": {},
    },
    # NOTE: send_feishu_message is defined in the 'feishu' category section below.
    # It was previously duplicated here under 'communication', which could cause
    # 'Tool names must be unique' errors when the DB lacked a UNIQUE constraint.
    {
        "name": "send_platform_message",
        "display_name": "Platform Message",
        "description": "Send a proactive message to a human colleague on the Clawith first-party platform (web or app). Use query_directory first, then pass target_member_id or platform_user_id.",
        "category": "communication",
        "icon": "🌐",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "target_member_id": {"type": "string", "description": "Stable human member ID returned by query_directory. Preferred recipient identifier."},
                "platform_user_id": {"type": "string", "description": "Platform user ID returned by query_directory for first-party platform users."},
                "message": {"type": "string", "description": "Message content"},
            },
            "required": ["message"],
            "anyOf": [
                {"required": ["target_member_id"]},
                {"required": ["platform_user_id"]},
            ],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "send_channel_message",
        "display_name": "Channel Message",
        "description": "Send a message to a human colleague via their configured external channel (Feishu, DingTalk, WeCom, Slack, Teams, WeChat). Use query_directory first, then pass target_member_id.",
        "category": "communication",
        "icon": "💬",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "target_member_id": {"type": "string", "description": "Stable human member ID returned by query_directory. Preferred recipient identifier."},
                "message": {"type": "string", "description": "Message content"},
                "channel": {
                    "type": "string",
                    "description": "Optional: specific external channel to use.",
                    "enum": ["feishu", "dingtalk", "wecom", "slack", "teams", "microsoft_teams", "wechat"],
                },
            },
            "required": ["target_member_id", "message"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "send_message_to_agent",
        "display_name": "Agent Message",
        "description": "Send a private A2A message to a digital employee from query_directory. notify completes after the durable send. consult and task_delegate create a delegated Run; this Run waits and resumes with the correlated result, so do not poll or send the same request again.",
        "category": "communication",
        "icon": "🤖",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "target_agent_id": {"type": "string", "description": "Target digital employee ID returned by query_directory"},
                "message": {"type": "string", "description": "Message content"},
                "msg_type": {"type": "string", "enum": ["notify", "consult", "task_delegate"], "description": "(1) Target needs to DO WORK and return results? → task_delegate. (2) Just FYI? → notify. (3) Quick factual question? → consult. When unsure, prefer task_delegate."},
            },
            "required": ["target_agent_id", "message", "msg_type"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "send_file_to_agent",
        "display_name": "Agent File Transfer",
        "description": "Send a workspace file to another digital employee. Use query_directory first to get target_agent_id. The file is copied to the target agent's workspace/inbox/files/ and an inbox note is created.",
        "category": "communication",
        "icon": "📤",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "target_agent_id": {"type": "string", "description": "Target digital employee ID returned by query_directory"},
                "file_path": {"type": "string", "description": "Workspace-relative source file path"},
                "message": {"type": "string", "description": "Optional delivery note"},
            },
            "required": ["target_agent_id", "file_path"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "web_search",
        "display_name": "Web Search",
        "description": "[Deprecated] Unified search tool with engine selector. Use the dedicated tools (DuckDuckGo Search, Tavily Search, Google Search, Bing Search, Exa Search) instead for better control per engine.",
        "category": "search",
        "icon": "🔍",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords"},
                "max_results": {"type": "integer", "description": "Number of results to return"},
            },
            "required": ["query"],
        },
        "config": {
            "search_engine": "duckduckgo",
            "max_results": 5,
            "language": "en",
            "api_key": "",
        },
        "config_schema": {
            "fields": [
                {
                    "key": "search_engine",
                    "label": "Search Engine",
                    "type": "select",
                    "options": [
                        {"value": "duckduckgo", "label": "DuckDuckGo (free, no API key)"},
                        {"value": "tavily", "label": "Tavily (AI search, needs API key)"},
                        {"value": "google", "label": "Google Custom Search (needs API key)"},
                        {"value": "bing", "label": "Bing Search API (needs API key)"},
                        {"value": "exa", "label": "Exa (AI-powered search, needs API key)"},
                    ],
                    "default": "duckduckgo",
                },
                {
                    "key": "api_key",
                    "label": "API Key",
                    "type": "password",
                    "default": "",
                    "placeholder": "Required for engines that need an API key",
                    "depends_on": {"search_engine": ["tavily", "google", "bing", "exa"]},
                },
                {
                    "key": "max_results",
                    "label": "Default results count",
                    "type": "number",
                    "default": 5,
                    "min": 1,
                    "max": 20,
                },
                {
                    "key": "language",
                    "label": "Search language",
                    "type": "select",
                    "options": [
                        {"value": "en", "label": "English"},
                        {"value": "zh-CN", "label": "中文"},
                        {"value": "ja", "label": "日本語"},
                    ],
                    "default": "en",
                },
            ]
        },
    },
    {
        "name": "jina_search",
        "display_name": "Jina Search",
        "description": "Search the internet using Jina AI (s.jina.ai). Returns high-quality results with full content. Requires Jina AI API key for higher rate limits.",
        "category": "search",
        "icon": "🔮",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords"},
                "max_results": {"type": "integer", "description": "Number of results (default 5, max 10)"},
            },
            "required": ["query"],
        },
        "config": {},
        "config_schema": {
            "fields": [
                {
                    "key": "api_key",
                    "label": "Jina AI API Key",
                    "type": "password",
                    "default": "",
                    "placeholder": "jina_xxxxxxxxxxxxxxxx (get one at jina.ai)",
                },
            ]
        },
    },
    {
        "name": "jina_read",
        "display_name": "Jina Read",
        "description": "Read and extract full content from a URL using Jina AI Reader (r.jina.ai). Returns clean markdown. Requires Jina AI API key for higher rate limits.",
        "category": "search",
        "icon": "📖",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL to read"},
                "max_chars": {"type": "integer", "description": "Max characters to return (default 8000)"},
            },
            "required": ["url"],
        },
        "config": {},
        "config_schema": {
            "fields": [
                {
                    "key": "api_key",
                    "label": "Jina AI API Key",
                    "type": "password",
                    "default": "",
                    "placeholder": "jina_xxxxxxxxxxxxxxxx (get one at jina.ai)",
                },
            ]
        },
    },
    {
        "name": "read_webpage",
        "display_name": "Read Webpage",
        "description": "Fetch a public HTTP/HTTPS URL directly and extract readable webpage text. Use this when you already have a specific link and need its page content without relying on an external reader service.",
        "category": "search",
        "icon": "🌐",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full public HTTP/HTTPS URL to read"},
                "max_chars": {"type": "integer", "description": "Max characters to return (default 12000, max 50000)"},
                "include_links": {"type": "boolean", "description": "Whether to include extracted page links (default false)"},
            },
            "required": ["url"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "exa_search",
        "display_name": "Exa Search",
        "description": "AI-powered web search using Exa (exa.ai). Supports semantic search, category filtering, domain filtering, and multiple content modes (text, highlights, summary). Requires an Exa API key.",
        "category": "search",
        "icon": "🔎",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Number of results (default 5, max 10)"},
                "search_type": {
                    "type": "string",
                    "description": "Search type: auto (default), neural, or fast",
                    "enum": ["auto", "neural", "fast"],
                },
                "category": {
                    "type": "string",
                    "description": "Filter by category: company, research paper, news, personal site, financial report, or people",
                },
                "include_domains": {
                    "type": "string",
                    "description": "Comma-separated domains to restrict results to (e.g. 'arxiv.org, github.com')",
                },
                "exclude_domains": {
                    "type": "string",
                    "description": "Comma-separated domains to exclude from results",
                },
                "content_mode": {
                    "type": "string",
                    "description": "Content retrieval mode: text (default), highlights, or summary",
                    "enum": ["text", "highlights", "summary"],
                },
            },
            "required": ["query"],
        },
        "config": {},
        "config_schema": {
            "fields": [
                {
                    "key": "api_key",
                    "label": "Exa API Key",
                    "type": "password",
                    "default": "",
                    "placeholder": "Get your API key at exa.ai",
                },
            ]
        },
    },
    # ── Standalone search engines (each engine as its own tool) ──────────────
    # These complement web_search (which remains for backward compatibility).
    # Each tool wraps a single engine so agents can pick the right one for the
    # task without going through the unified engine-selector flow.
    {
        "name": "duckduckgo_search",
        "display_name": "DuckDuckGo Search",
        "description": "Search the internet using DuckDuckGo. Free, no API key required. Returns titles, URLs, and snippets.",
        "category": "search",
        "icon": "🦆",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords"},
                "max_results": {"type": "integer", "description": "Number of results to return (default 5, max 10)"},
            },
            "required": ["query"],
        },
        "config": {},
        "config_schema": {"fields": []},
    },
    {
        "name": "tavily_search",
        "display_name": "Tavily Search",
        "description": "AI-optimized web search using Tavily. Returns high-quality results with summaries. Requires a Tavily API key.",
        "category": "search",
        "icon": "🔍",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords"},
                "max_results": {"type": "integer", "description": "Number of results to return (default 5, max 10)"},
            },
            "required": ["query"],
        },
        "config": {},
        "config_schema": {
            "fields": [
                {
                    "key": "api_key",
                    "label": "Tavily API Key",
                    "type": "password",
                    "default": "",
                    "placeholder": "tvly-xxxxxxxxxxxxxxxx (get one at tavily.com)",
                },
            ]
        },
    },
    {
        "name": "google_search",
        "display_name": "Google Search",
        "description": "Search using Google Custom Search JSON API. Returns titles, URLs, and snippets. Requires a Google API key and Custom Search Engine ID (format: API_KEY:CX_ID).",
        "category": "search",
        "icon": "🔍",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords"},
                "max_results": {"type": "integer", "description": "Number of results to return (default 5, max 10)"},
                "language": {"type": "string", "description": "Search language code (e.g. 'en', 'zh')"},
            },
            "required": ["query"],
        },
        "config": {"language": "en"},
        "config_schema": {
            "fields": [
                {
                    "key": "api_key",
                    "label": "API Key & Search Engine ID",
                    "type": "password",
                    "default": "",
                    "placeholder": "API_KEY:SEARCH_ENGINE_ID (get at console.cloud.google.com)",
                },
                {
                    "key": "language",
                    "label": "Search language",
                    "type": "select",
                    "options": [
                        {"value": "en", "label": "English"},
                        {"value": "zh-CN", "label": "Chinese"},
                        {"value": "ja", "label": "Japanese"},
                    ],
                    "default": "en",
                },
            ]
        },
    },
    {
        "name": "bing_search",
        "display_name": "Bing Search",
        "description": "Search using Bing Web Search API. Returns titles, URLs, and snippets. Requires a Bing Search API key from Microsoft Azure.",
        "category": "search",
        "icon": "🔍",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords"},
                "max_results": {"type": "integer", "description": "Number of results to return (default 5, max 10)"},
                "language": {"type": "string", "description": "Market language code (e.g. 'en-US', 'zh-CN')"},
            },
            "required": ["query"],
        },
        "config": {"language": "en-US"},
        "config_schema": {
            "fields": [
                {
                    "key": "api_key",
                    "label": "Bing Search API Key",
                    "type": "password",
                    "default": "",
                    "placeholder": "Get from Azure Cognitive Services (Bing Search v7)",
                },
                {
                    "key": "language",
                    "label": "Market language",
                    "type": "select",
                    "options": [
                        {"value": "en-US", "label": "English (US)"},
                        {"value": "zh-CN", "label": "Chinese (Simplified)"},
                        {"value": "ja-JP", "label": "Japanese"},
                    ],
                    "default": "en-US",
                },
            ]
        },
    },
    # Plaza social tools (plaza_get_new_posts / plaza_create_post / plaza_add_comment)
    # were removed in the Plaza → experience library改造 (P0-1: no AI auto-posting).
    # Experience library — AI consumption side (hybrid pull, read-only).
    {
        "name": "search_experience",
        "display_name": "Experience: Search",
        "description": (
            "Search the team's private experience library by keyword before doing work that touches "
            "internal systems, internal processes, or a private/self-hosted environment. Returns lightweight "
            "candidates (title + applicability). Only entries visible to you are returned."
        ),
        "category": "knowledge",
        "icon": "🔎",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "Keywords describing your current situation/problem."},
            },
            "required": ["keyword"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "read_experience",
        "display_name": "Experience: Read",
        "description": (
            "Read the full four-part text (场景/问题/解决/适用条件与失效信号) of one experience entry when its "
            "applicability matches your situation. If it informs your answer, cite it with [[exp:<entry_id>]]."
        ),
        "category": "knowledge",
        "icon": "📚",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "The entry id from search_experience results."},
            },
            "required": ["entry_id"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "propose_experience_draft",
        "display_name": "Experience: Propose Draft",
        "description": (
            "当用户要求你把某条经验『记成经验 / 沉淀』时调用本工具。**此工具不写入任何存储，"
            "仅将结构化草稿呈现给用户确认**——用户点击『沉淀为经验』并人工确认后才会由人落库。"
            "你无权直接写入团队经验库，也不要把它写进 memory 或 workspace。"
            "title、body、applicability 三者必填，尤其 applicability（适用条件与失效信号）。"
        ),
        "category": "knowledge",
        "icon": "📝",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "简短标题"},
                "body": {
                    "type": "string",
                    "description": (
                        "经验正文，markdown 格式。默认用「## 场景 / ## 遇到的问题 / ## 解决方式」三个小节；"
                        "若内容不是「问题—解决」型（如一份配置说明、一条参考事实），按内容自然组织小节即可，不要硬套。"
                    ),
                },
                "applicability": {
                    "type": "string",
                    "description": (
                        "适用条件与失效信号（必填）：此经验在什么前提下成立、出现什么信号说明它已过时失效。"
                        "它会脱离正文单独展示给检索方判断是否适用，必须能独立读懂。"
                    ),
                },
                "tags": {"type": "array", "items": {"type": "string"}, "description": "1-3 个简短标签"},
            },
            "required": ["title", "body", "applicability"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "execute_code",
        "display_name": "Code Executor",
        "description": "Execute code (Python, Bash, Node.js) in a local sandboxed subprocess within the agent's workspace. Useful for data processing, calculations, file transformations, and automation.",
        "category": "code",
        "icon": "💻",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "language": {"type": "string", "enum": ["python", "bash", "node"], "description": "Programming language"},
                "code": {"type": "string", "description": "Code to execute"},
                "timeout": {"type": "integer", "minimum": 1, "description": "Execution timeout in seconds. Defaults to 30 and is capped by this tool's current max_timeout configuration."},
            },
            "required": ["language", "code"],
        },
        "config": {
            "sandbox_type": "subprocess",
            "cpu_limit": "0.5",
            "memory_limit": "256m",
            "allow_network": True,
            "default_timeout": 30,
            "max_timeout": 60,
        },
        "config_schema": {
            "fields": [
                {
                    "key": "cpu_limit",
                    "label": "CPU Limit",
                    "type": "text",
                    "default": "0.5",
                    "placeholder": "e.g., 0.5, 1.0, 2.0",
                },
                {
                    "key": "memory_limit",
                    "label": "Memory Limit",
                    "type": "text",
                    "default": "256m",
                    "placeholder": "e.g., 256m, 512m, 1g",
                },
                {
                    "key": "allow_network",
                    "label": "Allow Network Access",
                    "type": "checkbox",
                    "default": True,
                    "read_only_for_roles": ["agent_admin", "member"],
                },
                {
                    "key": "default_timeout",
                    "label": "Default Timeout (seconds)",
                    "type": "number",
                    "default": 30,
                    "min": 5,
                    "max": 3600,
                },
                {
                    "key": "max_timeout",
                    "label": "Max Timeout (seconds)",
                    "type": "number",
                    "default": 60,
                    "min": 10,
                    "max": 3600,
                },
            ]
        },
    },
    {
        "name": "execute_code_e2b",
        "display_name": "Code Executor (E2B Cloud)",
        "description": "Execute code (Python, Bash, Node.js) in a secure E2B cloud sandbox. Provides full network access and an isolated environment without consuming local resources. Requires an E2B API key.",
        "category": "code",
        "icon": "☁️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "language": {"type": "string", "enum": ["python", "bash", "node"], "description": "Programming language"},
                "code": {"type": "string", "description": "Code to execute"},
                "timeout": {"type": "integer", "description": "Max execution time in seconds (default 30, max 60)"},
            },
            "required": ["language", "code"],
        },
        "config": {
            "sandbox_type": "e2b",
            "api_key": "",
            "default_timeout": 30,
            "max_timeout": 60,
        },
        "config_schema": {
            "fields": [
                {
                    "key": "api_key",
                    "label": "E2B API Key",
                    "type": "password",
                    "default": "",
                    "placeholder": "Get your API key at https://e2b.dev",
                    "required": True,
                },
                {
                    "key": "default_timeout",
                    "label": "Default Timeout (seconds)",
                    "type": "number",
                    "default": 30,
                    "min": 5,
                    "max": 3600,
                },
                {
                    "key": "max_timeout",
                    "label": "Max Timeout (seconds)",
                    "type": "number",
                    "default": 60,
                    "min": 10,
                    "max": 3600,
                },
            ]
        },
    },

    {
        "name": "upload_image",
        "display_name": "Upload Image",
        "description": "Upload images from the workspace or a URL to ImageKit CDN and get a public URL. Useful for sharing images externally or embedding them in reports.",
        "category": "code",
        "icon": "🖼️",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Workspace-relative path to image file"},
                "url": {
                    "type": "string",
                    "format": "uri",
                    "description": "Public HTTP(S) URL of image to upload",
                },
                "file_name": {"type": "string", "description": "Custom filename (optional)"},
                "folder": {"type": "string", "description": "CDN folder path (default /clawith)"},
            },
            "oneOf": [
                {"required": ["file_path"]},
                {"required": ["url"]},
            ],
        },
        "config": {"private_key": "", "url_endpoint": ""},
        "config_schema": {
            "fields": [
                {
                    "key": "private_key",
                    "label": "ImageKit Private Key",
                    "type": "password",
                    "default": "",
                    "placeholder": "Your ImageKit private API key",
                },
                {
                    "key": "url_endpoint",
                    "label": "ImageKit URL Endpoint",
                    "type": "text",
                    "default": "",
                    "placeholder": "https://ik.imagekit.io/your_imagekit_id",
                },
            ]
        },
    },
    {
        "name": "generate_image_siliconflow",
        "display_name": "Generate Image (SiliconFlow)",
        "description": "Generate an image via SiliconFlow FLUX models. China-friendly and fast.",
        "category": "media",
        "icon": "🎨",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "minLength": 1, "description": "Detailed image description."},
                "size": {
                    "type": "string",
                    "enum": ["1024x1024", "1024x768", "768x1024", "1366x768", "768x1366", "1536x1024", "1024x1536"],
                    "description": "Image size. Default 1024x1024.",
                },
                "save_path": {
                    "type": "string",
                    "pattern": "^(?!/)(?!.*(?:^|/)\\.\\.(?:/|$)).+\\.(?:png|jpg|jpeg|webp)$",
                    "description": "Workspace-relative image path. Default: auto.",
                },
            },
            "required": ["prompt"],
        },
        "config": {
            "model": "",
            "api_key": "",
            "base_url": "",
        },
        "config_schema": {
            "fields": [
                {
                    "key": "model",
                    "label": "Model",
                    "type": "text",
                    "default": "",
                    "placeholder": "e.g. black-forest-labs/FLUX.1-schnell",
                },
                {
                    "key": "api_key",
                    "label": "API Key",
                    "type": "password",
                    "default": "",
                    "placeholder": "SiliconFlow API Key",
                },
                {
                    "key": "base_url",
                    "label": "Base URL (optional)",
                    "type": "text",
                    "default": "",
                    "placeholder": "Default: https://api.siliconflow.cn/v1",
                },
            ]
        },
    },
    {
        "name": "generate_image_openai",
        "display_name": "Generate Image (OpenAI)",
        "description": "Generate an image via OpenAI DALL-E models.",
        "category": "media",
        "icon": "🎨",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "minLength": 1, "description": "Detailed image description."},
                "size": {
                    "type": "string",
                    "enum": ["1024x1024", "1024x768", "768x1024", "1366x768", "768x1366", "1536x1024", "1024x1536"],
                    "description": "Image size. Default 1024x1024.",
                },
                "save_path": {
                    "type": "string",
                    "pattern": "^(?!/)(?!.*(?:^|/)\\.\\.(?:/|$)).+\\.(?:png|jpg|jpeg|webp)$",
                    "description": "Workspace-relative image path. Default: auto.",
                },
            },
            "required": ["prompt"],
        },
        "config": {
            "model": "",
            "api_key": "",
            "base_url": "",
        },
        "config_schema": {
            "fields": [
                {
                    "key": "model",
                    "label": "Model",
                    "type": "text",
                    "default": "",
                    "placeholder": "e.g. dall-e-3 or dall-e-2",
                },
                {
                    "key": "api_key",
                    "label": "API Key",
                    "type": "password",
                    "default": "",
                    "placeholder": "OpenAI API Key",
                },
                {
                    "key": "base_url",
                    "label": "Base URL (optional)",
                    "type": "text",
                    "default": "",
                    "placeholder": "Default: https://api.openai.com/v1",
                },
            ]
        },
    },
    {
        "name": "generate_image_google",
        "display_name": "Generate Image (Google/Vertex)",
        "description": "Generate an image via Google Gemini Image (Nano Banana) or Vertex AI.",
        "category": "media",
        "icon": "🎨",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "minLength": 1, "description": "Detailed image description."},
                "size": {
                    "type": "string",
                    "enum": ["1024x1024", "1024x768", "768x1024", "1366x768", "768x1366", "1536x1024", "1024x1536"],
                    "description": "Image size. Default 1024x1024.",
                },
                "save_path": {
                    "type": "string",
                    "pattern": "^(?!/)(?!.*(?:^|/)\\.\\.(?:/|$)).+\\.(?:png|jpg|jpeg|webp)$",
                    "description": "Workspace-relative image path. Default: auto.",
                },
            },
            "required": ["prompt"],
        },
        "config": {
            "model": "",
            "api_key": "",
            "base_url": "",
        },
        "config_schema": {
            "fields": [
                {
                    "key": "model",
                    "label": "Model",
                    "type": "text",
                    "default": "",
                    "placeholder": "e.g. gemini-2.5-flash-image",
                },
                {
                    "key": "api_key",
                    "label": "API Key",
                    "type": "password",
                    "default": "",
                    "placeholder": "Google AI Studio or Vertex API Key",
                },
                {
                    "key": "base_url",
                    "label": "Base URL (optional)",
                    "type": "text",
                    "default": "",
                    "placeholder": "Can be Vertex API URL: https://aiplatform.googleapis.com/...",
                },
            ]
        },
    },
    {
        "name": "generate_image_custom",
        "display_name": "Generate Image (Custom API)",
        "description": "Generate an image through a custom OpenAI-compatible or gateway API. Configure the request body template and response image path for providers such as TokenRouter or OpenRouter.",
        "category": "media",
        "icon": "🎨",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "minLength": 1, "description": "Detailed image description."},
                "size": {
                    "type": "string",
                    "enum": ["1024x1024", "1024x768", "768x1024", "1366x768", "768x1366", "1536x1024", "1024x1536"],
                    "description": "Image size. Default 1024x1024.",
                },
                "save_path": {
                    "type": "string",
                    "pattern": "^(?!/)(?!.*(?:^|/)\\.\\.(?:/|$)).+\\.(?:png|jpg|jpeg|webp)$",
                    "description": "Workspace-relative image path. Default: auto.",
                },
            },
            "required": ["prompt"],
        },
        "config": {
            "api_key": "",
            "base_url": "",
            "endpoint_path": "/chat/completions",
            "model": "",
            "request_body_template_json": "{\n  \"model\": \"{model}\",\n  \"messages\": [\n    {\n      \"role\": \"user\",\n      \"content\": \"{prompt}\"\n    }\n  ],\n  \"modalities\": [\"image\", \"text\"],\n  \"stream\": false\n}",
            "response_image_path": "choices.0.message.images.0.image_url.url",
            "extra_headers_json": "",
            "timeout_seconds": 120,
        },
        "config_schema": {
            "fields": [
                {
                    "key": "api_key",
                    "label": "API Key",
                    "type": "password",
                    "default": "",
                    "placeholder": "API key for your image generation gateway",
                },
                {
                    "key": "model",
                    "label": "Model",
                    "type": "text",
                    "default": "",
                    "placeholder": "e.g. google/gemini-2.5-flash-image",
                },
                {
                    "key": "base_url",
                    "label": "Base URL",
                    "type": "text",
                    "default": "",
                    "placeholder": "e.g. https://api.tokenrouter.com/v1 or https://openrouter.ai/api/v1",
                },
                {
                    "key": "endpoint_path",
                    "label": "Endpoint Path",
                    "type": "text",
                    "default": "/chat/completions",
                    "placeholder": "/chat/completions",
                    "advanced": True,
                },
                {
                    "key": "request_body_template_json",
                    "label": "Request Body Template JSON",
                    "type": "textarea",
                    "default": "{\n  \"model\": \"{model}\",\n  \"messages\": [\n    {\n      \"role\": \"user\",\n      \"content\": \"{prompt}\"\n    }\n  ],\n  \"modalities\": [\"image\", \"text\"],\n  \"stream\": false\n}",
                    "placeholder": "{\n  \"model\": \"{model}\",\n  \"messages\": [{\"role\": \"user\", \"content\": \"{prompt}\"}],\n  \"modalities\": [\"image\", \"text\"],\n  \"stream\": false\n}",
                    "advanced": True,
                },
                {
                    "key": "response_image_path",
                    "label": "Response Image Path",
                    "type": "text",
                    "default": "choices.0.message.images.0.image_url.url",
                    "placeholder": "choices.0.message.images.0.image_url.url",
                    "advanced": True,
                },
                {
                    "key": "extra_headers_json",
                    "label": "Extra Headers JSON",
                    "type": "textarea",
                    "default": "",
                    "placeholder": "{\n  \"HTTP-Referer\": \"https://your-app.example\",\n  \"X-Title\": \"Clawith\"\n}",
                    "advanced": True,
                },
                {
                    "key": "timeout_seconds",
                    "label": "Timeout Seconds",
                    "type": "number",
                    "default": 120,
                    "min": 10,
                    "max": 600,
                    "advanced": True,
                },
            ]
        },
    },
    {
        "name": "discover_resources",
        "display_name": "Resource Discovery",
        "description": "Search public MCP registries (Smithery + ModelScope) for tools and capabilities that can extend your abilities. Use this when you encounter a task you cannot handle with your current tools.",
        "category": "discovery",
        "icon": "🔎",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Semantic description of the capability needed, e.g. 'send email', 'query SQL database', 'generate images'"},
                "max_results": {"type": "integer", "description": "Max results to return (default 5, max 10)"},
            },
            "required": ["query"],
        },
        "config": {},
        "config_schema": {
            "fields": [
                {
                    "key": "smithery_api_key",
                    "label": "Smithery API Key",
                    "type": "password",
                    "default": "",
                    "placeholder": "Get your key at smithery.ai/account/api-keys",
                },
                {
                    "key": "modelscope_api_token",
                    "label": "ModelScope API Token",
                    "type": "password",
                    "default": "",
                    "placeholder": "Get your token at modelscope.cn → Home → Access Tokens",
                },
            ]
        },
    },
    {
        "name": "import_mcp_server",
        "display_name": "Import MCP Server",
        "description": "Import an MCP server from Smithery registry into the platform. The server's tools become available for use. Use discover_resources first to find the server ID.",
        "category": "discovery",
        "icon": "📥",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "server_id": {"type": "string", "description": "Smithery server ID, e.g. '@anthropic/brave-search' or '@anthropic/fetch'"},
                "config": {"type": "object", "description": "Optional server configuration (e.g. API keys required by the server)"},
                "reauthorize": {"type": "boolean", "default": False, "description": "Retry authorization for an existing server connection."},
            },
            "required": ["server_id"],
        },
        "config": {},
        "config_schema": {
            "fields": [
                {
                    "key": "smithery_api_key",
                    "label": "Smithery API Key",
                    "type": "password",
                    "default": "",
                    "placeholder": "Get your key at smithery.ai/account/api-keys",
                },
                {
                    "key": "modelscope_api_token",
                    "label": "ModelScope API Token",
                    "type": "password",
                    "default": "",
                    "placeholder": "Get your token at modelscope.cn → Home → Access Tokens",
                },
            ]
        },
    },
    # --- Email tools ---
    {
        "name": "send_email",
        "display_name": "Send Email",
        "description": "Send an email to one or more recipients. Supports subject, body text, CC, and file attachments from workspace.",
        "category": "email",
        "icon": "📧",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "minLength": 1, "description": "Recipient email address(es), comma-separated for multiple"},
                "subject": {"type": "string", "minLength": 1, "description": "Email subject line"},
                "body": {"type": "string", "minLength": 1, "description": "Email body text"},
                "cc": {"type": "string", "minLength": 1, "description": "CC recipients, comma-separated (optional)"},
                "attachments": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "description": "List of workspace-relative file paths to attach (optional). E.g. ['workspace/filename.ext']. Always specify this parameter if the user uploads a file or mentions sending/attaching a file.",
                },
            },
            "required": ["to", "subject", "body"],
        },
        "config": {},
        "config_schema": {
            "fields": [
                {
                    "key": "email_provider",
                    "label": "Email Provider",
                    "type": "select",
                    "options": [
                        {"value": "gmail", "label": "Gmail", "help_text": "Google Account → Security → App passwords → Generate app password", "help_url": "https://support.google.com/accounts/answer/185833"},
                        {"value": "outlook", "label": "Outlook / Microsoft 365", "help_text": "Microsoft Account → Security → App passwords", "help_url": "https://support.microsoft.com/en-us/account-billing/manage-app-passwords-for-two-step-verification-d6dc8c6d-4bf7-4851-ad95-6d07799387e9"},
                        {"value": "qq", "label": "QQ Mail", "help_text": "Settings → Account → POP3/IMAP/SMTP → Enable IMAP → Generate authorization code", "help_url": "https://service.mail.qq.com/detail/0/310"},
                        {"value": "163", "label": "163 Mail", "help_text": "Settings → POP3/SMTP/IMAP → Enable IMAP → Set authorization code", "help_url": "https://help.mail.163.com/faqDetail.do?code=d7a5dc8471cd0c0e8b4b8f4f8e49998b374173cfe9171305fa1ce630d7f67ac2"},
                        {"value": "qq_enterprise", "label": "Tencent Enterprise Mail", "help_text": "Enterprise Mail → Settings → Client-specific password → Generate new password", "help_url": "https://open.work.weixin.qq.com/help2/pc/18624"},
                        {"value": "aliyun", "label": "Alibaba Enterprise Mail", "help_text": "Use your email password directly", "help_url": ""},
                        {"value": "custom", "label": "Custom", "help_text": "Use the authorization code or app password from your email provider", "help_url": ""},
                    ],
                    "default": "gmail",
                },
                {
                    "key": "email_address",
                    "label": "Email Address",
                    "type": "text",
                    "placeholder": "your@email.com",
                },
                {
                    "key": "auth_code",
                    "label": "Authorization Code",
                    "type": "password",
                    "placeholder": "Authorization code (not your login password)",
                },
                {
                    "key": "imap_host",
                    "label": "IMAP Host",
                    "type": "text",
                    "placeholder": "imap.example.com",
                    "depends_on": {"email_provider": ["custom"]},
                },
                {
                    "key": "imap_port",
                    "label": "IMAP Port",
                    "type": "number",
                    "default": 993,
                    "depends_on": {"email_provider": ["custom"]},
                },
                {
                    "key": "smtp_host",
                    "label": "SMTP Host",
                    "type": "text",
                    "placeholder": "smtp.example.com",
                    "depends_on": {"email_provider": ["custom"]},
                },
                {
                    "key": "smtp_port",
                    "label": "SMTP Port",
                    "type": "number",
                    "default": 465,
                    "depends_on": {"email_provider": ["custom"]},
                },
            ]
        },
    },
    {
        "name": "read_emails",
        "display_name": "Read Emails",
        "description": "Read emails from your inbox. Can limit the number returned and search by criteria (e.g. FROM, SUBJECT, SINCE date).",
        "category": "email",
        "icon": "📬",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max number of emails to return (default 10, max 30)", "default": 10, "minimum": 1, "maximum": 30},
                "search": {"type": "string", "minLength": 1, "description": "IMAP search criteria, e.g. 'FROM \"john@example.com\"', 'SUBJECT \"meeting\"', 'SINCE 01-Mar-2026'. Default: all emails."},
                "folder": {"type": "string", "minLength": 1, "description": "Mailbox folder (default INBOX)", "default": "INBOX"},
            },
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "reply_email",
        "display_name": "Reply Email",
        "description": "Reply to an email by its Message-ID. Maintains the email thread with proper In-Reply-To headers.",
        "category": "email",
        "icon": "↩️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "minLength": 1, "description": "Message-ID of the email to reply to (from read_emails output)"},
                "body": {"type": "string", "minLength": 1, "description": "Reply body text"},
                "folder": {"type": "string", "minLength": 1, "description": "Mailbox folder containing the original message (default INBOX)", "default": "INBOX"},
            },
            "required": ["message_id", "body"],
        },
        "config": {},
        "config_schema": {},
    },
    # --- OKR Tools ---
    # These tools expose the OKR system to agents. Not default — assigned explicitly
    # to the OKR Agent and to other agents that want to self-report progress.
    {
        "name": "get_okr",
        "display_name": "Get OKR Board",
        "description": (
            "Get the full OKR board for the current period. Returns all Objectives and Key Results "
            "for the tenant, organized by company and member level. Includes objective_id values "
            "for every Objective and kr_id values for every Key Result, so you can update existing "
            "Objectives and KRs instead of creating duplicates. Used by the OKR Agent to generate "
            "progress reports and monitor team performance."
        ),
        "category": "okr",
        "icon": "🎯",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "period_start": {
                    "type": "string",
                    "description": "Optional: ISO date string (YYYY-MM-DD) to filter by period start. Defaults to current period.",
                },
                "period_end": {
                    "type": "string",
                    "description": "Optional: ISO date string (YYYY-MM-DD) to filter by period end.",
                },
            },
            "dependentRequired": {
                "period_start": ["period_end"],
                "period_end": ["period_start"],
            },
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "get_my_okr",
        "display_name": "My OKR",
        "description": (
            "Get your own OKR Objectives and Key Results for the current period. "
            "Returns a structured view of your goals, current progress values, plus objective_id and kr_id references "
            "you need to update existing OKRs correctly. Call this before changing progress, KR content, "
            "or Objective text so you reuse the current records instead of creating duplicates."
        ),
        "category": "okr",
        "icon": "🎯",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "period_start": {
                    "type": "string",
                    "description": "Optional: ISO date string (YYYY-MM-DD). Defaults to current period.",
                },
                "period_end": {
                    "type": "string",
                    "description": "Optional: ISO date string (YYYY-MM-DD).",
                },
            },
            "dependentRequired": {
                "period_start": ["period_end"],
                "period_end": ["period_start"],
            },
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "update_kr_progress",
        "display_name": "Update KR Progress",
        "description": (
            "Update the current progress value for a Key Result. Use get_my_okr first to obtain "
            "the kr_id. The status (on_track / at_risk / behind / completed) is automatically "
            "computed from the progress ratio, or you can override it explicitly. "
            "A progress log entry is recorded for full audit history."
        ),
        "category": "okr",
        "icon": "📈",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "kr_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "UUID of the Key Result to update. Get this from get_my_okr.",
                },
                "value": {
                    "type": "number",
                    "description": "New current value (e.g. 4.2 for a KR with target 5.0).",
                },
                "note": {
                    "type": "string",
                    "description": "Optional note explaining the progress update (e.g. 'Completed weekly review session').",
                },
                "status": {
                    "type": "string",
                    "enum": ["on_track", "at_risk", "behind", "completed"],
                    "description": "Optional: override the auto-computed status.",
                },
            },
            "required": ["kr_id", "value"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "update_kr_content",
        "display_name": "Update KR Content",
        "description": (
            "Update the content fields of one of YOUR OWN Key Results, such as title, target value, unit, "
            "focus reference, or status. Use get_my_okr first to obtain the kr_id. "
            "This tool is for changing KR definition/content, not reporting progress. "
            "If the user says to change, revise, adjust, or replace an existing KR target or wording, "
            "prefer this tool instead of create_key_result."
        ),
        "category": "okr",
        "icon": "✏️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "kr_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "UUID of the Key Result to update (from get_my_okr).",
                },
                "title": {
                    "type": "string",
                    "description": "Optional new KR title.",
                },
                "target_value": {
                    "type": "number",
                    "description": "Optional new target value.",
                },
                "unit": {
                    "type": "string",
                    "description": "Optional new unit label.",
                },
                "focus_ref": {
                    "type": "string",
                    "description": "Optional new focus file reference.",
                },
                "status": {
                    "type": "string",
                    "enum": ["on_track", "at_risk", "behind", "completed"],
                    "description": "Optional explicit status override.",
                },
            },
            "required": ["kr_id"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        # collect_okr_progress — legacy OKR Agent heartbeat collection path.
        # This replaces the need to contact each member individually.
        "name": "collect_okr_progress",
        "display_name": "Collect OKR Progress",
        "description": (
            "Legacy batch sync for reported KR progress. Prefer direct OKR tools such as "
            "get_my_okr and update_kr_progress for new work. Returns a summary of how many "
            "KRs were updated."
        ),
        "category": "okr",
        "icon": "📊",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "config": {"okr_agent_only": True},
        "config_schema": {},
    },
    {
        # generate_okr_report — OKR Agent calls this to produce a bounded report receipt.
        "name": "generate_okr_report",
        "display_name": "Generate OKR Report",
        "description": (
            "Generate a structured OKR progress report (daily or weekly) for the current "
            "period. The report summarizes all Objectives and Key Results, highlights items "
            "at risk or behind, and shows overall team health metrics. The report is saved "
            "to the database and to your workspace/reports/ folder. Returns a bounded receipt "
            "and reference to the stored report."
        ),
        "category": "okr",
        "icon": "📋",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "report_type": {
                    "type": "string",
                    "enum": ["daily", "weekly"],
                    "description": "Whether to generate a daily or weekly report.",
                },
            },
            "required": ["report_type"],
        },
        "config": {"okr_agent_only": True},
        "config_schema": {},
    },
    {
        # get_okr_settings — lets OKR Agent read the tenant's OKR configuration so it
        # can determine whether reports are due, what time they're scheduled, etc.
        "name": "get_okr_settings",
        "display_name": "Get OKR Settings",
        "description": (
            "Read the OKR configuration for this team, including whether daily/weekly "
            "reports are enabled, the configured report time, period frequency, and more. "
            "Use this at the start of your heartbeat to decide whether a report is due today."
        ),
        "category": "okr",
        "icon": "⚙️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "config": {"okr_agent_only": True},
        "config_schema": {},
    },
    {
        # create_objective — OKR Agent uses this after conversation-based confirmation
        # to create an O for the company, a user, or an agent. Only OKR Agent has this tool.
        "name": "create_objective",
        "display_name": "Create Objective",
        "description": (
            "Create an OKR Objective for the company, a specific user, or a specific agent. "
            "Call this after confirming the objective with the relevant person through conversation. "
            "Use this only when a new Objective needs to be created for the period. "
            "If the person already has a matching Objective and just wants to revise it, use update_objective instead. "
            "owner_type must be 'company', 'user', or 'agent'. "
            "owner_id is not required for company-level objectives. "
            "period_start and period_end must be ISO date strings (YYYY-MM-DD)."
        ),
        "category": "okr",
        "icon": "🎯",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The objective title (concise, inspiring, directional).",
                },
                "description": {
                    "type": "string",
                    "description": "Optional detailed description of the objective.",
                },
                "owner_type": {
                    "type": "string",
                    "enum": ["company", "user", "agent"],
                    "description": "Who this objective belongs to.",
                },
                "owner_id": {
                    "type": "string",
                    "description": "UUID of the owner. Try to use this if available in context.",
                },
                "owner_name": {
                    "type": "string",
                    "description": "Optional fallback: the exact display name of the human/agent. Use this ONLY if you don't have their UUID.",
                },
                "period_start": {
                    "type": "string",
                    "minLength": 1,
                    "description": "ISO date string for the start of the OKR period (e.g. '2026-04-01').",
                },
                "period_end": {
                    "type": "string",
                    "minLength": 1,
                    "description": "ISO date string for the end of the OKR period (e.g. '2026-06-30').",
                },
            },
            "required": ["title", "owner_type", "period_start", "period_end"],
        },
        "config": {"okr_agent_only": True},
        "config_schema": {},
    },
    {
        # create_key_result — OKR Agent creates a measurable KR under a confirmed objective.
        "name": "create_key_result",
        "display_name": "Create Key Result",
        "description": (
            "Create a Key Result (KR) under an existing Objective. "
            "Get the objective_id first using get_okr. "
            "Use this only for a brand-new KR. If the user is revising the wording, target value, unit, "
            "or focus reference of an existing KR, use update_kr_content instead. "
            "target_value is the goal number (e.g. 50000 for 50000 followers). "
            "unit is optional but recommended for clarity (e.g. '%', 'NPS', '万元', 'followers')."
        ),
        "category": "okr",
        "icon": "🔑",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "objective_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "UUID of the parent Objective.",
                },
                "title": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The KR title (specific, measurable outcome).",
                },
                "target_value": {
                    "type": "number",
                    "description": "The target number to achieve (e.g. 50000).",
                },
                "unit": {
                    "type": "string",
                    "description": "Optional unit label (e.g. '%', 'followers', '万元', 'NPS score').",
                },
                "focus_ref": {
                    "type": "string",
                    "description": "Optional: basename of the focus file that tracks this KR (e.g. 'content_quality').",
                },
            },
            "required": ["objective_id", "title", "target_value"],
        },
        "config": {"okr_agent_only": True},
        "config_schema": {},
    },
    {
        # update_objective — available to ALL agents, but with ownership enforcement:
        # regular agents can only modify their own O; OKR Agent can modify any O.
        "name": "update_objective",
        "display_name": "Update Objective",
        "description": (
            "Modify an Objective's title, description, status, or period dates. "
            "Regular agents can only update their own Objectives — call get_my_okr first "
            "to get your objective_id. The OKR Agent can update any member's Objective. "
            "Only provide the fields you want to change. If the request is to revise an existing OKR's "
            "goal text rather than create a new one, prefer this tool over create_objective."
        ),
        "category": "okr",
        "icon": "✏️",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "objective_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "UUID of the Objective to update. Get from get_my_okr (own) or get_okr (any).",
                },
                "title": {
                    "type": "string",
                    "description": "New title for the objective.",
                },
                "description": {
                    "type": "string",
                    "description": "New description.",
                },
                "status": {
                    "type": "string",
                    "enum": ["draft", "active", "completed", "archived"],
                    "description": "New status for the objective.",
                },
                "period_start": {
                    "type": "string",
                    "description": "New period start date (YYYY-MM-DD).",
                },
                "period_end": {
                    "type": "string",
                    "description": "New period end date (YYYY-MM-DD).",
                },
            },
            "required": ["objective_id"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        # update_any_kr_progress — OKR Agent exclusive: update KR for any member.
        # Unlike update_kr_progress (self-report), this can update anyone's KR.
        # Used after collecting progress data through conversation.
        "name": "update_any_kr_progress",
        "display_name": "Update Any KR Progress",
        "description": (
            "Update the progress value of any team member's Key Result. "
            "This is the OKR Agent's exclusive version of update_kr_progress — it can update "
            "KRs belonging to any user or agent, not just the caller's own. "
            "Use this ONLY after confirming the value with the KR owner through conversation. "
            "Get kr_id from get_okr. Optionally provide a note explaining the source."
        ),
        "category": "okr",
        "icon": "📈",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "kr_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "UUID of the Key Result to update. Get from get_okr.",
                },
                "value": {
                    "type": "number",
                    "description": "New current value for this KR.",
                },
                "note": {
                    "type": "string",
                    "description": "Source or context note (e.g. 'Reported by user in weekly check-in').",
                },
                "status": {
                    "type": "string",
                    "enum": ["on_track", "at_risk", "behind", "completed"],
                    "description": "Optional: override the auto-computed status.",
                },
            },
            "required": ["kr_id", "value"],
        },
        "config": {"okr_agent_only": True},
        "config_schema": {},
    },
    {
        # generate_monthly_okr_report — OKR Agent exclusive: produce the monthly summary report.
        # Called automatically by the monthly_okr_report system cron trigger, or on-demand.
        "name": "generate_monthly_okr_report",
        "display_name": "Generate Monthly OKR Report",
        "description": (
            "Generate the monthly OKR progress summary report. Covers all Objectives and Key "
            "Results for the current period, highlights completed and at-risk items, and provides "
            "a closing action note. Saved to WorkReport (report_type='monthly') and "
            "workspace/reports/. Returns a bounded receipt and reference to the stored report."
        ),
        "category": "okr",
        "icon": "📅",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "config": {"okr_agent_only": True},
        "config_schema": {},
    },
    {
        # upsert_member_daily_report — OKR Agent exclusive: create or revise a member daily report.
        "name": "upsert_member_daily_report",
        "display_name": "Upsert Member Daily Report",
        "description": (
            "Create or update the final normalized daily report for any member in the company. "
            "Use this after discussing progress with the member and distilling their update into "
            "one concise final report. The stored content should stay within 2000 characters."
        ),
        "category": "okr",
        "icon": "📝",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "report_date": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Report date in YYYY-MM-DD format.",
                },
                "content": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Final concise daily report content. Keep it within 2000 characters.",
                },
                "member_type": {
                    "type": "string",
                    "enum": ["user", "agent"],
                    "description": "Member type. Defaults to user if omitted.",
                },
                "member_id": {
                    "type": "string",
                    "description": "UUID of the member. Preferred when available.",
                },
                "member_name": {
                    "type": "string",
                    "description": "Member display name. Use when you do not have the UUID.",
                },
                "source": {
                    "type": "string",
                    "description": "Optional source tag such as okr_agent_assisted or manual.",
                },
            },
            "required": ["report_date", "content"],
        },
        "config": {"okr_agent_only": True},
        "config_schema": {},
    },
    # --- Feishu Integration Tools ---
    # These tools require a configured Feishu channel to function.
    # They are NOT enabled by default — agents with Feishu channels should enable them.
    {
        "name": "send_feishu_message",
        "display_name": "Feishu Message",
        "description": "Hidden legacy compatibility shortcut for old Feishu tool calls. New model calls must use query_directory followed by send_channel_message(channel='feishu').",
        "category": "feishu",
        "icon": "💬",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "target_member_id": {"type": "string", "description": "Stable member ID returned by query_directory."},
                "message": {"type": "string", "description": "Message content"},
            },
            "required": ["target_member_id", "message"],
            "additionalProperties": False,
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "feishu_user_search",
        "display_name": "Feishu User Search",
        "description": "Search the visible tenant directory for contactable Feishu colleagues. Returns stable member IDs and display facts only; use target_member_id with channel tools.",
        "category": "feishu",
        "icon": "🔍",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Colleague name or other visible directory text to search for.",
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Maximum number of visible directory entries to inspect.",
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "minimum": 0,
                    "description": "Zero-based directory offset.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "bitable_create_app",
        "display_name": "Bitable Create",
        "description": "在飞书云盘中新建一个多维表格（Bitable）应用。创建后返回可直接访问的链接和 App Token，下一步可以通过 bitable_list_tables 查看初始数据表。",
        "category": "feishu",
        "icon": "📊",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "新多维表格的名称，例如「项目追踪表」"},
                "folder_token": {"type": "string", "description": "可选：父文件夹的 folder_token。不填则创建到「我的空间」根目录。"},
            },
            "required": ["name"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "bitable_list_tables",
        "display_name": "Bitable List Tables",
        "description": "列出飞书多维表格内的所有数据表 (Tables)。url 支持表格链接或 Wiki 链接。使用此工具了解请求的多维表格中有哪些表。",
        "category": "feishu",
        "icon": "📊",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "多维表格的 URL 链接。"},
            },
            "required": ["url"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "bitable_list_fields",
        "display_name": "Bitable List Fields",
        "description": "列出飞书多维表格指定数据表中的所有字段 (Fields)。url 支持表格链接或 Wiki 链接。在查询或修改数据前，必须先调用此工具了解字段名称和类型。",
        "category": "feishu",
        "icon": "⌨️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "多维表格的 URL 链接。"},
                "table_id": {"type": "string", "description": "具体的数据表 ID，如果 url 中包含 tbl 则可以不填。"},
            },
            "required": ["url"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "bitable_query_records",
        "display_name": "Bitable Query Records",
        "description": "查询飞书多维表格中的数据行。可以提供过滤条件 (filter)。",
        "category": "feishu",
        "icon": "🔍",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "多维表格的 URL 链接。"},
                "table_id": {"type": "string", "description": "具体的数据表 ID，如果 url 中包含 tbl 则可以不填。"},
                "filter_info": {
                    "type": "object",
                    "description": "可选：飞书多维表格 records/search API 的结构化 filter_info 对象。",
                    "additionalProperties": True,
                },
                "max_results": {"type": "integer", "description": "最大返回条数 (默认 100)"},
            },
            "required": ["url"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "bitable_create_record",
        "display_name": "Bitable Create Record",
        "description": "在飞书多维表格中新增一行数据。fields 参数是一个字典，key 是字段名 (需要先通过 bitable_list_fields 获取)，value 是对应的值。",
        "category": "feishu",
        "icon": "➕",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "多维表格的 URL 链接。"},
                "table_id": {"type": "string", "description": "具体的数据表 ID，如果 url 中包含 tbl 则可以不填。"},
                "fields": {
                    "type": "object",
                    "description": "要插入的字段对象，例如 {\"Name\": \"张三\", \"Age\": 30}。",
                    "additionalProperties": True,
                },
            },
            "required": ["url", "fields"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "bitable_update_record",
        "display_name": "Bitable Update Record",
        "description": "更新飞书多维表格中的指定行数据。",
        "category": "feishu",
        "icon": "✏️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "多维表格的 URL 链接。"},
                "table_id": {"type": "string", "description": "具体的数据表 ID，如果 url 中包含 tbl 则可以不填。"},
                "record_id": {"type": "string", "description": "要更新的 record_id，通过 bitable_query_records 获取。"},
                "fields": {
                    "type": "object",
                    "description": "要更新的字段对象，例如 {\"Status\": \"Done\"}。",
                    "additionalProperties": True,
                },
            },
            "required": ["url", "record_id", "fields"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "bitable_delete_record",
        "display_name": "Bitable Delete Record",
        "description": "删除飞书多维表格中的指定行数据。",
        "category": "feishu",
        "icon": "🗑️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "多维表格的 URL 链接。"},
                "table_id": {"type": "string", "description": "具体的数据表 ID，如果 url 中包含 tbl 则可以不填。"},
                "record_id": {"type": "string", "description": "要删除的 record_id，通过 bitable_query_records 获取。"},
            },
            "required": ["url", "record_id"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "feishu_doc_search",
        "display_name": "Feishu Doc Search",
        "description": "Search Feishu cloud documents by keyword using the official document search API. Useful when a wiki or knowledge base has too many files to browse manually.",
        "category": "feishu",
        "icon": "🔎",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword, e.g. '恩菲' or '客户周报'"},
                "docs_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["doc", "docx", "sheet", "bitable", "file", "folder", "mindnote", "slides"]},
                    "description": "Optional file type filter.",
                },
                "count": {"type": "integer", "description": "Number of results to return (default 10, max 50)."},
                "offset": {"type": "integer", "description": "Result offset for pagination (default 0)."},
            },
            "required": ["query"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "feishu_wiki_list",
        "display_name": "Feishu Wiki List",
        "description": "List child pages below a Feishu Wiki node. Set recursive=true to include descendants up to the handler's bounded depth.",
        "category": "feishu",
        "icon": "📚",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "node_token": {
                    "type": "string",
                    "description": "Wiki node token from a Feishu /wiki/ URL.",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Whether to list descendants recursively. Defaults to false.",
                },
            },
            "required": ["node_token"],
            "additionalProperties": False,
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "feishu_doc_read",
        "display_name": "Feishu Doc Read",
        "description": "Read the text content of a Feishu document (Docx). Provide the document token from its URL.",
        "category": "feishu",
        "icon": "📄",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "document_token": {"type": "string", "description": "Feishu document token (from document URL)"},
                "max_chars": {"type": "integer", "description": "Max characters to return (default 6000, max 20000)"},
            },
            "required": ["document_token"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "feishu_doc_create",
        "display_name": "Feishu Doc Create",
        "description": "Create a new Feishu document with a given title. Returns the new document token and URL.",
        "category": "feishu",
        "icon": "📝",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Document title"},
                "folder_token": {"type": "string", "description": "Optional: parent folder token"},
            },
            "required": ["title"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "feishu_doc_append",
        "display_name": "Feishu Doc Append",
        "description": "Append text content to an existing Feishu document as new paragraphs at the end.",
        "category": "feishu",
        "icon": "📎",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "document_token": {"type": "string", "description": "Feishu document token"},
                "content": {"type": "string", "description": "Text content to append"},
            },
            "required": ["document_token", "content"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "feishu_drive_share",
        "display_name": "Feishu Drive Share",
        "description": "Manage collaborators for any Feishu Drive file (docx, bitable, sheet, etc.). Add, remove, or list collaborators with view/edit/full_access permissions.",
        "category": "feishu",
        "icon": "🔗",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "document_token": {"type": "string", "description": "File token (from URL or previous tool output)"},
                "doc_type": {"type": "string", "enum": ["docx", "bitable", "sheet", "doc", "folder", "mindnote", "slides"], "description": "File type. Default: 'docx'"},
                "action": {"type": "string", "enum": ["add", "remove", "list"], "description": "'add' to grant, 'remove' to revoke, 'list' to view"},
                "member_open_ids": {"type": "array", "items": {"type": "string"}, "description": "Feishu open_ids directly"},
                "permission": {"type": "string", "enum": ["view", "edit", "full_access"], "description": "Permission level. Default: 'edit'"},
            },
            "required": ["document_token", "action"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "feishu_drive_delete",
        "display_name": "Feishu Drive Delete",
        "description": "Delete a file or folder from Feishu Drive. The file is moved to the recycle bin. Supports all file types: docx, bitable, sheet, folder, etc.",
        "category": "feishu",
        "icon": "🗑️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "file_token": {"type": "string", "description": "Token of the file to delete"},
                "file_type": {"type": "string", "enum": ["file", "docx", "bitable", "folder", "doc", "sheet", "mindnote", "shortcut", "slides"], "description": "Type of the file to delete"},
            },
            "required": ["file_token", "file_type"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "feishu_calendar_list",
        "display_name": "Feishu Calendar List",
        "description": "List Feishu calendar events. No email or authorization needed.",
        "category": "feishu",
        "icon": "📅",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "start_time": {"type": "string", "description": "Range start, ISO 8601. Default: now."},
                "end_time": {"type": "string", "description": "Range end, ISO 8601. Default: 7 days from now."},
                "max_results": {"type": "integer", "description": "Max events to return (default 20)"},
            },
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "feishu_calendar_create",
        "display_name": "Feishu Calendar Create",
        "description": "Create a Feishu calendar event. Supports inviting colleagues by name. No email needed.",
        "category": "feishu",
        "icon": "📅",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Event title"},
                "start_time": {"type": "string", "description": "Event start in ISO 8601 with timezone"},
                "end_time": {"type": "string", "description": "Event end in ISO 8601 with timezone"},
                "description": {"type": "string", "description": "Event description or agenda"},
                "attendee_names": {"type": "array", "items": {"type": "string"}, "description": "Names of colleagues to invite"},
                "location": {"type": "string", "description": "Event location"},
            },
            "required": ["summary", "start_time", "end_time"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "feishu_calendar_update",
        "display_name": "Feishu Calendar Update",
        "description": "Update an existing Feishu calendar event. Provide only the fields you want to change.",
        "category": "feishu",
        "icon": "📅",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID from feishu_calendar_list"},
                "summary": {"type": "string", "description": "New title"},
                "description": {"type": "string", "description": "New event description or agenda"},
                "location": {"type": "string", "description": "New event location"},
                "start_time": {"type": "string", "description": "New start time (ISO 8601)"},
                "end_time": {"type": "string", "description": "New end time (ISO 8601)"},
                "timezone": {"type": "string", "description": "IANA timezone for updated times. Default: Asia/Shanghai"},
            },
            "required": ["event_id"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "feishu_calendar_delete",
        "display_name": "Feishu Calendar Delete",
        "description": "Delete (cancel) a Feishu calendar event.",
        "category": "feishu",
        "icon": "🗑️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Event ID to delete"},
            },
            "required": ["event_id"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "feishu_approval_create",
        "display_name": "Feishu Approval Create",
        "description": "发起一个飞书审批流实例。该外部写入当前仅保留兼容合同，Durable Runtime 在确认门禁接入前不会向模型暴露。",
        "category": "feishu",
        "icon": "📝",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "approval_code": {
                    "type": "string",
                    "minLength": 1,
                    "description": "审批定义的唯一代码 (approval_code)。",
                },
                "target_member_id": {
                    "type": "string",
                    "format": "uuid",
                    "description": "由 feishu_user_search 或 query_directory 返回的稳定成员 ID。",
                },
                "form_data": {
                    "type": "string",
                    "minLength": 2,
                    "description": "表单字段数组的 JSON 字符串。该字段属于敏感参数。",
                },
            },
            "required": ["approval_code", "target_member_id", "form_data"],
            "additionalProperties": False,
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "feishu_approval_query",
        "display_name": "Feishu Approval Query",
        "description": "查询指定的飞书审批实例列表。可以支持按状态查询（PENDING, APPROVED, REJECTED, CANCELED, DELETED）。",
        "category": "feishu",
        "icon": "📋",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "approval_code": {
                    "type": "string",
                    "minLength": 1,
                    "description": "审批定义的唯一代码 (approval_code)。",
                },
                "instance_status": {
                    "type": "string",
                    "enum": ["PENDING", "APPROVED", "REJECTED", "CANCELED", "DELETED"],
                    "description": "可选的 Provider 审批实例状态。",
                },
                "page_size": {
                    "type": "integer",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100,
                    "description": "本页最多返回的审批实例数。",
                },
                "page_token": {
                    "type": "string",
                    "description": "上一页返回的 Provider page_token。",
                },
            },
            "required": ["approval_code"],
            "additionalProperties": False,
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "feishu_approval_get",
        "display_name": "Feishu Approval Get",
        "description": "获取指定飞书审批实例的详细信息与当前审批状态。",
        "category": "feishu",
        "icon": "📊",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "instance_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "审批实例的 instance_id。",
                },
                "section": {
                    "type": "string",
                    "enum": ["summary", "form", "tasks", "timeline", "comments"],
                    "default": "summary",
                    "description": "读取安全摘要，或显式选择一个有界详情区段。",
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "minimum": 0,
                    "description": "所选详情区段的零基偏移量。",
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 50,
                    "description": "所选详情区段本次最多返回的项目数。",
                },
            },
            "required": ["instance_id"],
            "additionalProperties": False,
        },
        "config": {},
        "config_schema": {},
    },
    # --- Pages: public HTML hosting ---
    {
        "name": "publish_page",
        "display_name": "Publish Page",
        "description": "Publish an HTML file from workspace as a public page. Returns a public URL that anyone can access without login. Only .html/.htm files can be published.",
        "category": "pages",
        "icon": "🌐",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path in workspace, e.g. 'workspace/output.html'"},
            },
            "required": ["path"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "list_published_pages",
        "display_name": "List Published Pages",
        "description": "List all pages published by this agent, showing their public URLs and view counts.",
        "category": "pages",
        "icon": "📋",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {},
        },
        "config": {},
        "config_schema": {},
    },
    # --- Skill Management ---
    {
        "name": "search_clawhub",
        "display_name": "Search ClawHub",
        "description": "Search the ClawHub skill registry for skills matching a query. Returns a list of available skills with name, description, and last updated date.",
        "category": "discovery",
        "icon": "🔎",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query, e.g. 'research', 'code review', 'market analysis'"},
            },
            "required": ["query"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "install_skill",
        "display_name": "Install Skill",
        "description": "Install a skill into this agent's workspace. Accepts a ClawHub slug (e.g. 'market-research') or a GitHub URL.",
        "category": "discovery",
        "icon": "📥",
        "is_default": True,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "ClawHub skill slug (e.g. 'market-research') or GitHub URL"},
            },
            "required": ["source"],
        },
        "config": {},
        "config_schema": {},
    },
]

# ── AgentBay Tools ──────────────────────────────────────────────────────────

_AGENTBAY_TOOL_DEFINITIONS = [
    {
        "name": "agentbay_browser_navigate",
        "display_name": "AgentBay: Browser Navigate",
        "description": "[ENV: Browser] Navigate to a URL in the AgentBay HEADLESS BROWSER environment. IMPORTANT: This browser runs in an ISOLATED environment — it does NOT share filesystem, processes, or downloads with the Cloud Desktop (computer_* tools) or Code Sandbox (code_execute/command_exec). Files downloaded here are NOT accessible from other environments. Tip: after navigating, use browser_observe to identify interactive elements, then use browser_type/browser_click to interact.",
        "category": "agentbay",
        "icon": "🌐",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要访问的网址"},
                "wait_for": {"type": "string", "description": "等待元素选择器（可选）"},
            },
            "required": ["url"],
        },
        "config": {},
        "config_schema": {
            "fields": [
                {
                    "key": "api_key",
                    "label": "API Key",
                    "type": "password",
                    "default": "",
                    "placeholder": "从阿里云 AgentBay 控制台获取",
                },
                {
                    "key": "os_type",
                    "label": "Cloud Computer OS",
                    "type": "select",
                    "default": "windows",
                    "options": [
                        {"value": "linux", "label": "Linux"},
                        {"value": "windows", "label": "Windows"},
                    ],
                    "description": "Operating system for AgentBay cloud desktop (computer tools only)",
                },
            ],
        },
    },
    {
        "name": "agentbay_browser_screenshot",
        "display_name": "AgentBay: Browser Screenshot",
        "description": "[ENV: Browser] Take a screenshot of the current page in the headless browser. This browser is ISOLATED from the Cloud Desktop and Code Sandbox. Use this after clicking, typing, or submitting a form to verify the result — it preserves the current page state. Never call browser_navigate just to take a screenshot.",
        "category": "agentbay",
        "icon": "📸",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {},
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_browser_save_screenshot",
        "display_name": "AgentBay: Save Browser Screenshot",
        "description": "[ENV: Browser] Save the current headless browser screenshot to workspace/screenshots/. Use only when the user explicitly asks to save, share, keep, or show a screenshot. For routine visual observation, use agentbay_browser_screenshot instead because it stays internal and does not create workspace files.",
        "category": "agentbay",
        "icon": "A",
        "is_default": False,
        "parameters_schema": {"type": "object", "properties": {}},
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_browser_click",
        "display_name": "AgentBay: Browser Click",
        "description": "[ENV: Browser] Click an element in the headless browser (ISOLATED from Desktop and Code Sandbox). selector can be a CSS selector (e.g. #btn) or natural language description (e.g. 'the Send button').",
        "category": "agentbay",
        "icon": "🖱️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector (e.g. #button) or natural language description of the element (e.g. 'the blue Submit button')"},
            },
            "required": ["selector"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_browser_type",
        "display_name": "AgentBay: Browser Type",
        "description": "[ENV: Browser] Type text into an element in the headless browser (ISOLATED from Desktop and Code Sandbox). selector can be a CSS selector or natural language description (e.g. 'phone number input').",
        "category": "agentbay",
        "icon": "⌨️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector or natural language description of the input field (e.g. 'the phone number input' or 'input[type=tel]')"},
                "text": {"type": "string", "description": "要输入的文本"},
            },
            "required": ["selector", "text"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_code_execute",
        "display_name": "AgentBay: Code Execute",
        "description": "[ENV: Code Sandbox] Execute code (Python, Bash, Node.js) in the AgentBay Code Sandbox. IMPORTANT: This sandbox is an ISOLATED environment — it does NOT share filesystem, processes, or network with the Headless Browser (browser_* tools) or Cloud Desktop (computer_* tools). Files created here are NOT accessible from other environments.",
        "category": "agentbay",
        "icon": "💻",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "language": {"type": "string", "enum": ["python", "bash", "node"], "description": "编程语言"},
                "code": {"type": "string", "description": "要执行的代码"},
                "timeout": {"type": "integer", "description": "超时时间（秒）", "default": 30},
            },
            "required": ["language", "code"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_code_write_file",
        "display_name": "AgentBay: Write Code Sandbox File",
        "description": "[ENV: Code Sandbox] Write a text file inside the AgentBay Code Sandbox.",
        "category": "agentbay",
        "icon": "📝",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "remote_path": {
                    "type": "string",
                    "description": "Absolute path inside the code sandbox, e.g. /home/wuying/main.py",
                },
                "content": {"type": "string", "description": "File content to write."},
                "mode": {
                    "type": "string",
                    "enum": ["overwrite", "append"],
                    "description": "Write mode. Default: overwrite.",
                    "default": "overwrite",
                },
            },
            "required": ["remote_path", "content"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_code_read_file",
        "display_name": "AgentBay: Read Code Sandbox File",
        "description": "[ENV: Code Sandbox] Read a text file from the AgentBay Code Sandbox.",
        "category": "agentbay",
        "icon": "📖",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "remote_path": {
                    "type": "string",
                    "description": "Absolute path inside the code sandbox, e.g. /home/wuying/main.py",
                },
            },
            "required": ["remote_path"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_code_edit_file",
        "display_name": "AgentBay: Edit Code Sandbox File",
        "description": "[ENV: Code Sandbox] Edit a text file inside the AgentBay Code Sandbox by replacing exact text.",
        "category": "agentbay",
        "icon": "✏️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "remote_path": {
                    "type": "string",
                    "description": "Absolute path inside the code sandbox, e.g. /home/wuying/main.py",
                },
                "edits": {
                    "type": "array",
                    "description": "List of exact text replacements.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "oldText": {"type": "string", "description": "Exact text to replace."},
                            "newText": {"type": "string", "description": "Replacement text."},
                        },
                        "required": ["oldText", "newText"],
                    },
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview changes without applying them. Default: false.",
                    "default": False,
                },
            },
            "required": ["remote_path", "edits"],
        },
        "config": {},
        "config_schema": {},
    },
    # ── Browser: Extract & Observe ────────────────────────────────────────
    {
        "name": "agentbay_browser_extract",
        "display_name": "AgentBay: Browser Extract",
        "description": "[ENV: Browser] Extract structured data from the current browser page using a natural language instruction. This browser is ISOLATED from the Cloud Desktop and Code Sandbox. More efficient than taking a screenshot and parsing with vision.",
        "category": "agentbay",
        "icon": "📊",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "instruction": {"type": "string", "description": "Natural language description of what data to extract, e.g. 'extract all product names and prices'"},
                "selector": {"type": "string", "description": "Optional CSS selector to scope the extraction to a specific element"},
            },
            "required": ["instruction"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_browser_observe",
        "display_name": "AgentBay: Browser Observe",
        "description": "[ENV: Browser] Observe the current browser page state and return a list of interactive elements. This browser is ISOLATED from the Cloud Desktop and Code Sandbox. Helps the agent understand what can be clicked/interacted with on the page.",
        "category": "agentbay",
        "icon": "👁️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "instruction": {"type": "string", "description": "Natural language description of what to observe, e.g. 'find the login button' or 'list all navigation links'"},
                "selector": {"type": "string", "description": "Optional CSS selector to scope observation"},
            },
            "required": ["instruction"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_browser_login",
        "display_name": "AgentBay: Browser Login",
        "description": "[ENV: Browser] Use AgentBay's AI-driven login skill to automate complex login flows (CAPTCHAs, OTP, multi-step auth) in the headless browser. This browser is ISOLATED from the Cloud Desktop and Code Sandbox.",
        "category": "agentbay",
        "icon": "🔐",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The login page URL to navigate to"},
                "login_config": {"type": "string", "description": "JSON string with login config"},
            },
            "required": ["url", "login_config"],
        },
        "config": {},
        "config_schema": {},
    },
    # ── Command (Shell) ───────────────────────────────────────────────────
    {
        "name": "agentbay_command_exec",
        "display_name": "AgentBay: Shell Command",
        "description": "[ENV: Code Sandbox] Execute a shell command in the AgentBay Code Sandbox. IMPORTANT: This sandbox is ISOLATED from the Headless Browser (browser_* tools) and Cloud Desktop (computer_* tools). Files and processes are NOT shared between environments. Returns stdout, stderr, and exit code.",
        "category": "agentbay",
        "icon": "🖥️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute, e.g. 'ls -la' or 'pip install pandas'"},
                "timeout_ms": {"type": "integer", "description": "Timeout in milliseconds (default 50000)", "default": 50000},
                "cwd": {"type": "string", "description": "Working directory for the command (optional)"},
            },
            "required": ["command"],
        },
        "config": {},
        "config_schema": {},
    },
    # ── Computer Use ──────────────────────────────────────────────────────
    {
        "name": "agentbay_computer_screenshot",
        "display_name": "AgentBay: Desktop Screenshot",
        "description": "[ENV: Cloud Desktop] Take a screenshot of the full Cloud Desktop (Windows/Linux). The analysis image includes a coordinate grid and the result includes the pixel coordinate system for mouse tools. For tiny controls such as close buttons, menus, checkboxes, or small icons, call this again with focus_x/focus_y/focus_width/focus_height around the target area before clicking; the focused crop is enlarged for vision and its grid labels remain absolute desktop coordinates. IMPORTANT: This desktop is an ISOLATED environment — it does NOT share filesystem, processes, or browser sessions with the Headless Browser (browser_* tools) or Code Sandbox (code_execute/command_exec). To browse the web on this desktop, first use agentbay_computer_get_installed_apps, then start a browser with the returned start_cmd. Essential for understanding the current desktop state before performing GUI operations.",
        "category": "agentbay",
        "icon": "📸",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "focus_x": {"type": "integer", "description": "Optional absolute desktop X coordinate for the top-left of a focused precision crop"},
                "focus_y": {"type": "integer", "description": "Optional absolute desktop Y coordinate for the top-left of a focused precision crop"},
                "focus_width": {"type": "integer", "description": "Optional width of the focused precision crop in desktop pixels"},
                "focus_height": {"type": "integer", "description": "Optional height of the focused precision crop in desktop pixels"},
            },
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_save_screenshot",
        "display_name": "AgentBay: Save Desktop Screenshot",
        "description": "[ENV: Cloud Desktop] Save the current Cloud Desktop screenshot to workspace/screenshots/. Use only when the user explicitly asks to save, share, keep, or show a screenshot. For routine visual observation, use agentbay_computer_screenshot instead because it stays internal and does not create workspace files.",
        "category": "agentbay",
        "icon": "A",
        "is_default": False,
        "parameters_schema": {"type": "object", "properties": {}},
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_click",
        "display_name": "AgentBay: Mouse Click",
        "description": "[ENV: Cloud Desktop] Click the mouse at absolute desktop pixel coordinates on the Cloud Desktop (ISOLATED from Browser and Code Sandbox). Always inspect the desktop first with agentbay_computer_screenshot. Before clicking dialog buttons, text buttons, tabs, menus, checkboxes, close buttons, small controls, or any target whose center is not unambiguous from the full screenshot, call agentbay_computer_precision_screenshot around the target area and use the absolute coordinate labels in that enlarged crop. Do not repeatedly guess from the full screenshot after a miss. For login prompts, software popups, cancel/no-thanks/not-now/skip/no-login flows, prefer agentbay_computer_dismiss_dialog before coordinate clicking. Click the visual center of the target. Coordinates are from the full desktop top-left corner (0, 0), not from the right-side preview panel. For in-app popups, embedded panels, marketplace/store windows, browser/app tabs, document tabs, and software-internal close buttons, use the app UI with click, Escape, or shortcuts such as Ctrl+W; do not escalate to root-window close tools. Use agentbay_computer_list_windows/close_window only when the user explicitly wants to close or quit an entire OS-level window/application.",
        "category": "agentbay",
        "icon": "🖱️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate to click"},
                "y": {"type": "integer", "description": "Y coordinate to click"},
                "button": {"type": "string", "enum": ["left", "right", "middle", "double_left"], "description": "Mouse button (default: left)", "default": "left"},
            },
            "required": ["x", "y"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_precision_screenshot",
        "display_name": "AgentBay: Precision Screenshot",
        "description": "[ENV: Cloud Desktop] Take an enlarged focused crop of the Cloud Desktop for accurate mouse targeting. Use this before clicking dialog buttons, text buttons, tabs, menus, checkboxes, close buttons, small controls, or after any near-miss. Provide an approximate absolute desktop rectangle around the target; small rectangles are automatically expanded to include surrounding context, so prefer a region around the target instead of an ultra-tight crop. The returned vision image is enlarged and its grid labels remain absolute desktop coordinates for agentbay_computer_click. The next click should use the center coordinate read from this precision crop, not a guessed coordinate from the full screenshot.",
        "category": "agentbay",
        "icon": "A",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Absolute desktop X coordinate of the crop top-left"},
                "y": {"type": "integer", "description": "Absolute desktop Y coordinate of the crop top-left"},
                "width": {"type": "integer", "description": "Approximate crop width in desktop pixels. Small crops are automatically expanded for context."},
                "height": {"type": "integer", "description": "Approximate crop height in desktop pixels. Small crops are automatically expanded for context."},
            },
            "required": ["x", "y", "width", "height"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_input_text",
        "display_name": "AgentBay: Keyboard Input",
        "description": "[ENV: Cloud Desktop] Type text at the current cursor position on the Cloud Desktop (ISOLATED from Browser and Code Sandbox). Click on the target input field first.",
        "category": "agentbay",
        "icon": "⌨️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"},
            },
            "required": ["text"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_press_keys",
        "display_name": "AgentBay: Keyboard Shortcut",
        "description": "[ENV: Cloud Desktop] Press keyboard keys or shortcuts on the Cloud Desktop (ISOLATED from Browser and Code Sandbox). For example ['ctrl', 'c'] for copy, ['alt', 'tab'] for window switch, ['enter'] to confirm.",
        "category": "agentbay",
        "icon": "⌨️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "keys": {"type": "array", "items": {"type": "string"}, "description": "List of keys to press simultaneously, e.g. ['ctrl', 'c']"},
                "hold": {"type": "boolean", "description": "If true, hold keys down", "default": False},
            },
            "required": ["keys"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_scroll",
        "display_name": "AgentBay: Scroll",
        "description": "[ENV: Cloud Desktop] Scroll the screen at a specific position on the Cloud Desktop (ISOLATED from Browser and Code Sandbox).",
        "category": "agentbay",
        "icon": "🔃",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate of scroll position"},
                "y": {"type": "integer", "description": "Y coordinate of scroll position"},
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"], "description": "Scroll direction (default: down)", "default": "down"},
                "amount": {"type": "integer", "description": "Scroll amount in steps (default: 1)", "default": 1},
            },
            "required": ["x", "y"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_move_mouse",
        "display_name": "AgentBay: Mouse Move",
        "description": "[ENV: Cloud Desktop] Move the mouse to coordinates on the Cloud Desktop without clicking. Useful for triggering hover effects, tooltips, or dropdown menus.",
        "category": "agentbay",
        "icon": "🖱️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Target X coordinate"},
                "y": {"type": "integer", "description": "Target Y coordinate"},
            },
            "required": ["x", "y"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_drag_mouse",
        "display_name": "AgentBay: Mouse Drag",
        "description": "[ENV: Cloud Desktop] Drag the mouse from one position to another on the Cloud Desktop. Useful for selecting text, moving files, resizing windows.",
        "category": "agentbay",
        "icon": "🖱️",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "from_x": {"type": "integer", "description": "Start X coordinate"},
                "from_y": {"type": "integer", "description": "Start Y coordinate"},
                "to_x": {"type": "integer", "description": "End X coordinate"},
                "to_y": {"type": "integer", "description": "End Y coordinate"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "description": "Mouse button (default: left)", "default": "left"},
            },
            "required": ["from_x", "from_y", "to_x", "to_y"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_get_screen_size",
        "display_name": "AgentBay: Get Screen Size",
        "description": "[ENV: Cloud Desktop] Get the screen resolution of the Cloud Desktop. Useful for calculating click coordinates.",
        "category": "agentbay",
        "icon": "📐",
        "is_default": False,
        "parameters_schema": {"type": "object", "properties": {}},
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_start_app",
        "display_name": "AgentBay: Start Application",
        "description": "[ENV: Cloud Desktop] Start an application on the Cloud Desktop by its launch command. Prefer calling agentbay_computer_get_installed_apps first and pass the returned start_cmd exactly; do not guess commands such as chrome, microsoft-edge, or wps. If a direct command fails, this tool will try to match installed apps by name/start_cmd and retry with the real start_cmd. The desktop is ISOLATED from the Headless Browser and Code Sandbox environments.",
        "category": "agentbay",
        "icon": "🚀",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Application launch command, e.g. 'firefox' or 'libreoffice --calc'"},
                "work_dir": {"type": "string", "description": "Working directory for the application (optional)"},
            },
            "required": ["cmd"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_get_installed_apps",
        "display_name": "AgentBay: Get Installed Apps",
        "description": "[ENV: Cloud Desktop] List installed applications and their real launch commands. Use this before agentbay_computer_start_app, then pass the returned start_cmd exactly instead of guessing app names.",
        "category": "agentbay",
        "icon": "A",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "start_menu": {"type": "boolean", "description": "Include Start Menu applications (default: true)", "default": True},
                "desktop": {"type": "boolean", "description": "Include Desktop shortcuts (default: true)", "default": True},
                "ignore_system_apps": {"type": "boolean", "description": "Hide system applications (default: true)", "default": True},
            },
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_get_cursor_position",
        "display_name": "AgentBay: Get Cursor Position",
        "description": "[ENV: Cloud Desktop] Get the current mouse cursor position on the Cloud Desktop.",
        "category": "agentbay",
        "icon": "📍",
        "is_default": False,
        "parameters_schema": {"type": "object", "properties": {}},
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_get_active_window",
        "display_name": "AgentBay: Get Active Window",
        "description": "[ENV: Cloud Desktop] Get information about the currently focused window on the Cloud Desktop, including window ID, title, and position.",
        "category": "agentbay",
        "icon": "🪟",
        "is_default": False,
        "parameters_schema": {"type": "object", "properties": {}},
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_activate_window",
        "display_name": "AgentBay: Activate Window",
        "description": "[ENV: Cloud Desktop] Bring a specific window to the foreground on the Cloud Desktop by its window ID. Use agentbay_computer_list_windows or get_active_window to find window IDs.",
        "category": "agentbay",
        "icon": "🪟",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "window_id": {"type": "integer", "description": "Window ID to activate"},
            },
            "required": ["window_id"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_list_windows",
        "display_name": "AgentBay: List Windows",
        "description": "[ENV: Cloud Desktop] List OS-level root desktop windows with window_id, title, process, and geometry. These IDs are for whole application windows only. Use this for activation, or before closing only when the user explicitly wants to close/quit an entire desktop window or app. Do NOT use root window IDs for in-app popups, modals, embedded marketplace/store panels, browser/app tabs, document tabs, or software-internal dialogs; close those with the app UI, Escape, Ctrl+W, or agentbay_computer_dismiss_dialog.",
        "category": "agentbay",
        "icon": "A",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "timeout_ms": {"type": "integer", "description": "Timeout in milliseconds (default: 3000)", "default": 3000},
            },
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_close_window",
        "display_name": "AgentBay: Close Window",
        "description": "[ENV: Cloud Desktop] HIGH-RISK: close an entire OS-level root desktop window by explicit window_id returned by agentbay_computer_list_windows. This can quit the whole application and lose context. Use only when the user explicitly asks to close/quit a whole desktop window or app. Never use this for in-app popups, modals, embedded marketplace/store panels, browser/app tabs, document tabs, login prompts, or software-internal dialogs; use app UI clicks, Escape, Ctrl+W, or agentbay_computer_dismiss_dialog instead.",
        "category": "agentbay",
        "icon": "A",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "window_id": {"type": "integer", "description": "Window ID returned by agentbay_computer_list_windows or get_active_window"},
                "title": {"type": "string", "description": "Optional title text for candidate lookup only when window_id is unknown; title-only calls will not close anything"},
            },
            "required": ["window_id"],
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_dismiss_dialog",
        "display_name": "AgentBay: Dismiss Dialog",
        "description": "[ENV: Cloud Desktop] Safely dismiss the active in-app popup/dialog by sending Escape only. It never closes root desktop windows or applications. Prefer this over coordinate clicking for modals, login prompts, no-login/not-now/skip/cancel prompts, and software-internal dialogs. For in-app tabs, embedded panels, marketplace/store windows, or document tabs, prefer app UI controls or shortcuts such as Ctrl+W. Use agentbay_computer_close_window only when the user explicitly wants to close/quit an entire OS-level window/app.",
        "category": "agentbay",
        "icon": "A",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Optional human-readable popup/dialog title hint for logging only; this tool will still only send Escape"},
            },
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_computer_list_visible_apps",
        "display_name": "AgentBay: List Running Apps",
        "description": "[ENV: Cloud Desktop] List all currently visible/running applications on the Cloud Desktop with their process info and window IDs.",
        "category": "agentbay",
        "icon": "📋",
        "is_default": False,
        "parameters_schema": {"type": "object", "properties": {}},
        "config": {},
        "config_schema": {},
    },
    {
        "name": "agentbay_file_transfer",
        "display_name": "AgentBay: File Transfer",
        "description": (
            "Transfer a file between any two endpoints: the agent workspace, "
            "the AgentBay browser environment, the cloud desktop, or the code sandbox. "
            "Workspace -> env: upload a workspace file into a cloud environment. "
            "Env -> workspace: download a file from a cloud environment into the workspace. "
            "Env -> env: transfer between environments transparently (no workspace involvement)."
        ),
        "category": "agentbay",
        "icon": "🔄",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "from_type": {
                    "type": "string",
                    "enum": ["workspace", "browser", "computer", "code"],
                    "description": "Source endpoint: 'workspace' for agent workspace, or the AgentBay environment name.",
                },
                "from_path": {
                    "type": "string",
                    "description": "Source path. Relative if workspace (e.g. 'workspace/data.csv'), absolute if env (e.g. '/root/data.csv').",
                },
                "to_type": {
                    "type": "string",
                    "enum": ["workspace", "browser", "computer", "code"],
                    "description": "Destination endpoint: 'workspace' for agent workspace, or the AgentBay environment name.",
                },
                "to_path": {
                    "type": "string",
                    "description": "Destination path. Relative if workspace (e.g. 'workspace/output.csv'), absolute if env (e.g. '/root/output.csv').",
                },
            },
            "required": ["from_type", "from_path", "to_type", "to_path"],
        },
        "config": {},
        "config_schema": {},
    },
]

_BUILTIN_TOOL_SOURCE = [
    *_BUILTIN_TOOL_SOURCE,
    # ── AgentBay Tools ──  
    *_AGENTBAY_TOOL_DEFINITIONS,
]

_DEPLOY_BUILTIN_TOOL_DEFINITIONS = [
    {
        "name": "vercel_deploy",
        "display_name": "Deploy to Vercel",
        "description": "Deploy to Vercel by uploading a workspace directory or by deploying an existing GitHub repository and ref. Returns the accepted deployment receipt.",
        "category": "deploy",
        "icon": "🚀",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Vercel project name (will be created if not exists)"
                },
                "source_dir": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Directory in workspace containing the project, e.g. 'workspace/my-app'"
                },
                "deploy_method": {
                    "type": "string",
                    "enum": ["upload", "github"],
                    "default": "upload",
                    "description": "'upload': upload a workspace directory. 'github': deploy an existing GitHub repository and ref. Default: 'upload'."
                },
                "github_repo": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Existing GitHub repository in 'owner/repo' format. Required when deploy_method='github'."
                },
                "git_ref": {
                    "type": "string",
                    "minLength": 1,
                    "default": "main",
                    "description": "Existing branch, tag, or commit ref to deploy from the GitHub repository."
                },
                "framework": {
                    "type": "string",
                    "description": "Framework preset: 'nextjs', 'vite', 'static', etc.",
                    "enum": ["nextjs", "vite", "nuxtjs", "static", "remix", "astro"]
                },
                "production": {
                    "type": "boolean",
                    "description": "If true, deploy to production. Default false (preview)."
                }
            },
            "required": ["project_name"],
            "allOf": [
                {
                    "if": {
                        "properties": {
                            "deploy_method": {"const": "upload"}
                        }
                    },
                    "then": {"required": ["source_dir"]}
                },
                {
                    "if": {
                        "properties": {
                            "deploy_method": {"const": "github"}
                        },
                        "required": ["deploy_method"]
                    },
                    "then": {"required": ["github_repo"]}
                }
            ],
            "additionalProperties": False,
        },
        "config": {"vercel_token": ""},
        "config_schema": {
            "fields": [
                {
                    "key": "vercel_token",
                    "label": "Vercel Access Token",
                    "type": "password",
                    "default": "",
                    "help_text": "Get from https://vercel.com/account/tokens"
                }
            ]
        }
    },
    {
        "name": "vercel_list_deployments",
        "display_name": "List Vercel Deployments",
        "description": "List recent deployments for a Vercel project. Shows status, URL, and creation time.",
        "category": "deploy",
        "icon": "📋",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Vercel project name"
                }
            },
            "required": ["project_name"]
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "vercel_get_deploy_logs",
        "display_name": "Get Deploy Logs",
        "description": "Get build logs and runtime logs for a Vercel deployment. Useful for debugging failed deployments.",
        "category": "deploy",
        "icon": "📜",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "deployment_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Deployment ID or HTTPS URL"
                }
            },
            "required": ["deployment_id"]
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "vercel_set_env",
        "display_name": "Set Environment Variable",
        "description": "Set an environment variable for a Vercel project. Use for database URLs, API keys, and other secrets.",
        "category": "deploy",
        "icon": "🔐",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "minLength": 1},
                "key": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Environment variable name, e.g. DATABASE_URL",
                },
                "value": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Inline environment variable value",
                },
                "value_ref": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Private deploy-value reference returned by another tool",
                },
                "target": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "enum": ["production", "preview", "development"]},
                    "description": "Deployment targets. Default: all."
                }
            },
            "required": ["project_name", "key"],
            "oneOf": [
                {"required": ["value"]},
                {"required": ["value_ref"]},
            ],
            "additionalProperties": False,
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "vercel_manage_domain",
        "display_name": "Manage Domain",
        "description": "Check domain availability/pricing, or bind a custom domain to a Vercel project.",
        "category": "deploy",
        "icon": "🌐",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["check", "bind"],
                    "description": "'check' to check availability/price, 'bind' to add domain to project"
                },
                "domain": {"type": "string", "description": "Domain name, e.g. 'myapp.com'"},
                "project_name": {"type": "string", "description": "Required for 'bind' action"}
            },
            "required": ["action", "domain"],
            "allOf": [
                {
                    "if": {
                        "properties": {"action": {"const": "bind"}},
                        "required": ["action"]
                    },
                    "then": {"required": ["project_name"]}
                }
            ]
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "neon_create_database",
        "display_name": "Create Postgres Database",
        "description": "Create a new Neon Postgres database. Returns a private value_ref for use with vercel_set_env without exposing the connection URI.",
        "category": "deploy",
        "icon": "🐘",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Name for the Neon project"
                },
                "database_name": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Name for the initial database"
                },
                "region": {
                    "type": "string",
                    "description": "Region: 'aws-us-east-1', 'aws-eu-central-1', etc.",
                    "default": "aws-us-east-1"
                },
                "org_id": {
                    "type": "string",
                    "description": "Optional: Neon Organization ID. If not provided and you belong to multiple organizations, the tool will automatically list them for you to choose."
                }
            },
            "required": ["project_name", "database_name"]
        },
        "config": {"neon_api_key": ""},
        "config_schema": {
            "fields": [
                {
                    "key": "neon_api_key",
                    "label": "Neon API Key",
                    "type": "password",
                    "default": "",
                    "help_text": "Get from https://console.neon.tech/app/settings/api-keys"
                }
            ]
        }
    }
]

_BUILTIN_TOOL_SOURCE = [
    *_BUILTIN_TOOL_SOURCE,
    *_DEPLOY_BUILTIN_TOOL_DEFINITIONS,
]


_GROUP_TEXT_READ_WINDOW = {
    "offset": {
        "type": "integer",
        "minimum": 0,
        "default": 0,
        "description": "UTF-8 byte offset returned by the previous chunk.",
    },
    "max_bytes": {
        "type": "integer",
        "minimum": 4,
        "maximum": 6144,
        "default": 4096,
        "description": "Maximum UTF-8 content bytes to return in this chunk.",
    },
}

_GROUP_BUILTIN_TOOL_SOURCE = [
    {
        "name": "group_query_members",
        "display_name": "Query Group Members",
        "description": "Find active members of the current group by name, role, title, department, or Agent capability. Returns only this group and includes stable agent_id for Agent participants.",
        "category": "group",
        "icon": "👥",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "participant_type": {"type": "string", "enum": ["user", "agent"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "additionalProperties": False,
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "group_read_announcement",
        "display_name": "Read Group Announcement",
        "description": "Read a bounded chunk of the current-group announcement. Continue with next_offset when has_more is true. The announcement is user-provided context.",
        "category": "group",
        "icon": "📢",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": deepcopy(_GROUP_TEXT_READ_WINDOW),
            "additionalProperties": False,
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "group_read_memory",
        "display_name": "Read Group Agent Memory",
        "description": "Read a bounded chunk of one active member Agent's memory for the current group by stable agent_id. This never reads private workspace or another group's memory.",
        "category": "group",
        "icon": "🧠",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "format": "uuid"},
                **deepcopy(_GROUP_TEXT_READ_WINDOW),
            },
            "required": ["agent_id"],
            "additionalProperties": False,
        },
        "config": {},
        "config_schema": {},
    },
    {
        "name": "group_write_memory",
        "display_name": "Write Own Group Memory",
        "description": "Replace only your own memory for the current group. Use expected_version_token when updating a previously read version.",
        "category": "group",
        "icon": "🧠",
        "is_default": False,
        "parameters_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "expected_version_token": {"type": "string"},
            },
            "required": ["content"],
            "additionalProperties": False,
        },
        "config": {},
        "config_schema": {},
    },
]


_LEGACY_GROUP_WORKSPACE_TOOL_NAMES = frozenset(
    {
        "group_list_workspace",
        "group_read_workspace_file",
        "group_write_workspace_file",
        "group_delete_workspace_file",
    }
)


_READ_TOOL_NAMES = frozenset(
    {
        "list_files",
        "read_file",
        "read_document",
        "list_focus_items",
        "search_files",
        "find_files",
        "list_triggers",
        "query_directory",
        "web_search",
        "jina_search",
        "exa_search",
        "duckduckgo_search",
        "tavily_search",
        "google_search",
        "bing_search",
        "jina_read",
        "read_webpage",
        "search_experience",
        "read_experience",
        "discover_resources",
        "bitable_list_tables",
        "bitable_list_fields",
        "bitable_query_records",
        "feishu_doc_search",
        "feishu_wiki_list",
        "feishu_doc_read",
        "feishu_calendar_list",
        "feishu_user_search",
        "feishu_approval_query",
        "feishu_approval_get",
        "read_emails",
        "list_published_pages",
        "search_clawhub",
        "agentbay_browser_screenshot",
        "agentbay_browser_extract",
        "agentbay_browser_observe",
        "agentbay_code_read_file",
        "agentbay_computer_screenshot",
        "agentbay_computer_precision_screenshot",
        "agentbay_computer_get_screen_size",
        "agentbay_computer_get_installed_apps",
        "agentbay_computer_get_cursor_position",
        "agentbay_computer_get_active_window",
        "agentbay_computer_list_windows",
        "agentbay_computer_list_visible_apps",
        "get_okr",
        "get_my_okr",
        "get_okr_settings",
        "vercel_list_deployments",
        "vercel_get_deploy_logs",
        "group_query_members",
        "group_read_announcement",
        "group_read_memory",
        "group_list_workspace",
        "group_read_workspace_file",
    }
)

_LOCAL_WRITE_TOOL_NAMES = frozenset(
    {
        "upsert_focus_item",
        "complete_focus_item",
        "write_file",
        "move_file",
        "delete_file",
        "edit_file",
        "convert_csv_to_xlsx",
        "convert_html_to_pdf",
        "convert_html_to_pptx",
        "convert_markdown_to_docx",
        "convert_markdown_to_pdf",
        "set_trigger",
        "update_trigger",
        "cancel_trigger",
        "install_skill",
        "update_kr_content",
        "update_kr_progress",
        "collect_okr_progress",
        "generate_okr_report",
        "generate_monthly_okr_report",
        "create_objective",
        "create_key_result",
        "update_objective",
        "update_any_kr_progress",
        "upsert_member_daily_report",
        "group_write_memory",
        "group_write_workspace_file",
        "group_delete_workspace_file",
    }
)

_CHANNEL_TOOL_NAMES = frozenset(
    {
        "send_channel_message",
        "send_channel_file",
        "send_feishu_message",
    }
)

_CROSS_SPACE_ACTION_BY_TOOL = {
    "send_channel_message": "external_message",
    "send_platform_message": "external_message",
    "send_feishu_message": "external_message",
    "send_channel_file": "external_file",
    "send_file_to_agent": "external_file",
}

_FEISHU_TOOL_NAMES = frozenset(
    definition["name"]
    for definition in _BUILTIN_TOOL_SOURCE
    if definition.get("category") == "feishu"
)

_EMAIL_TOOL_NAMES = frozenset({"send_email", "read_emails", "reply_email"})

_SENSITIVE_PATHS: dict[str, tuple[str, ...]] = {
    "execute_code": ("env", "environment"),
    "execute_code_e2b": ("env", "environment"),
    "import_mcp_server": (
        "config.api_key",
        "config.token",
        "config.password",
        "config.authorization",
    ),
    "vercel_set_env": ("value",),
    "neon_create_database": ("password",),
    "feishu_approval_create": ("form_data",),
}

_TIMEOUT_SECONDS: dict[str, int] = {
    "execute_code": 30,
    "execute_code_e2b": 30,
    "read_webpage": 60,
    "jina_read": 60,
    "upload_image": 60,
    "generate_image_siliconflow": 120,
    "generate_image_openai": 120,
    "generate_image_google": 120,
    "generate_image_custom": 120,
}


def _policy_for_name(name: str) -> tuple[str, str, bool]:
    if name in _READ_TOOL_NAMES:
        return "read", "safe", True
    if name in _LOCAL_WRITE_TOOL_NAMES:
        return "write", "conditional", False
    return "external_write", "never", False


def _readiness(definition: Mapping[str, Any]) -> str:
    name = str(definition["name"])
    # These search/read tools have a deterministic credential-free path.
    # web_search defaults to DuckDuckGo, while Jina accepts anonymous requests
    # (the API key only raises rate limits). Runtime still validates a selected
    # credentialed web_search engine against its local configuration.
    if name in {"web_search", "jina_search", "jina_read"}:
        return "local"
    if name == "execute_code_e2b":
        return "e2b_configuration"
    if name in _FEISHU_TOOL_NAMES:
        return "feishu_channel"
    if name in _EMAIL_TOOL_NAMES:
        return "email_configuration"
    if name.startswith("vercel_"):
        # Every Vercel operation consumes the credential stored by the
        # vercel_deploy tool, even when the sibling has no config UI itself.
        return "configured_credentials"
    if name in _CHANNEL_TOOL_NAMES:
        return "configured_channel"
    if name.startswith("agentbay_"):
        return "agentbay_configuration"
    config_fields = (definition.get("config_schema") or {}).get("fields", [])
    if any(field.get("type") == "password" for field in config_fields):
        return "configured_credentials"
    return "local"


def _canonical_definition(seed: Mapping[str, Any]) -> dict[str, Any]:
    effect, retry_policy, parallel_safe = _policy_for_name(str(seed["name"]))
    return {
        **deepcopy(dict(seed)),
        "effect": effect,
        "retry_policy": retry_policy,
        "parallel_safe": parallel_safe,
        "timeout_seconds": _TIMEOUT_SECONDS.get(str(seed["name"])),
        "readiness": _readiness(seed),
        "sensitive_paths": _SENSITIVE_PATHS.get(str(seed["name"]), ()),
    }


BUILTIN_TOOL_DEFINITIONS = tuple(
    _canonical_definition(seed) for seed in _BUILTIN_TOOL_SOURCE
)
GROUP_BUILTIN_TOOL_DEFINITIONS = tuple(
    _canonical_definition(seed) for seed in _GROUP_BUILTIN_TOOL_SOURCE
)
BUILTIN_TOOL_NAMES = frozenset(
    definition["name"] for definition in BUILTIN_TOOL_DEFINITIONS
)
_BUILTIN_TOOL_BY_NAME = {
    definition["name"]: definition for definition in BUILTIN_TOOL_DEFINITIONS
}
_ALL_BUILTIN_TOOL_BY_NAME = {
    **_BUILTIN_TOOL_BY_NAME,
    **{
        definition["name"]: definition
        for definition in GROUP_BUILTIN_TOOL_DEFINITIONS
    },
}

_POLICY_ONLY_KEYS = frozenset(
    {
        "effect",
        "retry_policy",
        "parallel_safe",
        "timeout_seconds",
        "readiness",
        "sensitive_paths",
    }
)
BUILTIN_TOOL_SEEDS = tuple(
    {
        key: deepcopy(value)
        for key, value in definition.items()
        if key not in _POLICY_ONLY_KEYS
    }
    for definition in BUILTIN_TOOL_DEFINITIONS
)


def builtin_model_definition(name: str) -> dict[str, Any]:
    """Return one fresh OpenAI-compatible builtin contract."""
    definition = _ALL_BUILTIN_TOOL_BY_NAME[name]
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": definition["description"],
            "parameters": deepcopy(definition["parameters_schema"]),
        },
    }


def builtin_model_definitions() -> list[dict[str, Any]]:
    """Return all builtin model contracts in deterministic seed order."""
    return [
        builtin_model_definition(str(definition["name"]))
        for definition in BUILTIN_TOOL_DEFINITIONS
    ]


GROUP_RUNTIME_TOOL_DEFINITIONS = tuple(
    builtin_model_definition(str(definition["name"]))
    for definition in GROUP_BUILTIN_TOOL_DEFINITIONS
)


def builtin_policy(name: str) -> dict[str, Any]:
    """Return the persisted execution policy, conservatively for dynamics."""
    definition = _ALL_BUILTIN_TOOL_BY_NAME.get(name)
    if definition is None:
        if name in _LEGACY_GROUP_WORKSPACE_TOOL_NAMES:
            effect, retry_policy, parallel_safe = _policy_for_name(name)
            return {
                "effect": effect,
                "retry_policy": retry_policy,
                "parallel_safe": parallel_safe,
            }
        return {
            "effect": "external_write",
            "retry_policy": "never",
            "parallel_safe": False,
        }
    return {
        "effect": definition["effect"],
        "retry_policy": definition["retry_policy"],
        "parallel_safe": definition["parallel_safe"],
    }


def builtin_cross_space_action(name: str) -> str | None:
    """Normalize aliases that move content outside the current conversation."""
    return _CROSS_SPACE_ACTION_BY_TOOL.get(name)


def builtin_sensitive_paths(name: str) -> tuple[str, ...]:
    definition = _ALL_BUILTIN_TOOL_BY_NAME.get(name)
    if definition is None:
        return ()
    return tuple(definition["sensitive_paths"])


def builtin_readiness(name: str) -> str | None:
    """Return the canonical deterministic readiness kind for one builtin."""
    definition = _ALL_BUILTIN_TOOL_BY_NAME.get(name)
    if definition is None:
        return None
    return str(definition["readiness"])


def is_reserved_custom_tool_name(name: str) -> bool:
    """Prevent custom tools from replacing Runtime control/group contracts."""
    return name in {"finish", "wait"} or name.startswith("group_")


def validate_builtin_tool_definitions() -> None:
    """Fail startup/tests on duplicate or malformed model contracts."""
    definitions = (
        *BUILTIN_TOOL_DEFINITIONS,
        *GROUP_BUILTIN_TOOL_DEFINITIONS,
    )
    names = [definition.get("name") for definition in definitions]
    if any(not isinstance(name, str) or not name.strip() for name in names):
        raise ValueError("builtin tool names must be non-empty strings")
    if len(names) != len(set(names)):
        raise ValueError("builtin tool names must be unique")
    for definition in definitions:
        name = str(definition["name"])
        description = definition.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"builtin tool {name!r} requires a description")
        schema = definition.get("parameters_schema")
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            raise ValueError(f"builtin tool {name!r} requires an object schema")
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise ValueError(f"builtin tool {name!r} properties must be an object")
        required = schema.get("required", [])
        if (
            not isinstance(required, list)
            or any(not isinstance(item, str) for item in required)
            or not set(required).issubset(properties)
        ):
            raise ValueError(
                f"builtin tool {name!r} required fields must exist in properties"
            )
        alternatives = schema.get("anyOf", [])
        if not isinstance(alternatives, list):
            raise ValueError(f"builtin tool {name!r} anyOf must be an array")
        for alternative in alternatives:
            alternative_required = (
                alternative.get("required", [])
                if isinstance(alternative, Mapping)
                else None
            )
            if (
                not isinstance(alternative_required, list)
                or any(not isinstance(item, str) for item in alternative_required)
                or not set(alternative_required).issubset(properties)
            ):
                raise ValueError(
                    f"builtin tool {name!r} anyOf required fields must exist in properties"
                )
        for property_name, property_schema in properties.items():
            if not isinstance(property_schema, Mapping):
                raise ValueError(
                    f"builtin tool {name!r} property {property_name!r} is invalid"
                )
            enum = property_schema.get("enum")
            if enum is not None and (
                not isinstance(enum, list) or not enum or len(enum) != len(set(enum))
            ):
                raise ValueError(
                    f"builtin tool {name!r} property {property_name!r} has invalid enum"
                )


validate_builtin_tool_definitions()


__all__ = [
    "BUILTIN_TOOL_DEFINITIONS",
    "BUILTIN_TOOL_NAMES",
    "BUILTIN_TOOL_SEEDS",
    "GROUP_BUILTIN_TOOL_DEFINITIONS",
    "GROUP_RUNTIME_TOOL_DEFINITIONS",
    "builtin_model_definition",
    "builtin_model_definitions",
    "builtin_cross_space_action",
    "builtin_policy",
    "builtin_sensitive_paths",
    "is_reserved_custom_tool_name",
    "validate_builtin_tool_definitions",
]
