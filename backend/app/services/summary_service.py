"""
Summary Service — generates meeting summaries from transcripts using Groq LLM.

This service is called automatically after a transcript is captured (from any source:
Teams live, Teams export, Meet export, or manual upload). It:
  1. Takes the raw transcript text
  2. Sends it to Groq's LLM API with a carefully crafted prompt
  3. Returns a structured meeting summary with:
     - Key discussion points
     - Decisions made
     - Action items (per attendee when identifiable)
     - Follow-ups needed
  4. Updates the meeting row: sets summary, status='ready'

If GROQ_API_KEY is not configured, falls back to a basic extractive summary
(first/last lines + word count stats) so the pipeline never breaks.
"""

import logging
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("meetmind.summary")

# The system prompt that shapes how the LLM summarizes meetings
SUMMARY_SYSTEM_PROMPT = """You are MeetMind, an expert AI meeting assistant. Your job is to analyze 
meeting transcripts and produce clear, actionable summaries.

Given a meeting transcript, produce a structured summary with these sections:

## 📋 Meeting Overview
A 2-3 sentence high-level summary of what the meeting was about.

## 🔑 Key Discussion Points
Bullet points of the main topics discussed, in the order they came up.

## ✅ Decisions Made
Specific decisions that were agreed upon during the meeting. If none, say "No explicit decisions recorded."

## 📌 Action Items
List action items with the responsible person (if identifiable from the transcript).
Format: - **[Person]**: Action item description
If attendees aren't identifiable, list items without names.

## 🔄 Follow-ups Needed
Items that need follow-up in the next meeting or async.

## 📊 Meeting Stats
- Duration: (estimate from timestamps if available)
- Participants: (list names mentioned)
- Mood/Tone: (professional, casual, urgent, etc.)

Keep the summary concise but comprehensive. Use the actual content from the transcript — 
do not invent or assume information not present."""


class SummaryService:
    """Generates meeting summaries from transcripts using Groq LLM."""

    async def generate_summary(self, transcript: str, meeting_title: str = "") -> str:
        """
        Generate a meeting summary from a transcript.

        Args:
            transcript: The raw meeting transcript text.
            meeting_title: Optional meeting title for context.

        Returns:
            The generated summary text (markdown formatted).
        """
        if not settings.groq_configured:
            logger.warning(
                "GROQ_API_KEY not configured — falling back to extractive summary. "
                "Set GROQ_API_KEY in .env for AI-powered summaries."
            )
            return self._extractive_fallback(transcript, meeting_title)

        try:
            summary = await self._call_groq(transcript, meeting_title)
            logger.info(
                "AI summary generated: %d chars from %d char transcript",
                len(summary), len(transcript),
            )
            return summary
        except Exception as e:
            logger.error("Groq API call failed: %s — falling back to extractive summary", str(e))
            return self._extractive_fallback(transcript, meeting_title)

    async def _call_groq(self, transcript: str, meeting_title: str) -> str:
        """Call the Groq API to generate a summary."""
        user_prompt = f"Meeting Title: {meeting_title}\n\n--- TRANSCRIPT ---\n{transcript}\n--- END TRANSCRIPT ---"

        # Truncate very long transcripts to fit context window (~128k tokens for llama-3.3-70b)
        # Rough heuristic: 1 token ≈ 4 chars, leave room for system prompt + response
        max_transcript_chars = 400_000  # ~100k tokens, leaves room for prompt + response
        if len(user_prompt) > max_transcript_chars:
            user_prompt = user_prompt[:max_transcript_chars] + "\n\n[... transcript truncated due to length ...]"

        payload = {
            "model": settings.GROQ_MODEL,
            "messages": [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,  # Low temperature for factual, consistent summaries
            "max_tokens": 4096,
        }

        headers = {
            "Authorization": f"Bearer {settings.GROQ_API_KEY}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json=payload,
                headers=headers,
            )

            if resp.status_code != 200:
                raise RuntimeError(f"Groq API error ({resp.status_code}): {resp.text}")

            data = resp.json()
            return data["choices"][0]["message"]["content"]

    def _extractive_fallback(self, transcript: str, meeting_title: str) -> str:
        """
        Basic extractive summary when Groq is not available.
        Extracts key statistics and first/last segments of the transcript.
        """
        lines = [line.strip() for line in transcript.strip().split("\n") if line.strip()]
        total_lines = len(lines)
        total_words = sum(len(line.split()) for line in lines)

        # Extract speaker names (lines that look like "Name (timestamp)" or "Name:")
        speakers = set()
        for line in lines:
            if "(" in line and ")" in line:
                name = line.split("(")[0].strip()
                if name and len(name) < 50:
                    speakers.add(name)
            elif ":" in line and len(line.split(":")[0].strip()) < 50:
                name = line.split(":")[0].strip()
                if name and not name[0].isdigit():
                    speakers.add(name)

        # Build basic summary
        summary_parts = [
            f"## 📋 Meeting Overview",
            f"Meeting: **{meeting_title or 'Untitled Meeting'}**",
            "",
            f"*This is an automated extractive summary. Set `GROQ_API_KEY` for AI-powered summaries.*",
            "",
            f"## 📊 Meeting Stats",
            f"- **Total lines:** {total_lines}",
            f"- **Total words:** {total_words:,}",
            f"- **Estimated duration:** ~{max(1, total_words // 150)} minutes (at ~150 words/min speaking rate)",
        ]

        if speakers:
            summary_parts.append(f"- **Participants identified:** {', '.join(sorted(speakers))}")

        # First few lines as opening context
        summary_parts.extend([
            "",
            "## 🔑 Opening Discussion",
        ])
        for line in lines[:min(5, total_lines)]:
            summary_parts.append(f"> {line}")

        # Last few lines as closing context
        if total_lines > 5:
            summary_parts.extend([
                "",
                "## 🔄 Closing Discussion",
            ])
            for line in lines[max(-5, -total_lines):]:
                summary_parts.append(f"> {line}")

        summary_parts.extend([
            "",
            "---",
            "*Full transcript available in the meeting record.*",
        ])

        return "\n".join(summary_parts)


# Module-level singleton for convenience
summary_service = SummaryService()
