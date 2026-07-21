"""LLM model pool configuration."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LLMModel(Base):
    """LLM model in the platform model pool."""

    __tablename__ = "llm_models"
    __table_args__ = (
        CheckConstraint(
            "context_window_tokens IS NULL OR context_window_tokens > 0",
            name="ck_llm_models_context_window_tokens_positive",
        ),
        CheckConstraint(
            "context_window_tokens_override IS NULL OR context_window_tokens_override > 0",
            name="ck_llm_models_context_window_tokens_override_positive",
        ),
        CheckConstraint(
            "max_input_tokens IS NULL OR max_input_tokens > 0",
            name="ck_llm_models_max_input_tokens_positive",
        ),
        CheckConstraint(
            "max_input_tokens_override IS NULL OR max_input_tokens_override > 0",
            name="ck_llm_models_max_input_tokens_override_positive",
        ),
        CheckConstraint(
            "capability_source IS NULL OR capability_source IN "
            "('manual', 'provider_api', 'builtin_registry', 'runtime_config')",
            name="ck_llm_models_capability_source",
        ),
        CheckConstraint(
            "tool_calling_capability_source IS NULL OR "
            "tool_calling_capability_source IN ('probe', 'builtin_registry')",
            name="ck_llm_models_tool_calling_capability_source",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # anthropic, openai, deepseek, etc.
    model: Mapped[str] = mapped_column(String(100), nullable=False)  # claude-opus-4-6, gpt-4o, etc.
    api_key_encrypted: Mapped[str] = mapped_column(String(1024), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500))
    label: Mapped[str] = mapped_column(String(200), nullable=False)  # Display name
    max_tokens_per_day: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    request_timeout: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Request timeout in seconds, default 120
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Per-model output token limit override
    context_window_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_window_tokens_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_input_tokens_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capability_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    capability_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    supports_tool_calling: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tool_calling_capability_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tool_calling_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tool_calling_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
