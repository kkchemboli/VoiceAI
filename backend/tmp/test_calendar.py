import os
import sys
import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))


async def test_get_event_config():
    """Test that get event type configuration from Cal.com."""
    api_key = os.getenv("CALCOM_API_KEY")
    if not api_key:
        api_key = os.getenv("CAL_API_KEY")
    if not api_key:
        print("FAIL: CALCOM_API_KEY not found in environment")
        return False

    try:
        cal = CalComCalendar(api_key=api_key, timezone="Asia/Kolkata")
        await cal.initialize()

        event_type_id = 5157717
        cal._lk_event_id = event_type_id # Override for testing
        print(f"Event Type ID: {event_type_id}")

        async with cal._http_session.get(
            headers=cal._build_headers(api_version="2024-06-14"),
            url=f"{BASE_URL}event-types/{event_type_id}",
        ) as resp:
            resp.raise_for_status()
            data = (await resp.json())["data"]

        print("\nEvent Type Configuration:")
        print(f"  ID: {data.get('id')}")
        print(f"  Title: {data.get('title')}")
        print(f"  Slug: {data.get('slug')}")
        print(f"  Duration: {data.get('lengthInMinutes')} minutes")
        print(f"  Start time: {data.get('startTime')}")
        print(f"  Before event buffer: {data.get('beforeEventBuffer')} minutes")
        print(f"  After event buffer: {data.get('afterEventBuffer')} minutes")
        print(f"  Booking limits: {data.get('bookingLimits')}")
        print(f"  Recurring event: {data.get('recurringEvent')}")

        print(f"  Schedule ID: {data.get('scheduleId')}")
        print(f"  Slot interval: {data.get('slotInterval')}")
        print(f"  Minimum booking notice: {data.get('minimumBookingNotice')} minutes")

        schedule_id = data.get("scheduleId")

        async with cal._http_session.get(
            headers=cal._build_headers(api_version="2024-06-14"),
            url=f"{BASE_URL}schedules",
        ) as resp:
            resp.raise_for_status()
            schedules_data = (await resp.json())["data"]

        print(f"\nUser Schedules ({len(schedules_data)} total):")
        for sched in schedules_data:
            print(f"\n  Schedule: {sched.get('name')}")
            print(f"    ID: {sched.get('id')}")
            print(f"    Timezone: {sched.get('timeZone')}")
            print(f"    Raw keys: {list(sched.keys())}")
            schedule_object = sched.get("scheduleObject", {})
            print(f"    Schedule Object: {schedule_object}")
            if schedule_object:
                for day, times in schedule_object.items():
                    if times:
                        print(f"    {day}: {times}")
            if "availability" in sched:
                print(f"    Availability: {sched.get('availability')}")

        print(f"\n  Raw data keys: {list(data.keys())}")

        print("\nFull response (filtered):")
        for key, value in data.items():
            if value is not None and value != [] and value != {}:
                print(f"  {key}: {value}")

        print("\nPASS: Retrieved event config successfully")
        return True

    except Exception as e:
        print(f"FAIL: test_get_event_config raised exception: {e}")
        import traceback

        traceback.print_exc()
        return False


from calendar_api import CalComCalendar, BASE_URL

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


async def test_list_available_slots():
    """Test that list_available_slots returns available appointment slots."""
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

        print(f"Found {len(slots)} available slots:")
        for slot in slots:
            local = slot.start_time.astimezone(ZoneInfo("Asia/Kolkata"))
            print(
                f"  - {slot.unique_hash} | {local.strftime('%Y-%m-%d %I:%M %p')} | {slot.duration_min} min"
            )

        if slots:
            print("PASS: list_available_slots returned slots successfully")
            return True
        else:
            print("INFO: No available slots found in the date range")
            return True

    except Exception as e:
        print(f"FAIL: list_available_slots raised exception: {e}")
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


