"""Pydantic schemas for meeting endpoints."""

from datetime import datetime
from uuid import UUID
from typing import Optional, List

from pydantic import BaseModel, Field


# ---------- Request schemas ----------

class MeetingCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    meeting_datetime: datetime


class TeamsJoinRequest(BaseModel):
    teams_join_url: str = Field(..., min_length=1)


class MeetImportRequest(BaseModel):
    transcript_text: str = Field(..., min_length=1)


class ManualTranscriptRequest(BaseModel):
    """For manually uploading / pasting a transcript to any meeting."""
    transcript_text: str = Field(..., min_length=1)


# ---------- Response schemas ----------

class MeetingResponse(BaseModel):
    id: UUID
    title: str
    meeting_datetime: datetime
    created_by: UUID
    source: str
    external_meeting_id: Optional[str] = None
    raw_transcript: Optional[str] = None
    summary: Optional[str] = None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MeetingSummaryResponse(BaseModel):
    """Lightweight response returning just the summary."""
    id: UUID
    title: str
    status: str
    summary: Optional[str] = None
    transcript_length: int = 0

    model_config = {"from_attributes": True}
