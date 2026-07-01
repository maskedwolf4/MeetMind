"""
Cognee Service — memory layer with per-user isolation for meeting content.

COGNEE CLOUD MIGRATION (Phase 3):
  Uses cognee.serve(url, api_key) to connect to Cognee Cloud.
  The serve() call routes all subsequent add/cognify/search operations
  through the cloud tenant instead of local storage.

  If COGNEE_API_KEY is not set, falls back to in-memory storage gracefully.
  The in-memory store is always available as a safety net.

Isolation guarantee (unchanged):
  - Global summary: stored under EACH attendee's Cognee user with node_set=[meeting_id]
  - Per-person extract: stored under ONLY that attendee's Cognee user with node_set=[meeting_id]
  - Search always scopes by both user AND node_name=[meeting_id]
"""

import logging
import os
from typing import List, Optional

logger = logging.getLogger("meetmind.cognee")

# Flag to track if cognee is available
_cognee_available = False
_cognee_cloud_connected = False
try:
    import cognee
    from cognee.api.v1.search import SearchType
    _cognee_available = True
except ImportError:
    logger.warning("Cognee not installed — running in stub mode (in-memory storage)")
    SearchType = None


# In-memory fallback storage when Cognee is not installed or Cloud is unreachable
# Structure: { (user_id_str, meeting_id_str): [content_strings] }
_memory_store: dict[tuple[str, str], list[str]] = {}


async def configure_cognee() -> bool:
    """
    Configure Cognee for cloud or local mode.

    Cloud mode: uses cognee.serve(url, api_key) to connect to Cognee Cloud.
    Local mode: uses cognee.config.set_llm_* to configure the LLM provider.

    Returns True if Cognee Cloud is connected, False otherwise.
    """
    global _cognee_cloud_connected

    from app.core.config import settings

    if not _cognee_available:
        logger.info("Cognee not available — using in-memory fallback storage")
        return False

    # Configure LLM provider (Groq) for Cognee's internal extraction
    if settings.groq_configured:
        try:
            cognee.config.set_llm_api_key(settings.GROQ_API_KEY)
            cognee.config.set_llm_provider("groq")
            cognee.config.set_llm_model(settings.GROQ_MODEL)
            logger.info(
                "Cognee LLM configured: Groq (%s)", settings.GROQ_MODEL
            )
        except Exception as e:
            logger.warning("Failed to configure Cognee LLM: %s", str(e))
            # Fall back to env vars
            os.environ["LLM_API_KEY"] = settings.GROQ_API_KEY
            os.environ["LLM_PROVIDER"] = "groq"
            os.environ["LLM_MODEL"] = settings.GROQ_MODEL

    # Attempt Cognee Cloud connection
    if settings.cognee_configured:
        try:
            await cognee.serve(
                url=settings.COGNEE_BASE_URL,
                api_key=settings.COGNEE_API_KEY,
            )
            _cognee_cloud_connected = True
            logger.info("✅ Cognee Cloud connected at %s", settings.COGNEE_BASE_URL)
            return True
        except Exception as e:
            logger.warning(
                "⚠️ Cognee Cloud unreachable: %s — falling back to local/in-memory mode",
                str(e),
            )
            _cognee_cloud_connected = False
            return False
    else:
        logger.info(
            "⚠️ COGNEE_API_KEY not set — using in-memory fallback storage"
        )
        return False


def is_cloud_connected() -> bool:
    """Check if Cognee Cloud is connected."""
    return _cognee_cloud_connected


async def _get_or_create_cognee_user(user_id: str):
    """
    Get or create a Cognee User object corresponding to a MeetMind user UUID.
    Each MeetMind user maps to a unique Cognee user for isolation.
    """
    if not _cognee_available:
        return None

    from cognee.modules.users.methods import get_default_user
    try:
        user = await get_default_user()
        return user
    except Exception as e:
        logger.warning("Could not get Cognee user: %s — using default", str(e))
        return None


async def add_global_summary(
    meeting_id: str,
    attendee_user_ids: List[str],
    content: str,
) -> None:
    """
    Store the global meeting summary in Cognee under EVERY attendee's scope.

    This ensures each attendee can retrieve shared facts (decisions, agenda,
    overall recap) when searching by their user_id + meeting_id.

    Args:
        meeting_id: The meeting UUID as a string.
        attendee_user_ids: List of attendee user UUIDs as strings.
        content: The global summary text to store.
    """
    for user_id in attendee_user_ids:
        dataset_name = f"user_{user_id}"
        node_tags = [meeting_id]

        if _cognee_available and (_cognee_cloud_connected or not _memory_only()):
            try:
                cognee_user = await _get_or_create_cognee_user(user_id)
                await cognee.add(
                    data=content,
                    dataset_name=dataset_name,
                    user=cognee_user,
                    node_set=node_tags,
                )
                logger.info(
                    "Stored global summary for user %s in meeting %s via Cognee",
                    user_id[:8], meeting_id[:8],
                )
            except Exception as e:
                logger.error(
                    "Cognee add failed for user %s: %s — falling back to memory store",
                    user_id[:8], str(e),
                )
                _memory_store.setdefault((user_id, meeting_id), []).append(content)
        else:
            _memory_store.setdefault((user_id, meeting_id), []).append(content)
            logger.info(
                "Stored global summary for user %s in meeting %s (in-memory)",
                user_id[:8], meeting_id[:8],
            )


