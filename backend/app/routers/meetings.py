"""Meeting router: CRUD, Teams join, Meet import, ingestion pipeline, summarization.

Phase 2 additions (extend only, Day 1 endpoints preserved):
  GET  /meetings              -> list meetings where current user is attendee
  GET  /meetings/{id}         -> 404 unless current user is attendee
  POST /meetings/{id}/attendees -> add user as attendee by email
  POST /meetings/{id}/process -> trigger LangGraph ingestion pipeline
"""

from datetime import datetime, timezone
from uuid import UUID
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.meeting import Meeting, MeetingAttendee
from app.schemas.meeting import (
    MeetingCreateRequest,
    MeetingResponse,
    MeetingSummaryResponse,
    TeamsJoinRequest,
    MeetImportRequest,
    ManualTranscriptRequest,
    AddAttendeeRequest,
    AddAttendeeResponse,
)
from app.services.teams_bot_service import teams_bot_service
from app.services.meet_ingestion_service import MeetIngestionService
from app.services.summary_service import summary_service

router = APIRouter(prefix="/meetings", tags=["meetings"])


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #
async def _get_meeting_for_attendee(
    meeting_id: UUID, db: AsyncSession, current_user: User
) -> Meeting:
    """Fetch a meeting, ensuring the current user is an attendee OR the creator."""
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    # Check if user is creator
    if meeting.created_by == current_user.id:
        return meeting

    # Check if user is an attendee
    attendee_check = await db.execute(
        select(MeetingAttendee).where(
            MeetingAttendee.meeting_id == meeting_id,
            MeetingAttendee.user_id == current_user.id,
        )
    )
    if attendee_check.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    return meeting


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


async def _get_meeting_attendees(meeting_id: UUID, db: AsyncSession) -> dict[str, str]:
    """Get all attendees for a meeting as {user_id_str: user_name}."""
    result = await db.execute(
        select(User)
        .join(MeetingAttendee, MeetingAttendee.user_id == User.id)
        .where(MeetingAttendee.meeting_id == meeting_id)
    )
    attendees = result.scalars().all()

    # Also include the creator
    meeting_result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = meeting_result.scalar_one_or_none()
    if meeting:
        creator_result = await db.execute(select(User).where(User.id == meeting.created_by))
        creator = creator_result.scalar_one_or_none()
        if creator:
            attendee_map = {str(creator.id): creator.name}
        else:
            attendee_map = {}
    else:
        attendee_map = {}

    for u in attendees:
        attendee_map[str(u.id)] = u.name

    return attendee_map


