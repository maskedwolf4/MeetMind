"""
Groq Service — shared LLM client for all extraction tasks.

Model: llama-3.3-70b-versatile (Groq-hosted)
Temperature: 0.1 for extraction tasks (deterministic, factual outputs)

All prompts request JSON-only output. The wrapper includes retry logic
for malformed JSON responses (1 retry before failing the call).
"""

import json
import logging
from typing import Any, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("meetmind.groq")

# Groq model for structured JSON extraction
GROQ_EXTRACTION_MODEL = "llama-3.3-70b-versatile"
GROQ_EXTRACTION_TEMPERATURE = 0.1


class GroqClient:
    """
    Shared Groq LLM client for all MeetMind extraction tasks.

    All extraction calls request JSON-only output and include retry logic
    for malformed JSON (1 retry with an explicit "return valid JSON" nudge).
    """

    def __init__(self):
        self.api_key = settings.GROQ_API_KEY
        self.model = settings.GROQ_MODEL or GROQ_EXTRACTION_MODEL
        self.base_url = "https://api.groq.com/openai/v1/chat/completions"

    async def extract_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = GROQ_EXTRACTION_TEMPERATURE,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        """
        Call Groq with a prompt that expects JSON output.
        Parses the response, retries once on malformed JSON.

        Args:
            system_prompt: System instruction (should mention JSON output).
            user_prompt: The user content to process.
            temperature: LLM temperature (default 0.1 for extraction).
            max_tokens: Max response tokens.

        Returns:
            Parsed JSON dict.

        Raises:
            ValueError: If JSON is malformed after retry.
            RuntimeError: If the Groq API call fails.
        """
        raw = await self._call_groq(system_prompt, user_prompt, temperature, max_tokens)

        # Attempt 1: parse JSON
        parsed = self._try_parse_json(raw)
        if parsed is not None:
            return parsed

        # Retry: ask Groq to fix the JSON
        logger.warning("Malformed JSON from Groq — retrying with fix prompt")
        retry_prompt = (
            f"Your previous response was not valid JSON. Here it is:\n\n{raw}\n\n"
            "Please return ONLY valid JSON with no markdown formatting, no code fences, "
            "no extra text. Just the raw JSON object."
        )
        raw_retry = await self._call_groq(
            system_prompt, retry_prompt, temperature, max_tokens
        )
        parsed_retry = self._try_parse_json(raw_retry)
        if parsed_retry is not None:
            logger.info("Retry succeeded — valid JSON obtained")
            return parsed_retry

        raise ValueError(
            f"Groq returned malformed JSON after retry. Raw output: {raw_retry[:500]}"
        )

    async def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """Call Groq for plain-text generation (non-JSON)."""
        return await self._call_groq(system_prompt, user_prompt, temperature, max_tokens)

    async def _call_groq(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Raw Groq API call. Returns the text content of the response."""
        if not self.api_key:
            raise RuntimeError(
                "GROQ_API_KEY not configured. Set it in .env to enable AI extraction."
            )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(self.base_url, json=payload, headers=headers)

            if resp.status_code != 200:
                raise RuntimeError(
                    f"Groq API error ({resp.status_code}): {resp.text[:500]}"
                )

            data = resp.json()
            return data["choices"][0]["message"]["content"]

    def _try_parse_json(self, text: str) -> Optional[dict[str, Any]]:
        """
        Try to parse JSON from Groq's response, handling common formatting issues:
        - Markdown code fences (```json ... ```)
        - Leading/trailing whitespace
        - Nested JSON within text
        """
        cleaned = text.strip()

        # Remove markdown code fences if present
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first line (```json or ```) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Try to find JSON object in the text
        brace_start = cleaned.find("{")
        brace_end = cleaned.rfind("}")
        if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
            try:
                return json.loads(cleaned[brace_start : brace_end + 1])
            except json.JSONDecodeError:
                pass

        return None


# Module-level singleton
groq_client = GroqClient()
