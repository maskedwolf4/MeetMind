"""Meeting and MeetingAttendee ORM models."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    String, DateTime, Text, ForeignKey, CheckConstraint, Index, text,
    PrimaryKeyConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class MeetingAttendee(Base):
    """Association table: meeting <-> user (many-to-many)."""
    __tablename__ = "meeting_attendees"

    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    __table_args__ = (
        PrimaryKeyConstraint("meeting_id", "user_id"),
        Index("ix_meeting_attendees_user_id", "user_id"),
    )


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    meeting_datetime: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    external_meeting_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    raw_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'draft'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        CheckConstraint(
            "source IN ('teams_live','teams_export','meet_export','manual')",
            name="ck_meetings_source",
        ),
        CheckConstraint(
            "status IN ('draft','awaiting_join','recording','processing','summarizing','ready','failed')",
            name="ck_meetings_status",
        ),
        Index("ix_meetings_external_meeting_id", "external_meeting_id"),
    )

    # Relationships
    creator = relationship("User", back_populates="created_meetings")
    attendees = relationship(
        "User",
        secondary="meeting_attendees",
        back_populates="attended_meetings",
    )
    chat_threads = relationship("ChatThread", back_populates="meeting")
