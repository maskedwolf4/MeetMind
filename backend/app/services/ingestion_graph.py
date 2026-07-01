"""
LangGraph Ingestion Pipeline — turns raw transcripts into structured meeting intelligence.

This pipeline is source-agnostic: it processes any raw_transcript on a meetings row
regardless of whether it came from Teams live-join, Teams export, or Meet export.
The only precondition is: meetings.status='processing' AND raw_transcript is non-empty.

StateGraph node sequence:
  1. parse_transcript        — normalize speaker turns (handles Teams, Meet, generic formats)
  2. global_summary_node     — Groq → {summary, decisions[], agenda_items[]}
  3. identify_attendees_node — match speakers → registered meeting attendees (fuzzy name match)
  4. per_person_extract_node — fan out per attendee: Groq → {action_items[], mentions[], decisions_affecting_them[], deadlines[]}
  5. store_in_cognee_node    — add_global_summary for all, add_person_extract per user, run_cognify()
  6. finalize_node           — set status='ready' (or 'failed' with error_message)

Groq model: llama-3.3-70b-versatile, temperature 0.1 for extraction.
All prompts request JSON-only output; validate/parse with retry on malformed JSON.
"""

import logging
import re
from typing import Any, Optional
from uuid import UUID
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.groq_service import groq_client
from app.services import cognee_service

logger = logging.getLogger("meetmind.ingestion")


# ------------------------------------------------------------------ #
# Pipeline State
# ------------------------------------------------------------------ #
@dataclass
class IngestionState:
    """State object passed through the pipeline nodes."""
    meeting_id: str = ""
    meeting_title: str = ""
    raw_transcript: str = ""
    # Registered attendees from DB: {user_id: user_name}
    registered_attendees: dict[str, str] = field(default_factory=dict)

    # Node outputs
    parsed_turns: list[dict[str, str]] = field(default_factory=list)  # [{speaker, text, timestamp?}]
    global_summary: dict[str, Any] = field(default_factory=dict)  # {summary, decisions, agenda_items}
    speaker_to_user: dict[str, str] = field(default_factory=dict)  # {speaker_name: user_id}
    person_extracts: dict[str, dict] = field(default_factory=dict)  # {user_id: {action_items, mentions, ...}}
    cognee_stored: bool = False
    final_status: str = "ready"
    error_message: str = ""


# ------------------------------------------------------------------ #
# Node 1: parse_transcript
# ------------------------------------------------------------------ #
async def parse_transcript(state: IngestionState) -> IngestionState:
    """
    Parse raw transcript into structured speaker turns.

    Handles multiple realistic formats:
    - Teams live/export: "Speaker Name\n00:00:05 --> 00:00:10\nHello everyone"
    - Meet export: "Speaker Name (00:05)\nHello everyone"
    - VTT format: "<v Speaker>Hello</v>"
    - Generic: "Speaker: Hello everyone"
    - Plain text (no speakers): each line as a turn from "Unknown"
    """
    text = state.raw_transcript.strip()
    turns = []

    # Try to detect and parse by format
    if "<v " in text:
        turns = _parse_vtt(text)
    elif re.search(r"\d{2}:\d{2}:\d{2}\.\d+ --> \d{2}:\d{2}:\d{2}\.\d+", text):
        turns = _parse_teams_vtt(text)
    elif re.search(r"^.+\s*\(\d{1,2}:\d{2}(?::\d{2})?\)", text, re.MULTILINE):
        turns = _parse_meet_export(text)
    elif re.search(r"^[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\s*:", text, re.MULTILINE):
        turns = _parse_colon_format(text)
    else:
        # Fallback: try Groq-based normalization for unusual formats
        turns = await _parse_with_groq(text)

    if not turns:
        # Ultimate fallback: treat entire text as one turn
        turns = [{"speaker": "Unknown", "text": text}]

    state.parsed_turns = turns
    logger.info(
        "Parsed transcript: %d turns, %d unique speakers",
        len(turns),
        len(set(t["speaker"] for t in turns)),
    )
    return state


