# MeetMind

# Phase 1
Build two things for "MeetMind" — an AI meeting assistant that summarizes
meeting transcripts and lets each attendee chat with a personalized, isolated
view of that meeting (no leakage of one attendee's action items to another):

PART A — the foundation: Supabase Postgres schema, FastAPI backend scaffold
with JWT auth, Next.js frontend scaffold with auth pages.

PART B — the meeting-acquisition framework: a Microsoft Teams bot that can
join a live Teams meeting via Microsoft Graph / Bot Framework and produce a
transcript, plus a Google Meet ingestion path based on transcript export
(not live bot-join, which is explicitly out of scope for the primary path —
see RULES for the optional stretch addition).

# Phase 2
Build the memory and ingestion layer of "MeetMind" on top of the Day 1
foundation (Supabase Postgres schema + FastAPI auth already exist and must
not be broken or redesigned). This phase adds: Cognee memory layer
integration with verified per-user isolation, meeting CRUD endpoints, a
LangGraph ingestion pipeline that turns a raw transcript into a shared
meeting summary plus per-attendee personalized extracts, and Groq as the LLM
provider for all extraction steps. Do NOT build the chat endpoint, streaming,
or any frontend chat UI in this phase — that is Day 3.

# Phase 3
Complete "MeetMind" by building the chat layer on top of Day 1 (auth +
schema) and Day 2 (Cognee isolation + ingestion pipeline) — both already
exist and must not be redesigned or broken. This phase adds: the
streaming chat endpoint, conversation history persistence in Postgres, the
full Next.js dashboard and chat UI, and final demo-readiness polish
including a seeded demo dataset.