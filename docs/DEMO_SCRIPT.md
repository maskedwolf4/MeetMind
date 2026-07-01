# MeetMind — 3-Minute Demo Script

## Setup (before the demo)
1. Run: python scripts/seed_demo.py
2. Open two browser windows (or one normal + one incognito)
3. In Window 1: login as alan@demo.com / DemoAlan2024
4. In Window 2: login as bob@demo.com / DemoBob2024

## The Demo (3 minutes)

### Minute 1 — Show the acquisition framework (30 seconds)
Point to the "New Meeting" modal and open it. Show all three tabs briefly:
"Teams Live join, transcript import for Teams or Meet exports, and a
manual mode for demos like this one." You do not need to demonstrate a
live join — explaining the architecture is enough. Close the modal.

### Minute 1:30 — Chat as Alan (60 seconds)
In Window 1 (Alan), click "Q3 Product Planning" in the sidebar.
The chat opens. Point out the 4 suggestion chips.
Click "What are my action items?" — show the streaming response arrive
token by token. The answer should reference:
  - Complete the auth refactor by July 31st
  - Write the migration guide for the team
Ask a follow-up: "When is my auth refactor due?" — show it answers
from context without hallucinating.

### Minute 2:30 — Same question as Bob (30 seconds)
Switch to Window 2 (Bob). Click the same "Q3 Product Planning" meeting.
Click "What are my action items?" (or type it).
The answer should reference:
  - Update the project board today and notify the mobile team
  - Review Alan's migration guide within 48 hours
  - Organise the Q3 retrospective and send calendar invites
Point out: same meeting, same question, completely different personalised
answers. This is Cognee's per-user isolation at work.

### The Architecture Point (key judging moment)
"The isolation isn't a prompt instruction telling the AI 'only answer
about Bob' — that would be fragile and prompt-injectable. Instead, every
Cognee search call is scoped by two parameters: the user's ID from their
authenticated JWT, and the meeting ID. Bob's query physically cannot
retrieve Alan's personal extract because it was stored under a different
dataset namespace. Postgres holds the chat history; Cognee Cloud holds
the semantic meeting memory."