def _parse_vtt(text: str) -> list[dict]:
    """Parse WebVTT format with <v Speaker> tags."""
    turns = []
    current_speaker = "Unknown"
    current_text = []

    for line in text.split("\n"):
        line = line.strip()
        if not line or line == "WEBVTT" or "-->" in line:
            if current_text:
                turns.append({"speaker": current_speaker, "text": " ".join(current_text)})
                current_text = []
            continue

        if line.startswith("<v ") and ">" in line:
            end = line.index(">")
            current_speaker = line[3:end]
            rest = line[end + 1:].replace("</v>", "").strip()
            if rest:
                current_text.append(rest)
        else:
            clean = line.replace("</v>", "").strip()
            if clean:
                current_text.append(clean)

    if current_text:
        turns.append({"speaker": current_speaker, "text": " ".join(current_text)})

    return turns


def _parse_teams_vtt(text: str) -> list[dict]:
    """Parse Teams VTT-style export (speaker name on one line, timestamp on next, text after)."""
    turns = []
    lines = text.strip().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Skip empty lines and "WEBVTT" header
        if not line or line == "WEBVTT":
            i += 1
            continue

        # Check if next line is a timestamp
        if i + 1 < len(lines) and "-->" in lines[i + 1]:
            speaker = line
            i += 2  # skip timestamp
            text_parts = []
            while i < len(lines) and lines[i].strip() and "-->" not in lines[i]:
                text_parts.append(lines[i].strip())
                i += 1
            if text_parts:
                turns.append({"speaker": speaker, "text": " ".join(text_parts)})
        elif "-->" in line:
            # Timestamp without speaker
            i += 1
            text_parts = []
            while i < len(lines) and lines[i].strip() and "-->" not in lines[i]:
                text_parts.append(lines[i].strip())
                i += 1
            if text_parts:
                turns.append({"speaker": "Unknown", "text": " ".join(text_parts)})
        else:
            i += 1

    return turns


def _parse_meet_export(text: str) -> list[dict]:
    """Parse Google Meet export: 'Speaker Name (HH:MM:SS)' or 'Speaker Name (MM:SS)'."""
    turns = []
    # Pattern: Name (timestamp) on its own line, followed by text lines
    pattern = re.compile(r"^(.+?)\s*\((\d{1,2}:\d{2}(?::\d{2})?)\)\s*$", re.MULTILINE)

    parts = pattern.split(text)
    # parts: [before_first_match, name1, timestamp1, text_after1, name2, timestamp2, text_after2, ...]
    i = 1
    while i + 2 < len(parts):
        speaker = parts[i].strip()
        # timestamp = parts[i + 1]  # available but not needed
        body = parts[i + 2].strip()
        if body:
            turns.append({"speaker": speaker, "text": body})
        i += 3

    return turns


def _parse_colon_format(text: str) -> list[dict]:
    """Parse 'Speaker: text' format (one speaker per line or block)."""
    turns = []
    pattern = re.compile(r"^([A-Z][a-zA-Z]*(?:\s[A-Z][a-zA-Z]*)*)\s*:\s*(.+)", re.MULTILINE)

    for match in pattern.finditer(text):
        speaker = match.group(1).strip()
        body = match.group(2).strip()
        if body:
            turns.append({"speaker": speaker, "text": body})

    return turns


async def _parse_with_groq(text: str) -> list[dict]:
    """Fallback: use Groq to normalize an unusual transcript format."""
    try:
        system_prompt = (
            "You are a transcript parser. Given a meeting transcript in any format, "
            "extract a list of speaker turns. Return ONLY valid JSON:\n"
            '{"turns": [{"speaker": "Name", "text": "What they said"}]}\n'
            "If you cannot identify speakers, use 'Unknown' as the speaker name."
        )
        # Truncate for Groq context
        truncated = text[:20000]
        result = await groq_client.extract_json(system_prompt, truncated)
        turns = result.get("turns", [])
        return [{"speaker": t.get("speaker", "Unknown"), "text": t.get("text", "")} for t in turns if t.get("text")]
    except Exception as e:
        logger.warning("Groq-based transcript parsing failed: %s", str(e))
        return []


