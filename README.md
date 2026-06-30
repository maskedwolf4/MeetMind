# MeetMind

AI-powered meeting assistant that summarizes meeting transcripts and lets each
attendee chat with a personalized, isolated view of that meeting — no leakage of
one attendee's action items to another.

## Phase 1 — Foundation + Meeting Acquisition (Current)

- **Supabase Postgres schema** — 5 tables (users, meetings, meeting_attendees, chat_threads, chat_messages)
- **FastAPI backend** — JWT auth, meeting CRUD, Teams bot join, Meet import
- **Next.js frontend** — Login, Register, Dashboard with meeting connection UI
- **Microsoft Teams bot** — Joins live meetings via Graph API, captures transcripts
- **Google Meet import** — Export-based transcript ingestion (not live)

## Phase 2 — Memory & Ingestion (Day 2)

- Cognee memory layer with per-user isolation
- LangGraph ingestion pipeline
- Meeting CRUD endpoints
- Groq LLM provider for extraction

## Phase 3 — Chat Layer (Day 3)

- Streaming chat endpoint
- Conversation history persistence
- Full Next.js dashboard and chat UI
- Demo-readiness polish with seeded dataset

## Quick Start

See [docs/README.md](docs/README.md) for full setup instructions.

### Backend
```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Edit with your DATABASE_URL
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for full system design.

## Setup Guides

- [Teams Bot Setup](docs/TEAMS_BOT_SETUP.md) — Azure AD app registration
- [Meet Ingestion](docs/MEET_INGESTION.md) — Google Meet transcript export