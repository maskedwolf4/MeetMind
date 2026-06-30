# Google Meet Transcript Ingestion Guide

## Overview

MeetMind supports importing Google Meet transcripts through an **export-based** approach. This is **not** a real-time, live-bot-join integration — the meeting must have already ended, and the transcript must be manually exported and pasted into MeetMind.

## How It Works

1. A Google Meet meeting occurs with **transcription enabled**
2. After the meeting ends, the transcript is saved to the host's Google Drive
3. The user exports/copies the transcript text
4. The user pastes the transcript into MeetMind's "Import a Meet transcript" form
5. MeetMind stores the raw transcript and begins processing (Day 2 pipeline)

## Prerequisites

### Workspace Admin Must Enable Transcription

Google Meet transcription is a **Google Workspace** feature (not available on free personal Google accounts). The Workspace admin must enable it:

1. Go to [Google Admin Console](https://admin.google.com) → **Apps** → **Google Workspace** → **Google Meet**
2. Under **Meet video settings**, find **Recording and transcription**
3. Enable **"Transcription"** (or "Allow users to transcribe meetings")
4. Save changes (may take up to 24 hours to propagate)

### Meeting Host Must Start Transcription

During the meeting:
1. The host clicks the **Activities** icon (bottom right) in Google Meet
2. Select **Transcription** → **Start transcription**
3. All participants see a notice that the meeting is being transcribed

> **Important:** If the host forgets to start transcription, there will be no transcript to export.

## Finding Your Transcript After the Meeting

After the meeting ends, the transcript is automatically saved to the **meeting organizer's** Google Drive:

### Method 1: Google Drive Direct

1. Open [Google Drive](https://drive.google.com)
2. Look in the root folder or the **"Meet Recordings"** folder
3. The transcript file is named: `[Meeting Title] - Transcript [Date].docx`
4. Open the file and copy all the text

### Method 2: Google Calendar

1. Open [Google Calendar](https://calendar.google.com)
2. Find the meeting event
3. Click on it — you'll see a link to the transcript in the event details
4. Click the link to open in Google Drive
5. Open the file and copy all the text

### Method 3: Gmail

1. After the meeting ends, the organizer receives an email with links to:
   - The recording (if recording was enabled)
   - The transcript file in Google Drive
2. Click the transcript link
3. Open the file and copy all the text

## Transcript Format

Google Meet transcripts are formatted as a conversation log:

```
Meeting Title - Transcript
Date: June 30, 2026

John Smith (00:00:05)
Good morning everyone. Let's get started with the weekly standup.

Jane Doe (00:00:12)
Sure. I completed the API integration yesterday and started on the frontend changes.

John Smith (00:00:25)
Great. Any blockers?

Jane Doe (00:00:28)
Not at the moment. I should have the PR ready by end of day.

Bob Johnson (00:00:35)
I'm still working on the database migration. Ran into an issue with the foreign key constraints...
```

Copy **all** of this text (including timestamps and speaker names) and paste it into MeetMind.

## Using MeetMind's Import Feature

### Via the Web UI

1. Log in to MeetMind
2. On the dashboard, click **"Import a Meet transcript"**
3. A text area appears — paste the entire transcript text
4. Click **"Import"**
5. The meeting status changes to `processing` — the Day 2 pipeline will handle the rest

### Via the API

```bash
# 1. First, create a draft meeting
curl -X POST http://localhost:8000/meetings \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "Weekly Standup", "meeting_datetime": "2026-06-30T10:00:00Z"}'

# Response includes the meeting ID
# {"id": "abc-123-...", "status": "draft", ...}

# 2. Import the transcript
curl -X POST http://localhost:8000/meetings/abc-123-.../meet/import \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"transcript_text": "John Smith (00:00:05)\nGood morning everyone..."}'

# Response: {"status": "processing", "source": "meet_export", ...}
```

## Limitations

### This Is NOT Real-Time
- The transcript is only available **after** the meeting ends
- There is no live streaming of the transcript to MeetMind
- The user must manually copy and paste the transcript

### Requires Google Workspace
- Free personal Google accounts do **not** have transcription
- The feature requires Google Workspace Business Standard, Business Plus, Enterprise Standard, Enterprise Plus, Education Plus, or Teaching and Learning Upgrade

### Only the Organizer Gets the File
- The transcript file is saved to the **meeting organizer's** Google Drive
- Other attendees must ask the organizer to share the transcript
- Or the organizer can import it into MeetMind, and then add those attendees to the MeetMind meeting

### Language Support
- Google Meet transcription supports English and several other languages
- Accuracy depends on audio quality, speaker clarity, and number of simultaneous speakers
- Background noise, accents, and cross-talk reduce accuracy

### No Automatic Sync
- If you need to re-export a transcript (e.g., because Google updated it), you must manually re-import it
- There is no Google Drive API integration to auto-pull transcripts (this could be added in a future phase)

## Why Not a Live Bot-Join for Google Meet?

Google Meet does not provide an official API for bots to join meetings the way Microsoft Teams does. The alternatives are:

1. **Chrome extension**: Requires the user to install an extension and keep Chrome open — fragile and not scalable
2. **Headless browser (Puppeteer/Playwright)**: A server-side browser joins the meeting — technically possible but:
   - Violates Google's Terms of Service
   - Requires solving CAPTCHAs, authentication flows, and UI changes
   - Google actively detects and blocks automated joining
   - High operational cost (each meeting needs a running browser instance)

For these reasons, the **export-based approach** is the primary (and only recommended) path for Google Meet in MeetMind. It is reliable, requires no special infrastructure, and respects Google's platform policies.

> **Optional Stretch:** A headless-browser bot-join module may be developed experimentally in a separate, isolated module. If attempted, it is clearly labeled as experimental/risky and its absence or incompleteness does not affect any other functionality. See code comments in any such module for details.