# ------------------------------------------------------------------ #
# Node 2: global_summary_node
# ------------------------------------------------------------------ #
async def global_summary_node(state: IngestionState) -> IngestionState:
    """
    Generate a global meeting summary using one Groq call.
    Output: {summary, decisions[], agenda_items[]}
    """
    # Reconstruct readable transcript from turns
    transcript_text = "\n".join(
        f"{t['speaker']}: {t['text']}" for t in state.parsed_turns
    )

    system_prompt = (
        "You are MeetMind, an AI meeting analyst. Given a meeting transcript, "
        "produce a structured JSON summary. Return ONLY valid JSON with this exact schema:\n"
        "{\n"
        '  "summary": "A comprehensive 3-5 sentence summary of the meeting",\n'
        '  "decisions": ["Decision 1", "Decision 2", ...],\n'
        '  "agenda_items": ["Topic 1 discussed", "Topic 2 discussed", ...]\n'
        "}\n"
        "If no clear decisions were made, return an empty decisions array. "
        "Extract all discussed topics for agenda_items."
    )

    user_prompt = f"Meeting: {state.meeting_title}\n\n--- TRANSCRIPT ---\n{transcript_text[:30000]}"

    try:
        result = await groq_client.extract_json(system_prompt, user_prompt)
        state.global_summary = {
            "summary": result.get("summary", "No summary generated"),
            "decisions": result.get("decisions", []),
            "agenda_items": result.get("agenda_items", []),
        }
        logger.info(
            "Global summary generated: %d decisions, %d agenda items",
            len(state.global_summary["decisions"]),
            len(state.global_summary["agenda_items"]),
        )
    except Exception as e:
        logger.error("Global summary generation failed: %s", str(e))
        state.global_summary = {
            "summary": f"Summary generation failed: {str(e)}",
            "decisions": [],
            "agenda_items": [],
        }

    return state


# ------------------------------------------------------------------ #
# Node 3: identify_attendees_node
# ------------------------------------------------------------------ #
async def identify_attendees_node(state: IngestionState) -> IngestionState:
    """
    Match speaker names from the transcript against registered meeting attendees.
    Uses fuzzy first-name matching (case-insensitive).
    Produces: {speaker_name: user_id}
    """
    if not state.registered_attendees:
        logger.info("No registered attendees — skipping attendee matching")
        return state

    speakers = set(t["speaker"] for t in state.parsed_turns if t["speaker"] != "Unknown")

    if not speakers:
        logger.info("No identified speakers in transcript")
        return state

    mapping = {}

    for speaker in speakers:
        speaker_lower = speaker.lower().strip()
        speaker_first = speaker_lower.split()[0] if speaker_lower.split() else ""

        best_match = None
        for user_id, user_name in state.registered_attendees.items():
            user_lower = user_name.lower().strip()
            user_first = user_lower.split()[0] if user_lower.split() else ""

            # Exact name match
            if speaker_lower == user_lower:
                best_match = user_id
                break

            # First name match
            if speaker_first and user_first and speaker_first == user_first:
                best_match = user_id
                break

            # Partial match (speaker name contains user name or vice versa)
            if speaker_lower in user_lower or user_lower in speaker_lower:
                best_match = user_id
                break

        if best_match:
            mapping[speaker] = best_match

    state.speaker_to_user = mapping
    logger.info(
        "Attendee matching: %d/%d speakers resolved to users: %s",
        len(mapping), len(speakers),
        {s: state.registered_attendees.get(uid, uid[:8]) for s, uid in mapping.items()},
    )

    # Also identify attendees mentioned by name but who never speak
    # (they should still get extracts if decisions/deadlines affect them)
    all_text = " ".join(t["text"] for t in state.parsed_turns).lower()
    for user_id, user_name in state.registered_attendees.items():
        if user_id not in mapping.values():
            user_first = user_name.lower().split()[0] if user_name else ""
            if user_first and user_first in all_text:
                # This attendee is mentioned but doesn't speak
                mapping[f"__mentioned__{user_name}"] = user_id
                logger.info(
                    "Attendee %s mentioned but never speaks — will extract their items",
                    user_name,
                )

    state.speaker_to_user = mapping
    return state


