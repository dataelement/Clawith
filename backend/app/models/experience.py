"""Experience Library models.

The team experience library replaces the old Plaza social feed: human-curated,
AI-consumed private knowledge. An entry only becomes retrievable once a human
publishes it.

Structure is exactly as deep as the retrieval contract needs, and no deeper:
`title` + `applicability` are the *only* fields `search_experience` returns as a
candidate preview, so the agent can decide read-or-skip without paying for the
full text — they stay first-class columns. Everything else the agent only ever
sees verbatim, so the narrative is one free-form markdown `body` (editor seeds a
场景/问题/解决 template, but does not enforce it — not all internal knowledge is a
problem→solution story).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ExperienceEntry(Base):
    """A single curated experience entry.

    Status lifecycle:
        draft     — AI-generated or human-authored, not yet retrievable
        published — human-reviewed, injected into agent context directory
        retired   — marked stale; excluded from retrieval
    """

    __tablename__ = "experience_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # A draft created while editing a published/retired entry. Publishing the
    # draft copies its content back onto this stable source id, then removes the
    # draft. References and adoption stats therefore stay attached to the source.
    draft_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "experience_entries.id",
            name="fk_experience_entries_draft_of_id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    # ── P0-3: all three required to publish ──
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")  # search preview line 1
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")          # 正文 (markdown, free-form)
    applicability: Mapped[str] = mapped_column(Text, nullable=False, default="")  # 适用条件与失效信号 — search preview line 2

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)  # P1-2

    # Legacy visibility metadata retained for API/data compatibility. Published
    # human-facing Experience is tenant-wide and is canonicalized to company/null.
    visibility_scope: Mapped[str] = mapped_column(String(16), nullable=False, default="company", index=True)
    visibility_scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    # Provenance of the entry itself: normal distillation vs imported legacy Plaza data.
    # legacy_plaza entries are hard-isolated — never returned by search_experience.
    origin: Mapped[str] = mapped_column(String(20), nullable=False, default="chat", index=True)  # chat | legacy_plaza

    # ── Provenance & governance ──
    origin_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # source conversation
    origin_agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))    # agent present when distilled
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)  # initiator (chat participant)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))          # who approved publish
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # P1-3
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))        # when retired; 30d later → hard-deleted (cleared on re-publish)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