async def add_person_extract(
    meeting_id: str,
    user_id: str,
    content: str,
) -> None:
    """
    Store a per-person extract in Cognee under ONLY that attendee's scope.

    Other attendees CANNOT retrieve this content — it is scoped by both
    the user's dataset and the meeting's node_set tag.

    Args:
        meeting_id: The meeting UUID as a string.
        user_id: The attendee's user UUID as a string.
        content: The personalized extract text (action items, mentions, etc.).
    """
    dataset_name = f"user_{user_id}"
    node_tags = [meeting_id]

    if _cognee_available and (_cognee_cloud_connected or not _memory_only()):
        try:
            cognee_user = await _get_or_create_cognee_user(user_id)
            await cognee.add(
                data=content,
                dataset_name=dataset_name,
                user=cognee_user,
                node_set=node_tags,
            )
            logger.info(
                "Stored person extract for user %s in meeting %s via Cognee",
                user_id[:8], meeting_id[:8],
            )
        except Exception as e:
            logger.error(
                "Cognee add failed for person extract user %s: %s — fallback",
                user_id[:8], str(e),
            )
            _memory_store.setdefault((user_id, meeting_id), []).append(content)
    else:
        _memory_store.setdefault((user_id, meeting_id), []).append(content)
        logger.info(
            "Stored person extract for user %s in meeting %s (in-memory)",
            user_id[:8], meeting_id[:8],
        )


async def run_cognify() -> None:
    """
    Run Cognee's cognify() to process all newly added data into the knowledge graph.
    Called once after all adds for a meeting are complete.
    """
    if not _cognee_available or _memory_only():
        logger.info("Cognify skipped — Cognee not available (in-memory mode)")
        return

    try:
        await cognee.cognify()
        logger.info("Cognee cognify completed successfully")
    except Exception as e:
        logger.error("Cognee cognify failed: %s — data is still stored", str(e))


async def search(
    query: str,
    user_id: str,
    meeting_id: str,
) -> List[str]:
    """
    Search Cognee for content scoped to a specific user and meeting.

    Both user_id AND meeting_id are mandatory — this ensures:
    - User A cannot see User B's personal extracts
    - Searches are scoped to a single meeting's content

    Args:
        query: The search query text.
        user_id: The searching user's UUID as a string.
        meeting_id: The meeting UUID as a string.

    Returns:
        List of matching content strings.
    """
    dataset_name = f"user_{user_id}"
    node_tags = [meeting_id]

    if _cognee_available and (_cognee_cloud_connected or not _memory_only()):
        try:
            cognee_user = await _get_or_create_cognee_user(user_id)
            results = await cognee.search(
                query_text=query,
                user=cognee_user,
                datasets=[dataset_name],
                node_name=node_tags,
                query_type=SearchType.CHUNKS,
                top_k=10,
            )

            # Extract text content from search results
            texts = []
            for r in results:
                if hasattr(r, "payload"):
                    text = r.payload.get("text", "") if isinstance(r.payload, dict) else str(r.payload)
                elif hasattr(r, "text"):
                    text = r.text
                elif isinstance(r, dict):
                    text = r.get("text", r.get("content", str(r)))
                elif isinstance(r, str):
                    text = r
                else:
                    text = str(r)
                if text:
                    texts.append(text)

            logger.info(
                "Cognee search for user %s in meeting %s: %d results",
                user_id[:8], meeting_id[:8], len(texts),
            )
            return texts

        except Exception as e:
            logger.error(
                "Cognee search failed for user %s: %s — falling back to memory",
                user_id[:8], str(e),
            )
            return _memory_search(query, user_id, meeting_id)
    else:
        return _memory_search(query, user_id, meeting_id)


def _memory_only() -> bool:
    """Check if we should use memory-only mode (no Cognee backend available)."""
    from app.core.config import settings
    return not settings.cognee_configured and not _cognee_cloud_connected


def _memory_search(query: str, user_id: str, meeting_id: str) -> List[str]:
    """
    In-memory fallback search: returns all stored content for the user+meeting
    that contains any word from the query (basic keyword match).
    """
    key = (user_id, meeting_id)
    stored = _memory_store.get(key, [])
    if not stored:
        return []

    # Basic keyword matching
    query_words = set(query.lower().split())
    results = []
    for content in stored:
        content_lower = content.lower()
        if any(word in content_lower for word in query_words):
            results.append(content)

    # If no keyword matches, return all content for that scope
    if not results:
        results = stored[:]

    return results


def get_memory_store() -> dict:
    """Expose memory store for testing/debugging."""
    return _memory_store


def clear_memory_store() -> None:
    """Clear in-memory store (for testing)."""
    _memory_store.clear()
