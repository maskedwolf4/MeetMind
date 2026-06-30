"""
Teams Bot webhook handler — receives live call notifications from Azure Bot Service.

When MeetMind's bot joins a Teams meeting, Azure Bot Service sends real-time
notifications about:
  - Call state changes: ringing → established → terminated
  - Transcript data (if real-time streaming is enabled)
  - Call record availability (after call ends)

This handler routes those notifications to the TeamsBotService which manages
the meeting lifecycle (join → attend → capture transcript → summarize).
"""

import logging

from fastapi import APIRouter, Request, Response, status

from app.core.config import settings
from app.services.teams_bot_service import teams_bot_service

logger = logging.getLogger("meetmind.teams_bot.handler")

router = APIRouter(prefix="/api", tags=["bot-framework"])


@router.post("/messages")
async def bot_messages(request: Request):
    """
    Bot Framework messaging endpoint.
    Azure Bot Service sends all messaging activity here (chat messages,
    notifications, etc.).
    """
    if not settings.azure_configured:
        return Response(
            content="Bot not configured — Azure credentials missing",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    body = await request.json()
    activity_type = body.get("type", "unknown")
    logger.info("📨 Bot message activity: type=%s", activity_type)

    # Handle different activity types
    if activity_type == "message":
        # Someone sent a message to the bot in chat
        text = body.get("text", "")
        logger.info("Chat message received: %s", text[:100])

    elif activity_type == "event":
        event_name = body.get("name", "")
        logger.info("Event received: %s", event_name)

    return Response(status_code=status.HTTP_200_OK)


@router.post("/calls")
async def bot_calls(request: Request):
    """
    Bot Framework calling endpoint.
    Receives real-time call state change notifications:
      - Call established (bot joined the meeting)
      - Call terminated (meeting ended)
      - Transcript chunks (if real-time transcript is enabled)
      - Call record available (post-call)
    """
    if not settings.azure_configured:
        return Response(
            content="Bot not configured — Azure credentials missing",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    body = await request.json()

    # Graph sends notifications as a collection
    notifications = body.get("value", [])

    if not notifications:
        # Could be a direct call state update (not subscription-based)
        await _handle_direct_call_update(body)
        return Response(status_code=status.HTTP_200_OK)

    for notification in notifications:
        change_type = notification.get("changeType", "")
        resource_url = notification.get("resourceUrl", "")
        resource_data = notification.get("resourceData", {})

        logger.info(
            "📞 Call notification: changeType=%s resource=%s",
            change_type, resource_url[:80] if resource_url else "N/A",
        )

        # --- Call state change ---
        if "/communications/calls/" in resource_url:
            call_id = _extract_id_from_url(resource_url, "calls")
            state = resource_data.get("state", "")

            if state == "established":
                # Bot has joined the meeting — it's now attending live
                logger.info("🎙️ Call %s established — bot is in the meeting", call_id)
                await teams_bot_service.on_call_established(call_id)

            elif state == "terminated" or change_type == "deleted":
                # Meeting ended — trigger transcript pull + summarization
                logger.info("📞 Call %s terminated — meeting ended", call_id)
                await teams_bot_service.on_call_ended(call_id)

        # --- Call record available ---
        elif "callRecords" in resource_url:
            call_record_id = _extract_id_from_url(resource_url, "callRecords")
            logger.info("📋 Call record available: %s", call_record_id)
            # Call records can trigger transcript retrieval for calls
            # where we didn't get the terminated notification

        # --- Transcript data (real-time, if enabled) ---
        elif "transcripts" in resource_url:
            call_id = _extract_id_from_url(resource_url, "calls")
            await teams_bot_service.on_transcript_chunk(call_id, resource_data)

    return Response(status_code=status.HTTP_200_OK)


async def _handle_direct_call_update(body: dict) -> None:
    """
    Handle a direct call state update (not subscription-based notification).
    Graph sometimes sends these as top-level objects.
    """
    state = body.get("state", "")
    call_id = body.get("id", "")

    if not call_id:
        return

    if state == "established":
        logger.info("🎙️ Direct call update: %s established", call_id)
        await teams_bot_service.on_call_established(call_id)
    elif state == "terminated":
        logger.info("📞 Direct call update: %s terminated", call_id)
        await teams_bot_service.on_call_ended(call_id)
    else:
        logger.info("📞 Direct call update: %s state=%s", call_id, state)


def _extract_id_from_url(url: str, resource_name: str) -> str:
    """Extract the resource ID from a Graph resource URL."""
    try:
        parts = url.split("/")
        for i, part in enumerate(parts):
            if part == resource_name and i + 1 < len(parts):
                # ID might have query params, strip them
                return parts[i + 1].split("?")[0]
    except Exception:
        pass
    return url.split("/")[-1].split("?")[0] if "/" in url else ""
