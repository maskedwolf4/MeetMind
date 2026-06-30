"""Meeting router: create, get, Teams join, Meet import, summarize, manual transcript."""

from datetime import datetime, timezone
from uuid import UUID
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.meeting import Meeting
from app.schemas.meeting import (
    MeetingCreateRequest,
    MeetingResponse,
    MeetingSummaryResponse,
    TeamsJoinRequest,
    MeetImportRequest,
    ManualTranscriptRequest,
)
from app.services.teams_bot_service import teams_bot_service
from app.services.meet_ingestion_service import MeetIngestionService
from app.services.summary_service import summary_service

router = APIRouter(prefix="/meetings", tags=["meetings"])


# ------------------------------------------------------------------ #
# Helper
# ------------------------------------------------------------------ #
async def _get_meeting_for_creator(
    meeting_id: UUID, db: AsyncSession, current_user: User
) -> Meeting:
    """Fetch a meeting, ensuring it exists and the current user is the creator."""
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")
    if meeting.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return meeting


# ------------------------------------------------------------------ #
# CRUD
# ------------------------------------------------------------------ #
@router.post("", response_model=MeetingResponse, status_code=status.HTTP_201_CREATED)
async def create_meeting(
    body: MeetingCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new draft meeting."""
    meeting = Meeting(
        title=body.title,
        meeting_datetime=body.meeting_datetime,
        created_by=current_user.id,
        source="manual",
        status="draft",
    )
    db.add(meeting)
    await db.flush()
    await db.refresh(meeting)
    return MeetingResponse.model_validate(meeting)


@router.get("", response_model=List[MeetingResponse])
async def list_meetings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all meetings created by the current user."""
    result = await db.execute(
        select(Meeting)
        .where(Meeting.created_by == current_user.id)
        .order_by(Meeting.created_at.desc())
    )
    meetings = result.scalars().all()
    return [MeetingResponse.model_validate(m) for m in meetings]


@router.get("/{meeting_id}", response_model=MeetingResponse)
async def get_meeting(
    meeting_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a meeting by ID. Only the creator can view it."""
    meeting = await _get_meeting_for_creator(meeting_id, db, current_user)
    return MeetingResponse.model_validate(meeting)


# ------------------------------------------------------------------ #
# Teams Live Join — bot attends the meeting
# ------------------------------------------------------------------ #
@router.post(
    "/{meeting_id}/teams/join",
    response_model=MeetingResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def teams_join(
    meeting_id: UUID,
    body: TeamsJoinRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Send MeetMind's AI assistant to attend a live Teams meeting.

    The bot joins the meeting, stays for the duration, and after the meeting
    ends it automatically captures the transcript and generates a summary.

    Requires Azure AD credentials to be configured.
    """
    if not settings.azure_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Teams live-join is unavailable: Azure AD credentials are not configured. "
                "Required env vars: AZURE_APP_ID, AZURE_APP_PASSWORD, AZURE_TENANT_ID. "
                "See docs/TEAMS_BOT_SETUP.md for setup instructions."
            ),
        )

    meeting = await _get_meeting_for_creator(meeting_id, db, current_user)

    meeting.source = "teams_live"
    meeting.status = "awaiting_join"

    # Send the bot to join the meeting
    try:
        call_data = await teams_bot_service.schedule_join(
            str(meeting.id), body.teams_join_url
        )
        # Store the Graph call ID for tracking
        meeting.external_meeting_id = call_data.get("id")
    except Exception as e:
        meeting.status = "failed"
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to join Teams meeting: {str(e)}",
        )

    await db.flush()
    await db.refresh(meeting)
    return MeetingResponse.model_validate(meeting)


# ------------------------------------------------------------------ #
# Meet Export Import — paste a transcript + auto-summarize
# ------------------------------------------------------------------ #
@router.post(
    "/{meeting_id}/meet/import",
    response_model=MeetingResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def meet_import(
    meeting_id: UUID,
    body: MeetImportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Import a Google Meet exported transcript.

    The transcript is stored and an AI summary is generated automatically.
    No external dependency required — just paste the transcript text.
    """
    meeting = await _get_meeting_for_creator(meeting_id, db, current_user)

    ingestion = MeetIngestionService()
    await ingestion.ingest_exported_transcript(meeting, body.transcript_text, db)

    await db.refresh(meeting)
    return MeetingResponse.model_validate(meeting)


# ------------------------------------------------------------------ #
# Manual Transcript Upload — paste any transcript + auto-summarize
# ------------------------------------------------------------------ #
@router.post(
    "/{meeting_id}/transcript",
    response_model=MeetingResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_transcript(
    meeting_id: UUID,
    body: ManualTranscriptRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manually upload a transcript for any meeting (from any source).

    The transcript is stored and an AI summary is generated automatically.
    Use this for transcripts from any source — Zoom, Webex, in-person recordings, etc.
    """
    meeting = await _get_meeting_for_creator(meeting_id, db, current_user)

    meeting.raw_transcript = body.transcript_text
    meeting.source = "manual"
    meeting.status = "summarizing"
    await db.flush()

    # Generate summary
    try:
        summary = await summary_service.generate_summary(
            body.transcript_text, meeting.title
        )
        meeting.summary = summary
        meeting.status = "ready"
    except Exception as e:
        meeting.summary = f"⚠️ Summary generation failed: {str(e)}. Transcript is saved."
        meeting.status = "processing"

    await db.flush()
    await db.refresh(meeting)
    return MeetingResponse.model_validate(meeting)


# ------------------------------------------------------------------ #
# Re-summarize — regenerate summary for a meeting with a transcript
# ------------------------------------------------------------------ #
@router.post(
    "/{meeting_id}/summarize",
    response_model=MeetingSummaryResponse,
)
async def summarize_meeting(
    meeting_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    (Re)generate the AI summary for a meeting that already has a transcript.

    Use this to retry a failed summarization or regenerate with updated settings.
    """
    meeting = await _get_meeting_for_creator(meeting_id, db, current_user)

    if not meeting.raw_transcript:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No transcript available to summarize. Upload a transcript first.",
        )

    meeting.status = "summarizing"
    await db.flush()

    try:
        summary = await summary_service.generate_summary(
            meeting.raw_transcript, meeting.title
        )
        meeting.summary = summary
        meeting.status = "ready"
    except Exception as e:
        meeting.summary = f"⚠️ Summary generation failed: {str(e)}"
        meeting.status = "failed"

    await db.flush()
    await db.refresh(meeting)

    return MeetingSummaryResponse(
        id=meeting.id,
        title=meeting.title,
        status=meeting.status,
        summary=meeting.summary,
        transcript_length=len(meeting.raw_transcript or ""),
    )


# ------------------------------------------------------------------ #
# Get just the summary
# ------------------------------------------------------------------ #
@router.get("/{meeting_id}/summary", response_model=MeetingSummaryResponse)
async def get_summary(
    meeting_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the summary for a meeting."""
    meeting = await _get_meeting_for_creator(meeting_id, db, current_user)

    return MeetingSummaryResponse(
        id=meeting.id,
        title=meeting.title,
        status=meeting.status,
        summary=meeting.summary,
        transcript_length=len(meeting.raw_transcript or ""),
    )
