"""LLM model pool configuration."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LLMModel(Base):
    """LLM model in the platform model pool."""

    __tablename__ = "llm_models"

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