# ------------------------------------------------------------------ #
# Node 4: per_person_extract_node
# ------------------------------------------------------------------ #
async def per_person_extract_node(state: IngestionState) -> IngestionState:
    """
    For each resolved attendee, make one Groq call to extract personalized items.
    Output per user: {action_items[], mentions[], decisions_affecting_them[], deadlines[]}

    Handles attendees who are mentioned but never speak by including the full
    transcript context and asking Groq to find items that affect them.
    """
    transcript_text = "\n".join(
        f"{t['speaker']}: {t['text']}" for t in state.parsed_turns
    )

    # De-duplicate: collect unique user_ids to process
    user_ids_to_process = set(state.speaker_to_user.values())

    if not user_ids_to_process:
        logger.info("No resolved attendees — skipping per-person extraction")
        return state

    for user_id in user_ids_to_process:
        # Find the name(s) for this user
        user_name = state.registered_attendees.get(user_id, "Unknown")
        speaker_names = [
            s for s, uid in state.speaker_to_user.items()
            if uid == user_id and not s.startswith("__mentioned__")
        ]
        is_mentioned_only = not speaker_names

        system_prompt = (
            "You are MeetMind, analyzing a meeting transcript for a specific attendee. "
            "Return ONLY valid JSON with this exact schema:\n"
            "{\n"
            '  "action_items": ["Action item 1 assigned to or relevant for this person", ...],\n'
            '  "mentions": ["Context where this person was mentioned or involved", ...],\n'
            '  "decisions_affecting_them": ["Decision that impacts this person", ...],\n'
            '  "deadlines": ["Deadline: description (date if mentioned)", ...]\n'
            "}\n"
            "Extract items that are specifically relevant to, assigned to, or affect this person. "
            "If they were mentioned but didn't speak, still extract decisions and deadlines that "
            "involve them. Return empty arrays if nothing applies."
        )

        if is_mentioned_only:
            user_prompt = (
                f"Person: {user_name} (mentioned in the meeting but did not speak)\n"
                f"Meeting: {state.meeting_title}\n\n"
                f"--- TRANSCRIPT ---\n{transcript_text[:25000]}"
            )
        else:
            user_prompt = (
                f"Person: {user_name} (spoke as: {', '.join(speaker_names)})\n"
                f"Meeting: {state.meeting_title}\n\n"
                f"--- TRANSCRIPT ---\n{transcript_text[:25000]}"
            )

        try:
            result = await groq_client.extract_json(system_prompt, user_prompt)
            state.person_extracts[user_id] = {
                "user_name": user_name,
                "action_items": result.get("action_items", []),
                "mentions": result.get("mentions", []),
                "decisions_affecting_them": result.get("decisions_affecting_them", []),
                "deadlines": result.get("deadlines", []),
                "is_mentioned_only": is_mentioned_only,
            }
            logger.info(
                "Per-person extract for %s: %d actions, %d decisions, %d deadlines",
                user_name,
                len(state.person_extracts[user_id]["action_items"]),
                len(state.person_extracts[user_id]["decisions_affecting_them"]),
                len(state.person_extracts[user_id]["deadlines"]),
            )
        except Exception as e:
            logger.error("Per-person extraction failed for %s: %s", user_name, str(e))
            state.person_extracts[user_id] = {
                "user_name": user_name,
                "action_items": [],
                "mentions": [],
                "decisions_affecting_them": [],
                "deadlines": [],
                "error": str(e),
            }

    return state


# ------------------------------------------------------------------ #
# Node 5: store_in_cognee_node
# ------------------------------------------------------------------ #
async def store_in_cognee_node(state: IngestionState) -> IngestionState:
    """
    Store results in Cognee:
    - Global summary → under ALL attendee user_ids with node_set=[meeting_id]
    - Per-person extract → under ONLY that attendee's user_id with node_set=[meeting_id]
    - Run cognify() once after all adds
    """
    import json

    # Prepare global summary content
    global_content = (
        f"MEETING SUMMARY: {state.meeting_title}\n\n"
        f"{state.global_summary.get('summary', '')}\n\n"
        f"DECISIONS:\n" + "\n".join(f"- {d}" for d in state.global_summary.get("decisions", [])) + "\n\n"
        f"AGENDA ITEMS:\n" + "\n".join(f"- {a}" for a in state.global_summary.get("agenda_items", []))
    )

    # Get all unique user_ids (both speaking and mentioned-only attendees)
    all_user_ids = list(set(state.speaker_to_user.values()))

    # If no attendees matched, store under all registered attendees anyway
    if not all_user_ids and state.registered_attendees:
        all_user_ids = list(state.registered_attendees.keys())

    if all_user_ids:
        # Store global summary for ALL attendees
        await cognee_service.add_global_summary(
            meeting_id=state.meeting_id,
            attendee_user_ids=all_user_ids,
            content=global_content,
        )

        # Store per-person extracts for EACH individual attendee
        for user_id, extract in state.person_extracts.items():
            extract_content = (
                f"PERSONAL MEETING EXTRACT for {extract.get('user_name', 'Unknown')}\n"
                f"Meeting: {state.meeting_title}\n\n"
                f"ACTION ITEMS:\n" + "\n".join(f"- {a}" for a in extract.get("action_items", [])) + "\n\n"
                f"MENTIONS:\n" + "\n".join(f"- {m}" for m in extract.get("mentions", [])) + "\n\n"
                f"DECISIONS AFFECTING YOU:\n" + "\n".join(f"- {d}" for d in extract.get("decisions_affecting_them", [])) + "\n\n"
                f"DEADLINES:\n" + "\n".join(f"- {d}" for d in extract.get("deadlines", []))
            )
            await cognee_service.add_person_extract(
                meeting_id=state.meeting_id,
                user_id=user_id,
                content=extract_content,
            )

        # Run cognify once after all adds
        await cognee_service.run_cognify()
        state.cognee_stored = True
        logger.info(
            "Cognee storage complete: global for %d users, %d personal extracts",
            len(all_user_ids), len(state.person_extracts),
        )
    else:
        logger.warning("No attendees to store content for")

    return state


