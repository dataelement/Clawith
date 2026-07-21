"""Experience reference model — provenance of experience reuse.

Splits the reuse signal into two kinds so the adoption metric is not inflated:
    read  — an agent opened the full entry (via read_experience). "Read != used".
    cited — an agent's output actually referenced the entry.

Hit / read rate is computed from `read` rows; adoption rate is computed from
`cited` rows only. (Per PRD v2 — the kill-switch metric hangs on adoption.)
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ExperienceReference(Base):
    """One reuse event of an experience entry by an agent."""

    __tablename__ = "experience_references"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experience_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="read", index=True)  # read | cited

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
