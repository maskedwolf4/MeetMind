# MeetMind Architecture

## System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                         Frontend (Next.js)                       │
│  ┌──────────┐  ┌──────────┐  ┌────────────────────────────────┐ │
│  │  Login   │  │ Register │  │    Dashboard (Day 3 full UI)   │ │
│  │  Page    │  │  Page    │  │  ┌──────────────────────────┐  │ │
│  └──────────┘  └──────────┘  │  │ Connect Meeting Stub     │  │ │
│                              │  │ • Teams Join URL input    │  │ │
│                              │  │ • Meet Transcript import  │  │ │
│                              │  └──────────────────────────┘  │ │
│                              └────────────────────────────────┘ │
└───────────────────────────┬──────────────────────────────────────┘
                            │ HTTP (JWT Bearer)
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                      Backend (FastAPI)                            │
│                                                                  │
│  ┌─────────────────┐  ┌────────────────┐  ┌──────────────────┐  │
│  │  Auth Router     │  │ Meetings Router│  │ Bot Webhook      │  │
│  │  /auth/*         │  │ /meetings/*    │  │ /api/messages    │  │
│  │                  │  │                │  │ /api/calls       │  │
│  └────────┬─────────┘  └───────┬────────┘  └───────┬──────────┘  │
│           │                    │                    │            │
│  ┌────────▼─────────────────────▼────────────────────▼──────────┐│
│  │                     Services Layer                           ││
│  │  ┌──────────────────┐  ┌───────────────────────────────────┐ ││
│  │  │ MeetIngestion    │  │ TeamsBotService                   │ ││
│  │  │ Service          │  │ • Graph token acquisition         │ ││
│  │  │ • Store raw text │  │ • schedule_join (call creation)   │ ││
│  │  │ • Set status     │  │ • on_transcript_chunk             │ ││
│  │  │                  │  │ • on_call_ended                   │ ││
│  │  └──────────────────┘  │ • Post-call transcript retrieval  │ ││
│  │                        └───────────────────────────────────┘ ││
│  └──────────────────────────────────────────────────────────────┘│
│           │                                         │            │
│  ┌────────▼─────────────────────────────────────────▼──────────┐ │
│  │              SQLAlchemy ORM (async + asyncpg)               │ │
│  └─────────────────────────┬───────────────────────────────────┘ │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                   Supabase Postgres                              │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐              │
│  │  users   │  │ meetings │  │ meeting_attendees │              │
│  └──────────┘  └──────────┘  └──────────────────┘              │
│  ┌──────────────┐  ┌──────────────┐                            │
│  │ chat_threads │  │ chat_messages│                             │
│  └──────────────┘  └──────────────┘                            │
└──────────────────────────────────────────────────────────────────┘
```

## Database Schema

### Entity-Relationship Diagram

```
users (1) ─────────── (N) meetings         [created_by]
users (N) ─── meeting_attendees ─── (N) meetings  [many-to-many]
users (1) ─────────── (N) chat_threads     [user_id]
meetings (1) ────────── (N) chat_threads   [meeting_id]
chat_threads (1) ─────── (N) chat_messages [thread_id]
```

### Per-User Isolation Model

Each user gets their own `chat_thread` per meeting (enforced by a UNIQUE constraint
on `(user_id, meeting_id)`). Chat messages belong to threads, so User A's
conversation about a meeting is completely isolated from User B's — no leakage
of action items, notes, or assistant responses between attendees.

## Meeting Ingestion Flow

### Teams Live Path (Phase 1)
```
User creates meeting → Sets Teams join URL → Bot joins call via Graph API
→ Call ends → Post-call transcript retrieved → raw_transcript stored
→ status='processing' → [Day 2 pipeline picks up here]
```

### Meet Export Path (Phase 1)
```
User exports transcript from Google Drive → Pastes text into UI
→ raw_transcript stored → status='processing'
→ [Day 2 pipeline picks up here]
```

## Auth Flow

```
Register/Login → JWT access token (30 min) + refresh token (7 days)
→ All protected endpoints require Bearer token
→ get_current_user dependency is the ONLY trust source for identity
→ Refresh endpoint exchanges refresh token for new access token
```

## Degraded Mode

When Azure AD credentials are not configured:
- Backend starts normally with a clear warning log
- Teams live-join endpoints return HTTP 503 with explanatory message
- All other functionality (auth, Meet import, etc.) works normally
- Health endpoint reports `azure_configured: false`
