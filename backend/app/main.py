"""
MeetMind — FastAPI application entry point.

MeetMind is an AI meeting assistant that:
  1. Attends live Teams meetings via bot (captures transcript automatically)
  2. Accepts exported Google Meet transcripts
  3. Accepts manually pasted transcripts from any source
  4. Generates AI-powered summaries from all transcripts

On startup:
  1. Validates Azure AD prerequisites for Teams bot (degraded mode if missing).
  2. Validates Groq API key for summarization (fallback mode if missing).
  3. Registers all routers (auth, meetings, bot webhook).
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import auth, meetings
from app.services.teams_bot_service import teams_bot_service
from bots.teams_bot.bot_handler import router as bot_router

# ------------------------------------------------------------------ #
# Logging
# ------------------------------------------------------------------ #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-30s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger("meetmind")


# ------------------------------------------------------------------ #
# Lifespan (startup / shutdown)
# ------------------------------------------------------------------ #
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup tasks before the app starts serving requests."""
    logger.info("=" * 60)
    logger.info("  MeetMind Backend — Starting Up")
    logger.info("=" * 60)

    # Azure prerequisite check
    azure_ok = await teams_bot_service.validate_prerequisites()
    app.state.azure_configured = azure_ok

    if azure_ok:
        logger.info("🤖 Teams live-join: ENABLED — bot can attend meetings")
    else:
        logger.info("⚠️  Teams live-join: DISABLED (degraded mode)")

    # Groq / LLM check
    if settings.groq_configured:
        logger.info("🧠 AI Summarization: ENABLED (Groq %s)", settings.GROQ_MODEL)
        app.state.groq_configured = True
    else:
        logger.info("⚠️  AI Summarization: FALLBACK MODE (set GROQ_API_KEY for AI summaries)")
        app.state.groq_configured = False

    logger.info("📝 Meet transcript import: ENABLED (no external dependency)")
    logger.info("📋 Manual transcript upload: ENABLED")
    logger.info("=" * 60)
    logger.info("  MeetMind Backend — Ready")
    logger.info("=" * 60)

    yield  # App runs here

    logger.info("MeetMind Backend — Shutting down")


# ------------------------------------------------------------------ #
# App
# ------------------------------------------------------------------ #
app = FastAPI(
    title="MeetMind API",
    description=(
        "AI Meeting Assistant — attends your meetings, captures transcripts, "
        "and generates personalized summaries for each attendee."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router)
app.include_router(meetings.router)
app.include_router(bot_router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "azure_configured": getattr(app.state, "azure_configured", False),
        "groq_configured": getattr(app.state, "groq_configured", False),
        "capabilities": {
            "teams_live_join": getattr(app.state, "azure_configured", False),
            "ai_summarization": getattr(app.state, "groq_configured", False),
            "meet_import": True,
            "manual_transcript": True,
        },
    }
