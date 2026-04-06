"""Tool and AgentTool models for dynamic tool management."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Tool(Base):
    """A tool that can be assigned to agents.

    Types:
        - builtin: Hardcoded tools (file ops, task mgmt, feishu, web search, etc.)
        - mcp: External tools connected via Model Context Protocol
    """
    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)  # "web_search", "list_files"
    display_name: Mapped[str] = mapped_column(String(200))  # "互联网搜索"
    description: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String(20), default="builtin")  # builtin | mcp
    category: Mapped[str] = mapped_column(String(50), default="general")  # file, task, communication, search, custom
    icon: Mapped[str] = mapped_column(String(10), default="🔧")

    # OpenAI function-calling parameters schema
    parameters_schema: Mapped[dict] = mapped_column(JSON, default=dict)

    # Runtime configuration (admin-editable settings)
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # actual values, e.g. {"search_engine": "duckduckgo"}
    config_schema: Mapped[dict] = mapped_column(JSON, default=dict)  # UI schema describing configurable fields

    # MCP-specific fields
    mcp_server_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mcp_server_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    mcp_tool_name: Mapped[str | None] = mapped_column(String(200), nullable=True)  # tool name on the MCP server

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # global toggle
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # auto-assigned to new agents
    source: Mapped[str] = mapped_column(String(20), default="builtin")  # "builtin" | "admin" | "agent"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AgentTool(Base):
    """Junction table: which tools are enabled for which agent."""
    __tablename__ = "agent_tools"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"))
    tool_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tools.id", ondelete="CASCADE"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # per-agent tool config overrides
    source: Mapped[str] = mapped_column(String(20), default="system")  # "system" | "user_installed"
    installed_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)  # agent that installed this tool
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
