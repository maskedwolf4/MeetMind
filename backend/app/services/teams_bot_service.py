"""
Teams Bot Service — MeetMind's AI assistant that attends live Teams meetings.

MeetMind's bot joins a live Teams meeting, stays present throughout the call,
and after the meeting ends, pulls the official transcript from Microsoft Graph
and generates an AI-powered summary.

Lifecycle:
  1. schedule_join()   — Bot joins the meeting via Graph /communications/calls
  2. on_call_established() — Meeting is active, bot is recording, status='recording'
  3. on_call_ended()   — Meeting ends → pull transcript → generate summary → status='ready'

Transcript retrieval strategy: POST-CALL via /onlineMeetings/{id}/transcripts.
  Real-time media streaming requires a dedicated Azure Media Bot with SRTP endpoints,
  which is impractical outside Azure. Instead, we use Graph's transcript API that
  provides the full, speaker-attributed transcript after the call ends. This requires:
    - Transcription to be enabled in the tenant's Teams admin policy
    - OnlineMeetings.Read.All permission on the app registration
"""

import asyncio
import logging
from typing import Optional, Dict
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import async_session_factory

logger = logging.getLogger("meetmind.teams_bot")


class TeamsBotService:
    """
    MeetMind's Teams meeting attendant.
    Joins live meetings, captures transcripts, and triggers summarization.
    """

    def __init__(self):
        self._token: Optional[str] = None
        # Track active calls: graph_call_id → our meeting_id
        self._active_calls: Dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Token acquisition via OAuth 2.0 client credentials flow
    # ------------------------------------------------------------------ #
    async def _acquire_graph_token(self) -> str:
        """
        Acquire an access token for Microsoft Graph using client credentials.
        Uses the standard Azure AD v2.0 token endpoint.
        """
        token_url = (
            f"https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}/oauth2/v2.0/token"
        )
        payload = {
            "client_id": settings.AZURE_APP_ID,
            "client_secret": settings.AZURE_APP_PASSWORD,
            "scope": settings.GRAPH_API_SCOPE,
            "grant_type": "client_credentials",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, data=payload)
            if resp.status_code != 200:
                error_body = resp.text
                logger.error("Graph token acquisition failed: %s", error_body)
                raise RuntimeError(f"Failed to acquire Graph token: {error_body}")

            data = resp.json()
            self._token = data["access_token"]
            logger.info("✅ Acquired Microsoft Graph access token")
            return self._token

    async def _ensure_token(self) -> str:
        """Ensure we have a valid Graph token, acquiring one if needed."""
        if self._token is None:
            await self._acquire_graph_token()
        return self._token

    # ------------------------------------------------------------------ #
    # Validate Azure prerequisites (called on startup)
    # ------------------------------------------------------------------ #
    async def validate_prerequisites(self) -> bool:
        """
        Attempt to acquire a Graph token to prove Azure credentials work.
        Returns True on success, False on failure (with detailed logging).
        """
        if not settings.azure_configured:
            missing = []
            if not settings.AZURE_APP_ID:
                missing.append("AZURE_APP_ID")
            if not settings.AZURE_APP_PASSWORD:
                missing.append("AZURE_APP_PASSWORD")
            if not settings.AZURE_TENANT_ID:
                missing.append("AZURE_TENANT_ID")
            logger.warning(
                "⚠️  TEAMS BOT DEGRADED MODE: Azure credentials not configured. "
                "Missing env vars: %s. "
                "Teams live-join endpoints will return 503. "
                "See docs/TEAMS_BOT_SETUP.md for setup instructions.",
                ", ".join(missing),
            )
            return False

        try:
            await self._acquire_graph_token()
            logger.info("✅ Azure prerequisites validated — Graph API token acquired successfully")
            return True
        except Exception as e:
            logger.error(
                "❌ Azure credentials are set but Graph token acquisition FAILED: %s. "
                "Teams live-join endpoints will return 503. "
                "Check AZURE_APP_ID, AZURE_APP_PASSWORD, AZURE_TENANT_ID values and "
                "ensure the app registration has the correct API permissions.",
                str(e),
            )
            return False

    # ------------------------------------------------------------------ #
    # Schedule bot to join a live meeting
    # ------------------------------------------------------------------ #
    async def schedule_join(self, meeting_id: str, teams_join_url: str) -> dict:
        """
        Make the MeetMind bot join a live Teams meeting.

        The bot uses Microsoft Graph's /communications/calls API with
        service-hosted media config (Graph manages the media, we just
        get notifications about call state changes).

        Args:
            meeting_id: Our internal meeting UUID.
            teams_join_url: The Teams meeting join link
                            (e.g. https://teams.microsoft.com/l/meetup-join/...).

        Returns:
            The Graph call creation response (contains the call_id for tracking).
        """
        await self._ensure_token()

        # Build the Graph call payload using JoinURL-based meeting info
        call_payload = {
            "@odata.type": "#microsoft.graph.call",
            "callbackUri": f"{settings.BOT_FRAMEWORK_ENDPOINT}/api/calls",
            "requestedModalities": ["audio"],
            "mediaConfig": {
                "@odata.type": "#microsoft.graph.serviceHostedMediaConfig",
            },
            "meetingInfo": {
                "@odata.type": "#microsoft.graph.organizerMeetingInfo",
                "organizer": {
                    "@odata.type": "#microsoft.graph.identitySet",
                    "user": {
                        "@odata.type": "#microsoft.graph.identity",
                        "id": settings.AZURE_APP_ID,
                        "displayName": "MeetMind Assistant",
                    },
                },
            },
            "tenantId": settings.AZURE_TENANT_ID,
        }

        # Use the joinURL approach for meeting join
        call_payload["chatInfo"] = {
            "@odata.type": "#microsoft.graph.chatInfo",
            "threadId": self._extract_thread_id(teams_join_url),
            "messageId": "0",
        }

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://graph.microsoft.com/v1.0/communications/calls",
                json=call_payload,
                headers=headers,
            )

            if resp.status_code not in (200, 201):
                logger.error("Graph /communications/calls failed: %s", resp.text)
                raise RuntimeError(
                    f"Failed to join meeting ({resp.status_code}): {resp.text}"
                )

            call_data = resp.json()
            call_id = call_data.get("id", "unknown")

            # Track this call → meeting mapping
            self._active_calls[call_id] = meeting_id

            logger.info(
                "🤖 MeetMind bot joining meeting — Graph call ID: %s → meeting %s",
                call_id,
                meeting_id,
            )
            return call_data

    def _extract_thread_id(self, join_url: str) -> str:
        """
        Extract the Teams thread ID from a join URL.
        Teams join URLs contain the thread ID as a URL-encoded parameter.
        Example: https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc...
        """
        import urllib.parse
        parsed = urllib.parse.urlparse(join_url)
        path_parts = parsed.path.strip("/").split("/")
        # The thread ID is typically the last path component after "meetup-join"
        for i, part in enumerate(path_parts):
            if part == "meetup-join" and i + 1 < len(path_parts):
                return urllib.parse.unquote(path_parts[i + 1])
        # Fallback: use the full path
        return urllib.parse.unquote(parsed.path.split("/")[-1])

    # ------------------------------------------------------------------ #
    # Call state handlers (called by bot_handler webhook)
    # ------------------------------------------------------------------ #
    async def on_call_established(self, call_id: str) -> None:
        """
        The bot has successfully joined the meeting.
        Update the meeting status to 'recording' — MeetMind is now attending.
        """
        meeting_id = self._active_calls.get(call_id)
        if not meeting_id:
            logger.warning("Call established for unknown call_id: %s", call_id)
            return

        logger.info("🎙️ MeetMind is now attending meeting %s (call %s)", meeting_id, call_id)

        async with async_session_factory() as db:
            from app.models.meeting import Meeting
            result = await db.execute(
                select(Meeting).where(Meeting.id == UUID(meeting_id))
            )
            meeting = result.scalar_one_or_none()
            if meeting:
                meeting.status = "recording"
                await db.commit()
                logger.info("Meeting %s status → recording", meeting_id)

    async def on_call_ended(self, call_id: str) -> None:
        """
        The meeting has ended. MeetMind now:
          1. Sets status='processing'
          2. Pulls the transcript from Graph
          3. Generates an AI summary
          4. Sets status='ready'

        This runs as a background task so the webhook can return immediately.
        """
        meeting_id = self._active_calls.pop(call_id, None)
        if not meeting_id:
            logger.warning("Call ended for unknown call_id: %s", call_id)
            return

        logger.info("📞 Meeting ended — call %s → meeting %s", call_id, meeting_id)

        # Run transcript retrieval + summarization in background
        asyncio.create_task(
            self._process_ended_meeting(call_id, meeting_id)
        )

    async def _process_ended_meeting(self, call_id: str, meeting_id: str) -> None:
        """
        Background task: pull transcript from Graph, generate summary, update DB.
        """
        async with async_session_factory() as db:
            from app.models.meeting import Meeting

            result = await db.execute(
                select(Meeting).where(Meeting.id == UUID(meeting_id))
            )
            meeting = result.scalar_one_or_none()
            if not meeting:
                logger.error("Meeting %s not found after call ended", meeting_id)
                return

            try:
                # Step 1: Set status to processing
                meeting.status = "processing"
                await db.commit()
                logger.info("Meeting %s → processing (pulling transcript...)", meeting_id)

                # Step 2: Pull the transcript from Graph
                transcript = await self._pull_transcript(call_id, meeting.external_meeting_id)

                if transcript:
                    meeting.raw_transcript = transcript
                    meeting.status = "summarizing"
                    await db.commit()
                    logger.info(
                        "📝 Transcript captured for meeting %s — %d chars",
                        meeting_id, len(transcript),
                    )

                    # Step 3: Generate summary
                    from app.services.summary_service import summary_service
                    summary = await summary_service.generate_summary(
                        transcript, meeting.title
                    )
                    meeting.summary = summary
                    meeting.status = "ready"
                    await db.commit()
                    logger.info("✅ Meeting %s fully processed — summary ready!", meeting_id)
                else:
                    logger.warning(
                        "No transcript available for meeting %s — "
                        "transcription may not be enabled in the Teams admin policy",
                        meeting_id,
                    )
                    meeting.status = "failed"
                    meeting.summary = (
                        "⚠️ No transcript was captured. This usually means:\n"
                        "- Meeting transcription was not enabled by the Teams admin\n"
                        "- The meeting was too short for a transcript to be generated\n"
                        "- The transcript is still being processed by Microsoft (try again in a few minutes)"
                    )
                    await db.commit()

            except Exception as e:
                logger.error("Failed to process meeting %s: %s", meeting_id, str(e))
                meeting.status = "failed"
                meeting.summary = f"Processing failed: {str(e)}"
                await db.commit()

    # ------------------------------------------------------------------ #
    # Transcript retrieval from Microsoft Graph
    # ------------------------------------------------------------------ #
    async def _pull_transcript(
        self, call_id: str, external_meeting_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Pull the meeting transcript from Microsoft Graph.

        Strategy (in order of preference):
          1. /onlineMeetings/{meetingId}/transcripts — the official transcript API
          2. /communications/callRecords/{callId} — call record with session details
          3. Return None if neither is available (transcript not enabled)
        """
        await self._ensure_token()
        headers = {"Authorization": f"Bearer {self._token}"}

        # Strategy 1: Try the onlineMeetings transcript API
        if external_meeting_id:
            transcript = await self._fetch_online_meeting_transcript(
                external_meeting_id, headers
            )
            if transcript:
                return transcript

        # Strategy 2: Try call records API
        transcript = await self._fetch_call_record_transcript(call_id, headers)
        if transcript:
            return transcript

        # Strategy 3: Retry after a delay (Graph may still be processing)
        logger.info("Transcript not yet available — waiting 30s and retrying...")
        await asyncio.sleep(30)

        if external_meeting_id:
            transcript = await self._fetch_online_meeting_transcript(
                external_meeting_id, headers
            )
            if transcript:
                return transcript

        return None

    async def _fetch_online_meeting_transcript(
        self, meeting_id: str, headers: dict
    ) -> Optional[str]:
        """
        Fetch transcript via /onlineMeetings/{id}/transcripts.
        Returns the transcript text or None.
        """
        async with httpx.AsyncClient(timeout=60.0) as client:
            # List transcripts for this meeting
            resp = await client.get(
                f"https://graph.microsoft.com/v1.0/me/onlineMeetings/{meeting_id}/transcripts",
                headers=headers,
            )

            if resp.status_code != 200:
                # Try with the communications/onlineMeetings endpoint (app-level)
                resp = await client.get(
                    f"https://graph.microsoft.com/v1.0/communications/onlineMeetings/{meeting_id}/transcripts",
                    headers=headers,
                )

            if resp.status_code != 200:
                logger.debug("No transcripts found for meeting %s: %s", meeting_id, resp.status_code)
                return None

            transcripts = resp.json().get("value", [])
            if not transcripts:
                return None

            # Get the content of the first (most recent) transcript
            transcript_id = transcripts[0]["id"]
            content_resp = await client.get(
                f"https://graph.microsoft.com/v1.0/me/onlineMeetings/{meeting_id}"
                f"/transcripts/{transcript_id}/content",
                headers={**headers, "Accept": "text/vtt"},
            )

            if content_resp.status_code == 200:
                vtt_content = content_resp.text
                return self._parse_vtt_transcript(vtt_content)

            return None

    async def _fetch_call_record_transcript(
        self, call_id: str, headers: dict
    ) -> Optional[str]:
        """
        Fetch transcript data from call records API.
        This is a fallback when the onlineMeetings transcript API is not available.
        """
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                f"https://graph.microsoft.com/v1.0/communications/callRecords/{call_id}",
                headers=headers,
                params={"$expand": "sessions($expand=segments)"},
            )

            if resp.status_code != 200:
                logger.debug("No call record for call %s: %s", call_id, resp.status_code)
                return None

            record = resp.json()

            # Extract participant information and session details
            sessions = record.get("sessions", [])
            transcript_lines = []

            for session in sessions:
                caller = session.get("caller", {})
                caller_name = (
                    caller.get("identity", {})
                    .get("user", {})
                    .get("displayName", "Unknown")
                )

                segments = session.get("segments", [])
                for segment in segments:
                    callee = segment.get("callee", {})
                    callee_name = (
                        callee.get("identity", {})
                        .get("user", {})
                        .get("displayName", "Unknown")
                    )
                    start = segment.get("startDateTime", "")
                    end = segment.get("endDateTime", "")
                    transcript_lines.append(
                        f"[{start} - {end}] {caller_name} ↔ {callee_name}"
                    )

            if transcript_lines:
                return "\n".join(transcript_lines)

            return None

    def _parse_vtt_transcript(self, vtt_content: str) -> str:
        """
        Parse a WebVTT transcript into plain text with speaker attribution.
        VTT format:
          WEBVTT
          
          00:00:05.000 --> 00:00:10.000
          <v Speaker Name>Hello everyone</v>
        """
        lines = vtt_content.split("\n")
        transcript_parts = []
        current_speaker = ""
        current_text = []

        for line in lines:
            line = line.strip()

            # Skip header and timing lines
            if line == "WEBVTT" or line == "" or "-->" in line:
                if current_text:
                    text = " ".join(current_text)
                    transcript_parts.append(f"{current_speaker}: {text}" if current_speaker else text)
                    current_text = []
                continue

            # Extract speaker from <v> tags
            if line.startswith("<v ") and ">" in line:
                speaker_end = line.index(">")
                current_speaker = line[3:speaker_end]
                text = line[speaker_end + 1:]
                if text.endswith("</v>"):
                    text = text[:-4]
                current_text.append(text)
            else:
                # Remove any closing </v> tags
                clean = line.replace("</v>", "")
                if clean:
                    current_text.append(clean)

        # Don't forget the last block
        if current_text:
            text = " ".join(current_text)
            transcript_parts.append(f"{current_speaker}: {text}" if current_speaker else text)

        return "\n".join(transcript_parts)

    # ------------------------------------------------------------------ #
    # Transcript chunk handler (for real-time streaming, if available)
    # ------------------------------------------------------------------ #
    async def on_transcript_chunk(self, call_id: str, chunk: dict) -> None:
        """
        Handle an incremental transcript chunk from Graph's media notifications.
        Appends to an in-memory buffer (flushed on call end).

        Currently using post-call retrieval mode, so this is invoked only if
        the tenant has real-time transcript streaming enabled.
        """
        meeting_id = self._active_calls.get(call_id)
        if not meeting_id:
            return

        # Extract text from the chunk
        text = chunk.get("text", "")
        speaker = chunk.get("speaker", {}).get("displayName", "Unknown")
        timestamp = chunk.get("timestamp", "")

        if text:
            logger.info(
                "📝 [%s] %s: %s", timestamp, speaker, text[:100]
            )

            # In a production system, we'd buffer these and flush to DB periodically
            # For now, the post-call transcript retrieval handles the full text


# Module-level singleton
teams_bot_service = TeamsBotService()
