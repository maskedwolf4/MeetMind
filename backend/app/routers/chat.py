"""
Chat router — streaming SSE chat with per-user isolated meeting context.

Endpoints:
  GET  /meetings/{id}/thread           -> get or create chat thread
  GET  /threads/{id}/messages           -> paginated message history
  POST /threads/{id}/messages           -> send message + stream SSE response

Isolation guarantee:
  - Thread ownership: user_id on chat_thread must match current_user.id
  - Cognee search: always passes user_id from JWT, never from request body
  - System prompt: only includes context from that user's Cognee dataset
"""

import json
import logging
from datetime import datetime, timezone
from uuid import UUID
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

import httpx

from app.core.config import settings
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.meeting import Meeting, MeetingAttendee
from app.models.chat import ChatThread, ChatMessage
from app.services import cognee_service

logger = logging.getLogger("meetmind.chat")

router = APIRouter(tags=["chat"])


# ------------------------------------------------------------------ #
# Schemas
# ------------------------------------------------------------------ #
class ThreadResponse(BaseModel):
    thread_id: str
    meeting_id: str
    user_id: str
    created_at: str
    message_count: int

class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: str

class MessagesResponse(BaseModel):
    messages: list[MessageOut]
    has_more: bool

class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


# ------------------------------------------------------------------ #
# GET /meetings/{id}/thread
# ------------------------------------------------------------------ #
@router.get("/meetings/{meeting_id}/thread")
async def get_or_create_thread(
    meeting_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get or create a chat thread for the current user and meeting.
    403 if not an attendee. 409 if meeting not ready.
    """
    # Check user is an attendee
    attendee_check = await db.execute(
        select(MeetingAttendee).where(
            MeetingAttendee.meeting_id == meeting_id,
            MeetingAttendee.user_id == current_user.id,
        )
    )
    meeting_result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = meeting_result.scalar_one_or_none()

    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Check if user is attendee or creator
    is_attendee = attendee_check.scalar_one_or_none() is not None
    is_creator = meeting.created_by == current_user.id
    if not is_attendee and not is_creator:
        raise HTTPException(status_code=403, detail="Not an attendee of this meeting")

    if meeting.status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"Meeting is not ready for chat (status: {meeting.status})",
        )

    # Get or create thread
    result = await db.execute(
        select(ChatThread).where(
            ChatThread.user_id == current_user.id,
            ChatThread.meeting_id == meeting_id,
        )
    )
    thread = result.scalar_one_or_none()

    if not thread:
        thread = ChatThread(user_id=current_user.id, meeting_id=meeting_id)
        db.add(thread)
        await db.flush()
        await db.refresh(thread)

    # Count messages
    count_result = await db.execute(
        select(func.count()).where(ChatMessage.thread_id == thread.id)
    )
    message_count = count_result.scalar() or 0

    return ThreadResponse(
        thread_id=str(thread.id),
        meeting_id=str(thread.meeting_id),
        user_id=str(thread.user_id),
        created_at=thread.created_at.isoformat(),
        message_count=message_count,
    )


# ------------------------------------------------------------------ #
# GET /threads/{id}/messages
# ------------------------------------------------------------------ #
@router.get("/threads/{thread_id}/messages")
async def get_messages(
    thread_id: UUID,
    limit: int = 50,
    before: Optional[UUID] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get messages for a thread. 403 if not the thread owner.
    Cursor-based pagination: pass `before` (message UUID) for older messages.
    """
    # Verify ownership
    result = await db.execute(select(ChatThread).where(ChatThread.id == thread_id))
    thread = result.scalar_one_or_none()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    if thread.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your thread")

    # Build query
    query = select(ChatMessage).where(ChatMessage.thread_id == thread_id)

    if before:
        # Get the cursor message's created_at
        cursor_result = await db.execute(
            select(ChatMessage.created_at).where(ChatMessage.id == before)
        )
        cursor_time = cursor_result.scalar_one_or_none()
        if cursor_time:
            query = query.where(ChatMessage.created_at < cursor_time)

    query = query.order_by(ChatMessage.created_at.asc()).limit(limit + 1)
    result = await db.execute(query)
    messages = result.scalars().all()

    has_more = len(messages) > limit
    if has_more:
        messages = messages[:limit]

    return MessagesResponse(
        messages=[
            MessageOut(
                id=str(m.id),
                role=m.role,
                content=m.content,
                created_at=m.created_at.isoformat(),
            )
            for m in messages
        ],
        has_more=has_more,
    )


# ------------------------------------------------------------------ #
# POST /threads/{id}/messages — streaming SSE
# ------------------------------------------------------------------ #
@router.post("/threads/{thread_id}/messages")
async def send_message(
    thread_id: UUID,
    body: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Send a message to a chat thread and receive a streaming SSE response.

    SSE format:
      data: {"delta": "<token>"}\n\n   (each token chunk)
      data: {"done": true}\n\n         (stream complete)
      data: {"error": "<msg>"}\n\n     (on error, stream closed)
    """
    # Verify ownership
    result = await db.execute(select(ChatThread).where(ChatThread.id == thread_id))
    thread = result.scalar_one_or_none()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    if thread.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your thread")

    # 1. Insert user message
    user_msg = ChatMessage(
        thread_id=thread_id,
        role="user",
        content=body.content,
    )
    db.add(user_msg)
    await db.flush()

    # 2. Fetch last 10 messages for context window
    history_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.thread_id == thread_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(10)
    )
    history_messages = list(reversed(history_result.scalars().all()))

    # 3. Cognee search — ALWAYS from authenticated user, NEVER from request body
    cognee_results = await cognee_service.search(
        query=body.content,
        user_id=str(current_user.id),
        meeting_id=str(thread.meeting_id),
    )

    # 4. Fetch meeting context
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.id == thread.meeting_id)
    )
    meeting = meeting_result.scalar_one_or_none()
    meeting_title = meeting.title if meeting else "Unknown Meeting"
    meeting_datetime = meeting.meeting_datetime.isoformat() if meeting else "Unknown"
    meeting_source = meeting.source if meeting else "unknown"

    # 5. Build system prompt
    context_text = "\n".join(cognee_results) if cognee_results else "No relevant context found."
    conversation_text = "\n".join(
        f"{m.role.capitalize()}: {m.content}" for m in history_messages
    )

    system_prompt = (
        f"You are MeetMind, a personal meeting assistant for {current_user.name}.\n"
        f"You are helping them with the meeting '{meeting_title}' held on "
        f"{meeting_datetime} (source: {meeting_source}).\n\n"
        f"RETRIEVED CONTEXT FROM THIS MEETING (your knowledge base):\n"
        f"{context_text}\n\n"
        f"CONVERSATION HISTORY:\n"
        f"{conversation_text}\n\n"
        f"Answer only using the provided context and the conversation so far.\n"
        f"If the answer is not in the context, say so plainly — do not guess or "
        f"fabricate. Never reference or reveal other attendees' personal action "
        f"items, tasks, or private mentions unless they appear in the shared "
        f"meeting context above."
    )

    # Need to commit the user message before streaming
    # so it's persisted even if the stream fails
    await db.commit()

    # 6. Stream response via Groq
    return StreamingResponse(
        _stream_groq_response(
            system_prompt=system_prompt,
            user_message=body.content,
            thread_id=str(thread_id),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_groq_response(
    system_prompt: str,
    user_message: str,
    thread_id: str,
):
    """
    Generator that streams SSE tokens from Groq and persists the full
    assistant reply to the database after stream completion.
    """
    if not settings.GROQ_API_KEY:
        yield f"data: {json.dumps({'error': 'GROQ_API_KEY not configured'})}\n\n"
        return

    payload = {
        "model": settings.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.3,
        "max_tokens": 2048,
        "stream": True,
    }

    headers = {
        "Authorization": f"Bearer {settings.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    full_response = []
    stream_error = False

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                "https://api.groq.com/openai/v1/chat/completions",
                json=payload,
                headers=headers,
            ) as resp:
                if resp.status_code != 200:
                    error_body = await resp.aread()
                    yield f"data: {json.dumps({'error': f'Groq API error ({resp.status_code})'})}\n\n"
                    return

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:]  # Remove "data: " prefix
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        token = delta.get("content", "")
                        if token:
                            full_response.append(token)
                            yield f"data: {json.dumps({'delta': token})}\n\n"
                    except json.JSONDecodeError:
                        continue

    except Exception as e:
        logger.error("Groq streaming error: %s", str(e))
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
        stream_error = True
        return

    # Send done event
    yield f"data: {json.dumps({'done': True})}\n\n"

    # 8. Persist the assistant reply in a background-safe way
    if not stream_error and full_response:
        assembled = "".join(full_response)
        try:
            from app.core.db import async_session_factory
            async with async_session_factory() as session:
                assistant_msg = ChatMessage(
                    thread_id=UUID(thread_id),
                    role="assistant",
                    content=assembled,
                )
                session.add(assistant_msg)
                await session.commit()
                logger.info(
                    "Persisted assistant reply (%d chars) to thread %s",
                    len(assembled), thread_id[:8],
                )
        except Exception as e:
            logger.error("Failed to persist assistant reply: %s", str(e))
