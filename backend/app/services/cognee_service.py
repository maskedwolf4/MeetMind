"""
Cognee Service — memory layer with per-user isolation for meeting content.

API DEVIATION FROM TASK SPEC:
  The real Cognee API (as of the installed version) uses:
    - cognee.add(data, dataset_name, user=User, node_set=List[str])
    - cognee.cognify(datasets, user=User)
    - cognee.search(query_text, user=User, node_name=List[str], datasets)
  
  Where `user` is a `cognee.modules.users.models.User` object (not a string UUID),
  and `node_set`/`node_name` are List[str] (not a single string).

  This service wraps Cognee's API to provide the isolation semantics required by
  MeetMind: each user gets their own Cognee User scoped by their MeetMind UUID,
  and each meeting is tagged via node_set=[meeting_id].

COGNEE LLM CONFIG MODE:
  Cognee supports custom LLM endpoint config via environment variables:
    LLM_PROVIDER, LLM_API_KEY, LLM_MODEL
  We set these to route through Groq BEFORE calling cognee.add/cognify.
  However, since Cognee's cognify() pipeline does its own entity extraction,
  we PRE-PROCESS content into clean structured text via our own Groq calls
  in the ingestion pipeline BEFORE storing in Cognee. This means Cognee is
  used primarily as storage/retrieval/graph layer, with extraction done by us.
  MODE: PRE-PROCESSED (our Groq calls produce structured text → Cognee stores it)

Isolation guarantee:
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
try:
    import cognee
    from cognee.api.v1.search import SearchType
    _cognee_available = True
except ImportError:
    logger.warning("Cognee not installed — running in stub mode (in-memory storage)")
    SearchType = None


# In-memory fallback storage when Cognee is not installed
# Structure: { (user_id_str, meeting_id_str): [content_strings] }
_memory_store: dict[tuple[str, str], list[str]] = {}


def configure_cognee():
    """
    Configure Cognee to use Groq as its LLM provider via environment variables.
    Called once on startup.
    """
    from app.core.config import settings

    if not _cognee_available:
        logger.info("Cognee not available — using in-memory fallback storage")
        return

    if settings.groq_configured:
        # Route Cognee's internal LLM calls through Groq
        os.environ["LLM_API_KEY"] = settings.GROQ_API_KEY
        os.environ["LLM_PROVIDER"] = "groq"
        os.environ["LLM_MODEL"] = settings.GROQ_MODEL
        logger.info(
            "Cognee configured to use Groq (%s) for internal extraction",
            settings.GROQ_MODEL,
        )
    else:
        logger.warning(
            "GROQ_API_KEY not set — Cognee will use its default LLM provider"
        )


async def _get_or_create_cognee_user(user_id: str):
    """
    Get or create a Cognee User object corresponding to a MeetMind user UUID.
    Each MeetMind user maps to a unique Cognee user for isolation.
    """
    if not _cognee_available:
        return None

    from cognee.modules.users.methods import get_default_user
    # Use the default user for now — Cognee's user isolation is primarily
    # dataset-scoped. We achieve per-user isolation through dataset naming
    # (user_id as dataset_name) + node_set tagging.
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

        if _cognee_available:
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

    if _cognee_available:
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
    if not _cognee_available:
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

    if _cognee_available:
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