async def test_check_april_13_bookings():
    """Check bookings on April 13, 2026."""
    api_key = os.getenv("CALCOM_API_KEY")
    if not api_key:
        api_key = os.getenv("CAL_API_KEY")
    if not api_key:
        print("FAIL: CALCOM_API_KEY not found in environment")
        return False

    try:
        cal = CalComCalendar(api_key=api_key, timezone="Asia/Kolkata")
        await cal.initialize()
        cal._lk_event_id = 5157717 # Override for testing

        start_time = datetime.datetime(
            2026, 4, 13, 0, 0, 0, tzinfo=datetime.timezone.utc
        )
        end_time = datetime.datetime(2026, 4, 14, 0, 0, 0, tzinfo=datetime.timezone.utc)

        # Query for all bookings, not just accepted ones
        query = urlencode(
            {
                "eventTypeId": cal._lk_event_id,
                "afterStart": start_time.isoformat(),
                "beforeEnd": end_time.isoformat(),
            }
        )
        async with cal._http_session.get(
            headers=cal._build_headers(api_version="2026-02-25"),
            url=f"{BASE_URL}bookings/?{query}",
        ) as resp:
            resp.raise_for_status()
            raw_data = (await resp.json())["data"]
        
        bookings = raw_data

        print(f"Found {len(bookings)} bookings on April 13, 2026:")
        for b in bookings:
            local_start = datetime.datetime.fromisoformat(
                b["start"].replace("Z", "+00:00")
            ).astimezone(ZoneInfo("Asia/Kolkata"))
            attendees = b.get("attendees", [])
            attendee_names = [a.get("name") for a in attendees]
            print(
                f"  - {b.get('title')} | Start: {local_start.strftime('%Y-%m-%d %I:%M %p')} | Attendees: {attendee_names}"
            )

        if bookings:
            print(f"\nINFO: Found {len(bookings)} bookings on April 13")
        else:
            print("\nINFO: No bookings found on April 13")

        print("\n--- Checking slots for a wider range ---")

        # Query from April 11 (today) to April 30
        start_range = datetime.datetime(
            2026, 4, 11, 0, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata")
        )
        end_range = datetime.datetime(
            2026, 4, 30, 23, 59, 59, tzinfo=ZoneInfo("Asia/Kolkata")
        )

        slots = await cal.list_available_slots(
            start_time=start_range, end_time=end_range
        )

        # Group slots by day
        slots_by_day = {}
        for slot in slots:
            local = slot.start_time.astimezone(ZoneInfo("Asia/Kolkata"))
            day = local.strftime("%Y-%m-%d (%A)")
            if day not in slots_by_day:
                slots_by_day[day] = []
            slots_by_day[day].append(local.strftime("%I:%M %p"))

        print(f"\nSlots by day (total {len(slots)} slots):")
        for day, times in sorted(slots_by_day.items()):
            print(f"  {day}: {len(times)} slots")

        print(
            "\n--- Debug: Check what the current time is and what's the earliest available slot ---"
        )
        now = datetime.datetime.now(ZoneInfo("Asia/Kolkata"))
        print(f"Current time: {now.strftime('%Y-%m-%d %I:%M %p')}")
        if slots:
            earliest = slots[0].start_time.astimezone(ZoneInfo("Asia/Kolkata"))
            print(f"Earliest slot: {earliest.strftime('%Y-%m-%d %I:%M %p')}")

        print("\n--- Check April 13 specifically with wider window ---")
        # April 13 00:00 to April 14 00:00 IST (full day)
        start_apr13 = datetime.datetime(
            2026, 4, 13, 0, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata")
        )
        end_apr13 = datetime.datetime(
            2026, 4, 14, 0, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata")
        )

        # But also start earlier to include the previous day's late evening
        start_wider = datetime.datetime(
            2026, 4, 12, 18, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata")
        )

        print(
            f"Wider query: {start_wider.strftime('%Y-%m-%d %I:%M %p')} to {end_apr13.strftime('%Y-%m-%d %I:%M %p')}"
        )

        slots_wide = await cal.list_available_slots(
            start_time=start_wider, end_time=end_apr13
        )
        print(f"Slots found: {len(slots_wide)}")

        for slot in slots_wide:
            local = slot.start_time.astimezone(ZoneInfo("Asia/Kolkata"))
            print(f"  {local.strftime('%Y-%m-%d %I:%M %p')}")

        print("\nPASS: April 13 slot check completed")
        return True

    except Exception as e:
        print(f"FAIL: test_check_april_13_bookings raised exception: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    import asyncio

    success = asyncio.run(test_get_event_config())
    exit(0 if success else 1)
