import os
import sys
import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

from calendar_api import CalComCalendar

load_dotenv()


async def test_list_events():
    """Test that list_bookings returns all events/bookings."""
    api_key = os.getenv("CALCOM_API_KEY")
    if not api_key:
        api_key = os.getenv("CAL_API_KEY")
    if not api_key:
        print("FAIL: CALCOM_API_KEY not found in environment")
        return False

    try:
        cal = CalComCalendar(api_key=api_key, timezone="Asia/Kolkata")
        await cal.initialize()

        start_time = datetime.datetime.now(datetime.timezone.utc)
        end_time = start_time + datetime.timedelta(days=30)

        events = await cal.list_bookings(start_time=start_time, end_time=end_time)

        print(f"Found {len(events)} events:")
        for event in events:
            print(
                f"  - {event.get('title', 'No title')} | Start: {event.get('start', 'N/A')} | Status: {event.get('status', 'N/A')}"
            )

        if events:
            print("PASS: list_bookings returned events successfully")
            return True
        else:
            print("INFO: No events found in the date range")
            return True

    except Exception as e:
        print(f"FAIL: list_bookings raised exception: {e}")
        import traceback

        traceback.print_exc()
        return False


async def test_create_booking():
    """Test that schedule_appointment creates a booking."""
    api_key = os.getenv("CALCOM_API_KEY")
    if not api_key:
        api_key = os.getenv("CAL_API_KEY")
    if not api_key:
        print("FAIL: CALCOM_API_KEY not found in environment")
        return False

    try:
        cal = CalComCalendar(api_key=api_key, timezone="Asia/Kolkata")
        await cal.initialize()

        now = datetime.datetime.now(ZoneInfo("Asia/Kolkata"))
        end_time = now + datetime.timedelta(days=14)

        slots = await cal.list_available_slots(start_time=now, end_time=end_time)

        if not slots:
            print("INFO: No available slots found")
            return True

        slot = slots[0]
        test_name = "Test User"
        test_phone = "9876543210"

        result = await cal.schedule_appointment(
            start_time=slot.start_time,
            attendee_name=test_name,
            phone_number=test_phone,
        )

        print(f"Booking result: {result}")

        start_check = now - datetime.timedelta(days=1)
        end_check = end_time + datetime.timedelta(days=1)
        bookings = await cal.list_bookings(start_time=start_check, end_time=end_check)

        created = any(
            any(
                att.get("phoneNumber") == f"+91{test_phone}"
                for att in b.get("attendees", [])
            )
            for b in bookings
        )

        if created:
            print("PASS: schedule_appointment created booking successfully")
            return True
        else:
            print("FAIL: schedule_appointment did not create expected booking")
            return False

    except Exception as e:
        print(f"FAIL: schedule_appointment raised exception: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    import asyncio

    success = asyncio.run(test_create_booking())
    exit(0 if success else 1)