# ------------------------------------------------------------------ #
# Node 6: finalize_node
# ------------------------------------------------------------------ #
async def finalize_node(state: IngestionState) -> IngestionState:
    """
    Update the meeting status to 'ready' (or 'failed' with error message).
    Also stores the global summary text on the meeting row.
    """
    if state.error_message:
        state.final_status = "failed"
    else:
        state.final_status = "ready"

    logger.info(
        "Ingestion pipeline complete for meeting %s — status=%s",
        state.meeting_id[:8], state.final_status,
    )
    return state


# ------------------------------------------------------------------ #
# Pipeline runner
# ------------------------------------------------------------------ #
async def run_ingestion_pipeline(
    meeting_id: str,
    meeting_title: str,
    raw_transcript: str,
    registered_attendees: dict[str, str],
    db: AsyncSession,
) -> IngestionState:
    """
    Run the full ingestion pipeline for a meeting.

    This is the main entry point called by the meetings router when
    status='processing' and raw_transcript is non-empty.

    Args:
        meeting_id: Meeting UUID as string.
        meeting_title: Meeting title for context.
        raw_transcript: The raw transcript text.
        registered_attendees: Dict of {user_id: user_name} for all attendees.
        db: Async database session.

    Returns:
        The final IngestionState with all extracted data.
    """
    from app.models.meeting import Meeting

    state = IngestionState(
        meeting_id=meeting_id,
        meeting_title=meeting_title,
        raw_transcript=raw_transcript,
        registered_attendees=registered_attendees,
    )

    pipeline_nodes = [
        ("parse_transcript", parse_transcript),
        ("global_summary", global_summary_node),
        ("identify_attendees", identify_attendees_node),
        ("per_person_extract", per_person_extract_node),
        ("store_in_cognee", store_in_cognee_node),
        ("finalize", finalize_node),
    ]

    for node_name, node_fn in pipeline_nodes:
        try:
            logger.info("Pipeline node: %s — starting", node_name)
            state = await node_fn(state)
            logger.info("Pipeline node: %s — complete", node_name)
        except Exception as e:
            logger.error("Pipeline node %s failed: %s", node_name, str(e))
            state.error_message = f"Pipeline failed at {node_name}: {str(e)}"
            state.final_status = "failed"
            break

    # Update meeting in DB
    result = await db.execute(
        select(Meeting).where(Meeting.id == UUID(meeting_id))
    )
    meeting = result.scalar_one_or_none()
    if meeting:
        meeting.status = state.final_status
        # Store the structured summary
        import json
        summary_text = state.global_summary.get("summary", "")
        decisions = state.global_summary.get("decisions", [])
        agenda = state.global_summary.get("agenda_items", [])

        meeting.summary = (
            f"{summary_text}\n\n"
            f"## Decisions\n" + "\n".join(f"- {d}" for d in decisions) + "\n\n"
            f"## Agenda Items\n" + "\n".join(f"- {a}" for a in agenda)
        )

        if state.error_message:
            meeting.summary = f"⚠️ {state.error_message}\n\n{meeting.summary or ''}"

        await db.flush()
        logger.info("Meeting %s updated: status=%s", meeting_id[:8], state.final_status)

    return state