# ------------------------------------------------------------------ #
# CRUD (Phase 2: list by attendee, get by attendee)
# ------------------------------------------------------------------ #
@router.post("", response_model=MeetingResponse, status_code=status.HTTP_201_CREATED)
async def create_meeting(
    body: MeetingCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new draft meeting. The creator is automatically an attendee."""
    meeting_source = body.source or "manual"
    meeting = Meeting(
        title=body.title,
        meeting_datetime=body.meeting_datetime,
        created_by=current_user.id,
        source=meeting_source,
        status="draft",
    )
    db.add(meeting)
    await db.flush()

    # Auto-add creator as an attendee
    attendee = MeetingAttendee(meeting_id=meeting.id, user_id=current_user.id)
    db.add(attendee)
    await db.flush()

    # Optionally add other attendees
    if body.attendee_emails:
        for email in body.attendee_emails:
            # Skip if it is the creator
            if email.lower() == current_user.email.lower():
                continue
            # Search for user by email
            user_result = await db.execute(select(User).where(User.email == email))
            user = user_result.scalar_one_or_none()
            if user:
                # Add as attendee
                db.add(MeetingAttendee(meeting_id=meeting.id, user_id=user.id))
            else:
                import logging
                logging.getLogger("meetmind.meetings").warning(
                    "Attendee email not registered: %s — skipping", email
                )
        await db.flush()

    await db.refresh(meeting)
    return MeetingResponse.model_validate(meeting)


@router.get("", response_model=List[MeetingResponse])
async def list_meetings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List all meetings where the current user is an attendee or creator,
    ordered by meeting_datetime desc, regardless of source.
    """
    # Find meetings where user is either creator or attendee
    attendee_meeting_ids = select(MeetingAttendee.meeting_id).where(
        MeetingAttendee.user_id == current_user.id
    ).scalar_subquery()

    result = await db.execute(
        select(Meeting)
        .where(
            or_(
                Meeting.created_by == current_user.id,
                Meeting.id.in_(attendee_meeting_ids),
            )
        )
        .order_by(Meeting.meeting_datetime.desc())
    )
    meetings = result.scalars().unique().all()
    return [MeetingResponse.model_validate(m) for m in meetings]


@router.get("/{meeting_id}", response_model=MeetingResponse)
async def get_meeting(
    meeting_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a meeting by ID. Returns 404 unless current user is an attendee or creator."""
    meeting = await _get_meeting_for_attendee(meeting_id, db, current_user)
    return MeetingResponse.model_validate(meeting)


# ------------------------------------------------------------------ #
# Attendee management
# ------------------------------------------------------------------ #
@router.post(
    "/{meeting_id}/attendees",
    response_model=AddAttendeeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_attendee(
    meeting_id: UUID,
    body: AddAttendeeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add an existing user as a meeting attendee by email.
    Returns 404 if the email is not a registered user.
    Only the meeting creator can add attendees.
    """
    meeting = await _get_meeting_for_creator(meeting_id, db, current_user)

    # Find user by email
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No registered user found with email: {body.email}",
        )

    # Check if already an attendee
    existing = await db.execute(
        select(MeetingAttendee).where(
            MeetingAttendee.meeting_id == meeting_id,
            MeetingAttendee.user_id == user.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already an attendee of this meeting",
        )

    attendee = MeetingAttendee(meeting_id=meeting_id, user_id=user.id)
    db.add(attendee)
    await db.flush()

    return AddAttendeeResponse(
        meeting_id=meeting_id,
        user_id=user.id,
        user_name=user.name,
        user_email=user.email,
    )


# ------------------------------------------------------------------ #
# Teams Live Join — bot attends the meeting (Day 1 endpoint, preserved)
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

    try:
        call_data = await teams_bot_service.schedule_join(
            str(meeting.id), body.teams_join_url
        )
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
# Meet Export Import (Day 1 endpoint, preserved)
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
    """Import a Google Meet exported transcript."""
    meeting = await _get_meeting_for_creator(meeting_id, db, current_user)

    ingestion = MeetIngestionService()
    await ingestion.ingest_exported_transcript(meeting, body.transcript_text, db)

    await db.refresh(meeting)
    return MeetingResponse.model_validate(meeting)


# ------------------------------------------------------------------ #
# Manual Transcript Upload (Day 1 endpoint, preserved)
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
    """Manually upload a transcript for any meeting (from any source)."""
    meeting = await _get_meeting_for_creator(meeting_id, db, current_user)

    meeting.raw_transcript = body.transcript_text
    meeting.source = "manual"
    meeting.status = "summarizing"
    await db.flush()

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
# Process — trigger LangGraph ingestion pipeline (Phase 2 addition)
# ------------------------------------------------------------------ #
@router.post(
    "/{meeting_id}/process",
    response_model=MeetingResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def process_meeting(
    meeting_id: UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Trigger the LangGraph ingestion pipeline for a meeting.

    Preconditions:
    - Meeting must have status='processing' (or 'draft'/'ready' for reprocessing)
    - raw_transcript must be non-empty

    The pipeline:
    1. Parses transcript into speaker turns
    2. Generates global summary via Groq
    3. Matches speakers to registered attendees
    4. Extracts per-person action items, decisions, deadlines via Groq
    5. Stores everything in Cognee with per-user isolation
    6. Sets status='ready' (or 'failed')
    """
    meeting = await _get_meeting_for_attendee(meeting_id, db, current_user)

    if not meeting.raw_transcript:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No transcript available to process. Upload or import a transcript first.",
        )

    # Get all attendees
    attendees = await _get_meeting_attendees(meeting_id, db)

    # Set status to processing
    meeting.status = "processing"
    await db.flush()
    await db.refresh(meeting)

    # Run ingestion pipeline (inline, not background, for testability)
    from app.services.ingestion_graph import run_ingestion_pipeline
    await run_ingestion_pipeline(
        meeting_id=str(meeting.id),
        meeting_title=meeting.title,
        raw_transcript=meeting.raw_transcript,
        registered_attendees=attendees,
        db=db,
    )

    await db.refresh(meeting)
    return MeetingResponse.model_validate(meeting)


# ------------------------------------------------------------------ #
# Re-summarize (Day 1 endpoint, preserved)
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
    """(Re)generate the AI summary for a meeting with a transcript."""
    meeting = await _get_meeting_for_creator(meeting_id, db, current_user)

    if not meeting.raw_transcript:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No transcript available to summarize.",
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
# Get summary (Day 1 endpoint, preserved)
# ------------------------------------------------------------------ #
@router.get("/{meeting_id}/summary", response_model=MeetingSummaryResponse)
async def get_summary(
    meeting_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the summary for a meeting."""
    meeting = await _get_meeting_for_attendee(meeting_id, db, current_user)

    return MeetingSummaryResponse(
        id=meeting.id,
        title=meeting.title,
        status=meeting.status,
        summary=meeting.summary,
        transcript_length=len(meeting.raw_transcript or ""),
    )
