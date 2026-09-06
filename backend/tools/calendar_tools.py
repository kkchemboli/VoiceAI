import datetime
import logging
from collections import defaultdict
from livekit.agents import llm
from utils.formatting import _format_date_human
from utils.normalizers import _normalize_slot_key

logger = logging.getLogger("voice-agent")


def create_calendar_tools(
    cal,
    tz_info,
    slots_map: dict,
    slots_normalized: dict,
    booking_info: dict,
    agent_state=None,
):
    """Factory function creating scoped LLM function tools for calendar slot query and booking."""

    @llm.function_tool(
        description="Get ALL available appointment slots for Free Demo Classes. Returns the complete list of slots grouped by day with available times. The return is formatted for speech: times on the same day are separated by commas, and each day ends with a full stop (period) so the voice pauses between dates. CRITICAL: Always call this first and present the ENTIRE list to the user (day, date, time only, no IDs) whenever they want to book a Free Demo Class, reading it exactly as returned with commas between times and full stops between dates (never use bullets, dashes, or markdown). After showing the list, ask the user which slot they prefer and WAIT for their explicit choice. Do NOT collect name, phone number, or book anything until the user has selected a specific slot."
    )
    async def list_available_slots():
        if agent_state and hasattr(agent_state, "update_booking_stage"):
            agent_state.update_booking_stage("SLOT_SELECTION")

        now = datetime.datetime.now(tz_info)
        range_days = 7
        tomorrow = now + datetime.timedelta(days=1)
        start_time = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
        slots = await cal.list_available_slots(
            start_time=start_time,
            end_time=start_time + datetime.timedelta(days=range_days),
        )

        if not slots:
            return "No slots available at the moment."

        day_map = defaultdict(list)
        for slot in slots:
            local = slot.start_time.astimezone(tz_info)
            day_key = local.date()
            time_str = local.strftime("%I:%M %p")
            day_map[day_key].append((slot, time_str))

        parts = []
        for day in sorted(day_map.keys()):
            times = sorted(day_map[day], key=lambda item: item[0].start_time)
            label = day.strftime("%A, %d %B %Y")
            time_list = ", ".join(time_str for _, time_str in times)
            parts.append(f"{label}: {time_list}.")
            for slot, time_str in times:
                key = f"{label} at {time_str}"
                slots_map[key] = slot
                slots_normalized[_normalize_slot_key(key)] = slot

        return " ".join(parts)

    @llm.function_tool(
        description="Schedule a Free Demo Class appointment. CRITICAL: Call this ONLY after the user has explicitly chosen a specific slot from the `list_available_slots` output. Never book a slot the user did not choose, never pick/default/guess a slot, and never call this before the user has selected a slot. `selected_slot` must be the exact date-and-time string the user chose from `list_available_slots`. Requires the user's confirmed name and phone number."
    )
    async def schedule_demo_class(
        selected_slot: str,
        phone_number: str,
        name: str,
    ):
        if not name or not name.strip():
            return (
                "Error: The user's name is missing. The booking was NOT created. "
                "Ask the user for their full name first, then call schedule_demo_class again."
            )
        if not phone_number or not phone_number.strip():
            return (
                "Error: The user's phone number is missing. The booking was NOT created. "
                "Ask the user for their phone number first, then call schedule_demo_class again."
            )

        slot = slots_map.get(selected_slot)
        if not slot:
            slot = slots_normalized.get(_normalize_slot_key(selected_slot))
        if not slot:
            return (
                f"Error: Slot '{selected_slot}' was not found among the available slots. "
                "This does not mean the slot is unavailable. Call list_available_slots to get the "
                "current available slots, then ask the user to choose from the exact options listed, "
                "and pass the chosen option to schedule_demo_class exactly as returned."
            )

        try:
            result = await cal.schedule_appointment(
                start_time=slot.start_time,
                attendee_name=name,
                phone_number=phone_number,
            )
            if result.startswith("Error"):
                return result
        except Exception as e:
            logger.error(f"Unexpected error in schedule_demo_class: {e}")
            return f"Error: An unexpected error occurred while booking. ({str(e)})"

        local = slot.start_time.astimezone(tz_info)
        now = datetime.datetime.now(tz_info)

        booking_info["booked"] = True
        booking_info["name"] = name
        booking_info["phone"] = phone_number
        booking_info["date"] = _format_date_human(local, now)
        booking_info["time"] = local.strftime("%I:%M %p")

        if agent_state:
            agent_state.caller_name = name
            agent_state.name_confirmed = True
            agent_state.phone_number = phone_number
            agent_state.phone_confirmed = True
            agent_state.selected_slot = selected_slot
            if hasattr(agent_state, "update_booking_stage"):
                agent_state.update_booking_stage("COMPLETED")

        return f"Success: The appointment was scheduled for {_format_date_human(local, now)}."

    return [list_available_slots, schedule_demo_class]

