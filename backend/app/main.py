"""
MeetMind — FastAPI application entry point (v0.4.0).

Capabilities:
  1. Attends live Teams meetings via bot
  2. Accepts exported Google Meet transcripts
  3. Accepts manually pasted transcripts
  4. Generates AI-powered summaries
  5. Runs LangGraph ingestion pipeline for per-attendee extracts
  6. Stores meeting memory in Cognee Cloud (or in-memory fallback)
  7. Streaming chat with per-user isolated meeting context
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import auth, meetings
from app.routers.chat import router as chat_router
from app.services.teams_bot_service import teams_bot_service
from app.services.cognee_service import configure_cognee, is_cloud_connected
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
    logger.info("  MeetMind Backend — Starting Up (v0.4.0)")
    logger.info("=" * 60)

    # Azure prerequisite check
    azure_ok = await teams_bot_service.validate_prerequisites()
    app.state.azure_configured = azure_ok
    if azure_ok:
        logger.info("🤖 Teams live-join: ENABLED")
    else:
        logger.info("⚠️  Teams live-join: DISABLED (degraded mode)")

    # Groq / LLM check
    if settings.groq_configured:
        logger.info("🧠 AI Summarization: ENABLED (Groq %s)", settings.GROQ_MODEL)
        app.state.groq_configured = True
    else:
        logger.info("⚠️  AI Summarization: FALLBACK MODE (set GROQ_API_KEY)")
        app.state.groq_configured = False

    # Cognee Cloud connection
    cognee_ok = await configure_cognee()
    app.state.cognee_cloud = cognee_ok
    if not cognee_ok:
        if not settings.cognee_configured:
            logger.info("⚠️  Cognee Cloud: NOT CONFIGURED (set COGNEE_API_KEY)")
        # else: warning already logged in configure_cognee

    logger.info("📝 Meet transcript import: ENABLED")
    logger.info("📋 Manual transcript upload: ENABLED")
    logger.info("🔄 LangGraph ingestion pipeline: ENABLED")
    logger.info("💬 Streaming chat: ENABLED")
    logger.info("=" * 60)
    logger.info("  MeetMind Backend — Ready")
    logger.info("=" * 60)

    yield  # App runs here

    # Cleanup: disconnect from Cognee Cloud if connected
    if is_cloud_connected():
        try:
            import cognee
            await cognee.disconnect()
            logger.info("Cognee Cloud disconnected")
        except Exception:
            pass

    logger.info("MeetMind Backend — Shutting down")


# ------------------------------------------------------------------ #
# App
# ------------------------------------------------------------------ #
app = FastAPI(
    title="MeetMind API",
    description=(
        "AI Meeting Assistant — attends meetings, captures transcripts, "
        "generates personalized summaries, and provides per-user isolated chat."
    ),
    version="0.4.0",
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
app.include_router(chat_router)
app.include_router(bot_router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "0.4.0",
        "azure_configured": getattr(app.state, "azure_configured", False),
        "groq_configured": getattr(app.state, "groq_configured", False),
        "cognee_cloud": getattr(app.state, "cognee_cloud", False),
        "capabilities": {
            "teams_live_join": getattr(app.state, "azure_configured", False),
            "ai_summarization": getattr(app.state, "groq_configured", False),
            "meet_import": True,
            "manual_transcript": True,
            "ingestion_pipeline": True,
            "cognee_memory": True,
            "streaming_chat": True,
        },
    }
