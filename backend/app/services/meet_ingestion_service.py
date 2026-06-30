"""
Meet Ingestion Service — handles Google Meet transcript import + auto-summarization.

When a user imports a Google Meet transcript, this service:
  1. Stores the raw transcript text on the meeting row
  2. Sets source='meet_export' and status='processing'
  3. Triggers AI-powered summary generation via the SummaryService
  4. Updates the meeting with the summary and sets status='ready'

The summary is generated inline (not as a background task) since the transcript
is already available — no waiting for external APIs.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.summary_service import summary_service

logger = logging.getLogger("meetmind.meet_ingestion")


class MeetIngestionService:
    """Handles Google Meet exported transcript ingestion + summarization."""

    async def ingest_exported_transcript(
        self,
        meeting,
        transcript_text: str,
        db: AsyncSession,
    ) -> None:
        """
        Store an exported Google Meet transcript and generate a summary.

        Args:
            meeting: The Meeting ORM object to update.
            transcript_text: The raw transcript text from the Google Meet export.
            db: The async database session.
        """
        # Step 1: Store the transcript
        meeting.source = "meet_export"
        meeting.raw_transcript = transcript_text
        meeting.status = "processing"
        await db.flush()

        logger.info(
            "Meet transcript ingested for meeting %s — %d chars stored",
            meeting.id,
            len(transcript_text),
        )

        # Step 2: Generate AI summary
        try:
            meeting.status = "summarizing"
            await db.flush()

            summary = await summary_service.generate_summary(
                transcript_text, meeting.title
            )
            meeting.summary = summary
            meeting.status = "ready"
            await db.flush()

            logger.info(
                "✅ Meeting %s summarized — %d char summary from %d char transcript",
                meeting.id,
                len(summary),
                len(transcript_text),
            )
        except Exception as e:
            logger.error(
                "Failed to summarize meeting %s: %s — transcript is saved, summary pending",
                meeting.id,
                str(e),
            )
            meeting.status = "processing"  # Keep as processing so retry is possible
            meeting.summary = f"⚠️ Summary generation failed: {str(e)}. Transcript is saved."
            await db.flush()
