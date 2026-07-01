"""
MeetMind Demo Seed Script — creates demo users, meeting, and runs ingestion.

Usage: python scripts/seed_demo.py
Idempotent: running twice does not create duplicates.
"""

import asyncio
import sys
import os

# Add backend dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

DEMO_TRANSCRIPT = """Alan: Good afternoon everyone. Let's get started on Q3 planning.
Bob: Sounds good. I've reviewed the roadmap and have some concerns.
Alan: Let's hear them.
Bob: The mobile feature timeline looks aggressive. We need two more weeks.
Alan: Agreed. Let's extend mobile delivery to end of August.
Bob: I'll update the project board today and notify the mobile team.
Alan: Perfect. On the API side, I need to complete the auth refactor by July 31st and write the migration guide for the team.
Bob: I'll review Alan's migration guide once it's ready and give feedback within 48 hours.
Alan: Great. Final item — we need a Q3 retrospective scheduled.
Bob: I can organise that. I'll send calendar invites to the full team by end of this week.
Alan: Perfect. That covers everything. Thanks Bob.
Bob: Thanks Alan, talk soon."""


async def main():
    # Import app modules (needs .env loaded)
    from dotenv import load_dotenv
    load_dotenv()

    from app.core.db import async_session_factory
    from app.core.security import hash_password
    from app.models.user import User
    from app.models.meeting import Meeting, MeetingAttendee
    from app.services.ingestion_graph import run_ingestion_pipeline

    from sqlalchemy import select

    print("=" * 60)
    print("  MeetMind Demo Seed Script")
    print("=" * 60)

    async with async_session_factory() as db:
        # 1. Create demo users
        alan = await _get_or_create_user(
            db, "Alan", "alan@demo.com", "DemoAlan2024"
        )
        bob = await _get_or_create_user(
            db, "Bob", "bob@demo.com", "DemoBob2024"
        )
        await db.commit()

        # 2. Check if demo meeting exists
        result = await db.execute(
            select(Meeting).where(
                Meeting.title == "Q3 Product Planning",
                Meeting.created_by == alan.id,
            )
        )
        meeting = result.scalar_one_or_none()

        if meeting:
            print(f"  ✅ Demo meeting already exists: {meeting.id}")
            if meeting.status == "ready":
                print("  ✅ Meeting already processed — skipping ingestion")
                print("\n✅ Seed complete (idempotent — no duplicates)")
                return
        else:
            # Create meeting
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            yesterday_2pm = yesterday.replace(hour=14, minute=0, second=0, microsecond=0)

            meeting = Meeting(
                title="Q3 Product Planning",
                meeting_datetime=yesterday_2pm,
                created_by=alan.id,
                source="manual",
                raw_transcript=DEMO_TRANSCRIPT,
                status="processing",
            )
            db.add(meeting)
            await db.flush()
            await db.refresh(meeting)
            print(f"  ✅ Created demo meeting: {meeting.id}")

            # Add attendees
            for user in [alan, bob]:
                existing = await db.execute(
                    select(MeetingAttendee).where(
                        MeetingAttendee.meeting_id == meeting.id,
                        MeetingAttendee.user_id == user.id,
                    )
                )
                if not existing.scalar_one_or_none():
                    db.add(MeetingAttendee(meeting_id=meeting.id, user_id=user.id))
            await db.flush()
            print("  ✅ Added attendees: Alan, Bob")
            await db.commit()

        # 3. Run ingestion pipeline
        print("\n  🔄 Running ingestion pipeline...")
        registered = {str(alan.id): "Alan", str(bob.id): "Bob"}

        async with async_session_factory() as db2:
            state = await run_ingestion_pipeline(
                meeting_id=str(meeting.id),
                meeting_title=meeting.title,
                raw_transcript=DEMO_TRANSCRIPT,
                registered_attendees=registered,
                db=db2,
            )
            await db2.commit()

        print(f"  Pipeline status: {state.final_status}")
        print(f"  Global summary: {len(state.global_summary.get('summary', ''))} chars")
        print(f"  Decisions: {state.global_summary.get('decisions', [])}")
        print(f"  Person extracts: {len(state.person_extracts)} users")

        for uid, extract in state.person_extracts.items():
            name = extract.get("user_name", "Unknown")
            actions = extract.get("action_items", [])
            print(f"    {name}: {len(actions)} action items")
            for a in actions:
                print(f"      - {a}")

        if state.final_status == "ready":
            print("\n✅ ✅ ✅  Seed complete — demo ready!")
        else:
            print(f"\n❌ Pipeline finished with status: {state.final_status}")
            if state.error_message:
                print(f"   Error: {state.error_message}")
            sys.exit(1)


async def _get_or_create_user(db, name, email, password):
    from app.models.user import User
    from app.core.security import hash_password
    from sqlalchemy import select

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user:
        print(f"  ✅ User {email} already exists")
        return user

    user = User(name=name, email=email, password_hash=hash_password(password))
    db.add(user)
    await db.flush()
    await db.refresh(user)
    print(f"  ✅ Created user: {email}")
    return user


if __name__ == "__main__":
    asyncio.run(main())
