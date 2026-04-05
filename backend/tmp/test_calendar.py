import os
import sys
import datetime
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

from calendar_api import CalComCalendar, SlotUnavailableError, AvailableSlot

load_dotenv()


async def test_list_available_slots():
    """Test that list_available_slots returns correctly structured slots."""
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
        end_time = start_time + datetime.timedelta(days=7)

        slots = await cal.list_available_slots(start_time=start_time, end_time=end_time)

        if not slots:
            print("WARN: No available slots found in the next 7 days")
            return True

        for slot in slots:
            if not isinstance(slot, AvailableSlot):
                print(f"FAIL: Slot is not an AvailableSlot instance: {type(slot)}")
                return False
            if not isinstance(slot.start_time, datetime.datetime):
                print(f"FAIL: start_time is not a datetime: {type(slot.start_time)}")
                return False
            if slot.duration_min != 30:
                print(f"FAIL: duration_min should be 30, got {slot.duration_min}")
                return False

        print(f"PASS: list_available_slots returned {len(slots)} valid slots")
        return True, slots

    except Exception as e:
        print(f"FAIL: list_available_slots raised exception: {e}")
        return False


async def test_schedule_appointment():
    """Test that schedule_appointment books a slot successfully."""
    api_key = os.getenv("CALCOM_API_KEY")
    if not api_key:
        api_key = os.getenv("CAL_API_KEY")
    if not api_key:
        print("FAIL: CALCOM_API_KEY not found in environment")
        return False, None

    try:
        cal = CalComCalendar(api_key=api_key, timezone="Asia/Kolkata")
        await cal.initialize()

        start_time = datetime.datetime.now(datetime.timezone.utc)
        end_time = start_time + datetime.timedelta(days=7)
        slots = await cal.list_available_slots(start_time=start_time, end_time=end_time)

        if not slots:
            print("FAIL: No available slots to book")
            return False, None

        slot_to_book = slots[0]
        print(
            f"  DEBUG: Attempting to book slot at {slot_to_book.start_time.isoformat()}"
        )

        await cal.schedule_appointment(
            start_time=slot_to_book.start_time,
            attendee_name="Test User",
            phone_number="+911234567890",
        )

        print(
            f"PASS: schedule_appointment booked slot at {slot_to_book.start_time.isoformat()}"
        )
        return True, slot_to_book

    except Exception as e:
        print(f"FAIL: schedule_appointment raised exception: {e}")
        import traceback

        traceback.print_exc()
        return False, None


async def test_double_booking_prevention():
    """Test that booking the same slot twice raises SlotUnavailableError."""
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
        end_time = start_time + datetime.timedelta(days=7)
        slots = await cal.list_available_slots(start_time=start_time, end_time=end_time)

        if not slots:
            print("WARN: No available slots to test double-booking")
            return True

        slot_to_book = slots[0]

        await cal.schedule_appointment(
            start_time=slot_to_book.start_time,
            attendee_name="Test User",
            phone_number="1234567890",
        )

        try:
            await cal.schedule_appointment(
                start_time=slot_to_book.start_time,
                attendee_name="Another User",
                phone_number="0987654321",
            )
            print("FAIL: Double booking did not raise SlotUnavailableError")
            return False
        except SlotUnavailableError:
            print("PASS: SlotUnavailableError raised on double booking attempt")
            return True

    except Exception as e:
        print(f"FAIL: test_double_booking_prevention raised exception: {e}")
        return False


async def test_booking_nonexistent_slot():
    """Test that booking a non-existent slot raises SlotUnavailableError."""
    api_key = os.getenv("CALCOM_API_KEY")
    if not api_key:
        api_key = os.getenv("CAL_API_KEY")
    if not api_key:
        print("FAIL: CALCOM_API_KEY not found in environment")
        return False

    try:
        cal = CalComCalendar(api_key=api_key, timezone="Asia/Kolkata")
        await cal.initialize()

        past_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=30
        )

        try:
            await cal.schedule_appointment(
                start_time=past_time,
                attendee_name="Test User",
                phone_number="1234567890",
            )
            print("FAIL: Booking past slot did not raise an error")
            return False
        except (SlotUnavailableError, Exception) as e:
            if isinstance(e, SlotUnavailableError):
                print("PASS: SlotUnavailableError raised for non-existent slot")
                return True
            else:
                print(f"PASS: Error raised for non-existent slot: {type(e).__name__}")
                return True

    except Exception as e:
        print(f"FAIL: test_booking_nonexistent_slot raised unexpected exception: {e}")
        return False


async def cleanup_test_bookings():
    """Attempt to cancel any bookings made during tests."""
    print("\n--- Attempting cleanup ---")
    api_key = os.getenv("CALCOM_API_KEY")
    if not api_key:
        api_key = os.getenv("CAL_API_KEY")
    if not api_key:
        print("Skipping cleanup: CALCOM_API_KEY not found")
        return

    try:
        import aiohttp
        from calendar_api import BASE_URL

        headers = {
            "Authorization": f"Bearer {api_key}",
            "cal-api-version": "2026-02-25",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url=f"{BASE_URL}bookings",
                headers=headers,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    bookings = data.get("data", [])
                    cancelled = 0
                    for booking in bookings:
                        uid = booking.get("uid")
                        if uid:
                            async with session.delete(
                                url=f"{BASE_URL}bookings/{uid}",
                                headers=headers,
                            ) as del_resp:
                                if del_resp.status in (200, 204):
                                    cancelled += 1
                    print(f"Cleanup complete: cancelled {cancelled} booking(s)")
                else:
                    print(
                        f"Cleanup skipped: could not fetch bookings (status {resp.status})"
                    )
    except Exception as e:
        print(f"Cleanup skipped: {e}")


async def run_all_tests():
    """Run all calendar API tests."""
    print("=" * 50)
    print("CALENDAR API TEST SUITE")
    print("=" * 50)

    results = {}

    print("\n--- Test 1: list_available_slots ---")
    result1 = await test_list_available_slots()
    results["list_available_slots"] = result1

    print("\n--- Test 2: schedule_appointment ---")
    result2, booked_slot = await test_schedule_appointment()
    results["schedule_appointment"] = result2

    print("\n--- Test 3: double_booking_prevention ---")
    results["double_booking_prevention"] = await test_double_booking_prevention()

    print("\n--- Test 4: booking_nonexistent_slot ---")
    results["booking_nonexistent_slot"] = await test_booking_nonexistent_slot()

    await cleanup_test_bookings()

    print("\n" + "=" * 50)
    print("TEST SUMMARY")
    print("=" * 50)

    def is_pass(result):
        if isinstance(result, bool):
            return result is True
        if isinstance(result, tuple) and len(result) > 0:
            return result[0] is True
        return False

    passed = sum(1 for v in results.values() if is_pass(v))
    total = len(results)
    print(f"Passed: {passed}/{total}")
    for test_name, result in results.items():
        if is_pass(result):
            status = "PASS"
        elif isinstance(result, bool):
            status = "FAIL"
        else:
            status = "SKIP"
        print(f"  [{status}] {test_name}")

    return passed == total


if __name__ == "__main__":
    import asyncio

    success = asyncio.run(run_all_tests())
    exit(0 if success else 1)
