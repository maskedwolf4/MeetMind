# MeetMind

AI-powered meeting assistant that summarizes meeting transcripts and lets each attendee chat with a personalized, isolated view of that meeting.

## Architecture

MeetMind is built in three phases:

### Phase 1 (This Phase) — Foundation + Meeting Acquisition
- **Supabase Postgres schema** with 5 tables: users, meetings, meeting_attendees, chat_threads, chat_messages
- **FastAPI backend** with JWT auth (register, login, refresh, me)
- **Next.js frontend** with auth pages (login, register, protected dashboard)
- **Microsoft Teams bot** that joins live meetings via Graph API
- **Google Meet import** for exported transcripts

### Phase 2 — Memory & Ingestion (Day 2)
- Cognee memory layer with per-user isolation
- LangGraph ingestion pipeline
- Meeting CRUD endpoints
- Groq LLM provider

### Phase 3 — Chat Layer (Day 3)
- Streaming chat endpoint
- Conversation history persistence
- Full Next.js dashboard and chat UI
- Demo-readiness polish

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- PostgreSQL 15+ (or Supabase project)

### Backend Setup

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your DATABASE_URL, JWT_SECRET, and optionally Azure credentials

# Run migrations
alembic upgrade head

# Start the server
uvicorn app.main:app --reload --port 8000
```

### Frontend Setup

```bash
cd frontend
npm install

# Copy and configure environment
cp .env.local.example .env.local
# Edit .env.local with your backend URL

# Start dev server
npm run dev
```

### Azure / Teams Bot Setup
See [docs/TEAMS_BOT_SETUP.md](docs/TEAMS_BOT_SETUP.md) for step-by-step Azure AD app registration instructions.

### Google Meet Import
See [docs/MEET_INGESTION.md](docs/MEET_INGESTION.md) for how the Meet export path works and its limitations.

## Tech Stack
- **Backend:** Python FastAPI, SQLAlchemy (async/asyncpg), Alembic
- **Frontend:** Next.js 14, TypeScript
- **Database:** Supabase Postgres
- **Auth:** JWT (python-jose), bcrypt (passlib)
- **Teams Integration:** Microsoft Bot Framework SDK, Microsoft Graph API
- **Meet Integration:** Export-based transcript import

## API Endpoints

### Auth
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/register` | Register new user |
| POST | `/auth/login` | Login with email/password |
| POST | `/auth/refresh` | Refresh access token |
| GET | `/auth/me` | Get current user profile |

### Meetings
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/meetings` | Create a draft meeting |
| GET | `/meetings/{id}` | Get meeting details |
| POST | `/meetings/{id}/teams/join` | Join Teams meeting via bot |
| POST | `/meetings/{id}/meet/import` | Import Meet transcript |

### Bot Framework
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/messages` | Bot messaging webhook |
| POST | `/api/calls` | Bot calling webhook |
