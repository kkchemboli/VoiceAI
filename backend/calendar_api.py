from __future__ import annotations

import base64
import datetime
import hashlib
import logging
import random
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import os

import aiohttp

from livekit.agents.utils import http_context


def _normalize_phone_number(phone_number: str) -> str:
    """
    Strictly normalizes to E.164. Ensuring the '+' prefix is always present.
    """
    phone = str(phone_number).strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        return phone
    # If it starts with 91 but no +, add +
    if phone.startswith("91") and len(phone) >= 12:
        return "+" + phone
    # Fallback: assume India if 10 digits
    if len(phone) == 10:
        return "+91" + phone
    # Last resort: just add + if missing
    return "+" + phone if phone else ""


class SlotUnavailableError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass
class AvailableSlot:
    start_time: datetime.datetime
    duration_min: int

    @property
    def unique_hash(self) -> str:
        # unique id based on the start_time & duration_min
        raw = f"{self.start_time.isoformat()}|{self.duration_min}".encode()
        digest = hashlib.blake2s(raw, digest_size=5).digest()
        return f"ST_{base64.b32encode(digest).decode().rstrip('=').lower()}"


class Calendar(Protocol):
    async def initialize(self) -> None: ...
    async def schedule_appointment(
        self,
        *,
        start_time: datetime.datetime,
        attendee_name: str = "",
        phone_number: str = "",
    ) -> str: ...
    async def list_available_slots(
        self, *, start_time: datetime.datetime, end_time: datetime.datetime
    ) -> list[AvailableSlot]: ...
    async def list_bookings(
        self, *, start_time: datetime.datetime, end_time: datetime.datetime
    ) -> list[dict]: ...


class FakeCalendar(Calendar):
    def __init__(
        self, *, timezone: str, slots: list[AvailableSlot] | None = None
    ) -> None:
        self.tz = ZoneInfo(timezone)
        self._slots: list[AvailableSlot] = []
        self._bookings: list[dict] = []

        if slots is not None:
            self._slots.extend(slots)
            return

        today = datetime.datetime.now(self.tz).date()
        for day_offset in range(1, 90):  # generate slots for the next 90 days
            current_day = today + datetime.timedelta(days=day_offset)
            if current_day.weekday() >= 5:
                continue

            # build all possible 30-min slots between 09:00 and 17:00
            day_start = datetime.datetime.combine(
                current_day, datetime.time(9, 0), tzinfo=self.tz
            )
            slots_in_day = [
                day_start + datetime.timedelta(minutes=30 * i)
                for i in range(int((17 - 9) * 2))  # (17-9)=8 hours => 16 slots
            ]

            num_slots = random.randint(3, 6)
            chosen = random.sample(slots_in_day, num_slots)

            for slot_start in sorted(chosen):
                self._slots.append(
                    AvailableSlot(start_time=slot_start, duration_min=30)
                )

    async def initialize(self) -> None:
        pass

    async def schedule_appointment(
        self,
        *,
        start_time: datetime.datetime,
        attendee_name: str = "",
        phone_number: str = "",
    ) -> str:
        # fake it by just removing it from our slots list and adding to bookings
        self._slots = [slot for slot in self._slots if slot.start_time != start_time]
        self._bookings.append(
            {
                "start": start_time.isoformat(),
                "attendee": {"name": attendee_name, "phone": phone_number},
            }
        )
        return "Appointment scheduled successfully (Fake)."

    async def list_available_slots(
        self, *, start_time: datetime.datetime, end_time: datetime.datetime
    ) -> list[AvailableSlot]:
        return [
            slot for slot in self._slots if start_time <= slot.start_time < end_time
        ]

    async def list_bookings(
        self, *, start_time: datetime.datetime, end_time: datetime.datetime
    ) -> list[dict]:
        return [
            b
            for b in self._bookings
            if start_time <= datetime.datetime.fromisoformat(b["start"]) < end_time
        ]


CAL_COM_EVENT_TYPE = "livekit-front-desk"
EVENT_DURATION_MIN = 30
BASE_URL = "https://api.cal.com/v2/"


class CalComCalendar(Calendar):
    def __init__(self, *, api_key: str, timezone: str) -> None:
        self.tz = ZoneInfo(timezone)
        self._api_key = api_key

        self._http_session = None

        self._logger = logging.getLogger("cal.com")

    async def initialize(self) -> None:
        if self._http_session is None:
            # We import here to avoid issues if the loop isn't running yet during module load
            import aiohttp
            from livekit.agents.utils import http_context

            try:
                self._http_session = http_context.http_session()
            except RuntimeError:
                self._http_session = aiohttp.ClientSession()

        async with self._http_session.get(
            headers=self._build_headers(api_version="2024-06-14"), url=f"{BASE_URL}me/"
        ) as resp:
            resp.raise_for_status()
            username = (await resp.json())["data"]["username"]
            self._logger.info(f"using cal.com username: {username}")

        query = urlencode({"username": username})
        async with self._http_session.get(
            headers=self._build_headers(api_version="2024-06-14"),
            url=f"{BASE_URL}event-types/?{query}",
        ) as resp:
            resp.raise_for_status()
            data = (await resp.json())["data"]
            lk_event_type = next(
                (event for event in data if event.get("slug") == CAL_COM_EVENT_TYPE),
                None,
            )

            if lk_event_type:
                self._lk_event_id = lk_event_type["id"]
            else:
                async with self._http_session.post(
                    headers=self._build_headers(api_version="2024-06-14"),
                    url=f"{BASE_URL}event-types",
                    json={
                        "lengthInMinutes": EVENT_DURATION_MIN,
                        "title": "LiveKit Front-Desk",
                        "slug": CAL_COM_EVENT_TYPE,
                    },
                ) as resp:
                    resp.raise_for_status()
                    self._logger.info(
                        f"successfully added {CAL_COM_EVENT_TYPE} event type"
                    )
                    data = (await resp.json())["data"]
                    self._lk_event_id = data["id"]

            self._logger.info(f"event type id: {self._lk_event_id}")

    async def schedule_appointment(
        self,
        *,
        start_time: datetime.datetime,
        attendee_name: str = "",
        phone_number: str = "",
    ) -> str:
        start_time = start_time.astimezone(datetime.timezone.utc)
        normalized_phone = _normalize_phone_number(phone_number)

        payload = {
            "start": start_time.isoformat(),
            "attendee": {
                "name": attendee_name,
                "phoneNumber": normalized_phone,
                "timeZone": str(self.tz),
            },
            "eventTypeId": self._lk_event_id,
        }

        self._logger.info(f"CAL.COM PAYLOAD: {payload}")

        try:
            async with self._http_session.post(
                headers=self._build_headers(api_version="2024-06-14"),
                url=f"{BASE_URL}bookings",
                json=payload,
            ) as resp:
                self._logger.info(f"Booking response status: {resp.status}")
                
                if resp.status >= 400:
                    data = await resp.json()
                    self._logger.warning(f"Booking error response: {data}")
                    error_info = data.get("error", {})
                    message = (
                        error_info.get("message", "")
                        if isinstance(error_info, dict)
                        else str(error_info)
                    )
                    
                    if "not available" in message.lower() or "conflict" in message.lower():
                        return f"Error: This slot is no longer available. ({message})"
                    
                    return f"Error: Cal.com API returned {resp.status}. Message: {message}"

                return "Appointment scheduled successfully."

        except Exception as e:
            self._logger.error(f"Exception during scheduling: {e}")
            return f"Error: Failed to connect to booking system. {str(e)}"

    async def list_available_slots(
        self, *, start_time: datetime.datetime, end_time: datetime.datetime
    ) -> list[AvailableSlot]:
        start_time = start_time.astimezone(datetime.timezone.utc)
        end_time = end_time.astimezone(datetime.timezone.utc)
        query = urlencode(
            {
                "eventTypeId": self._lk_event_id,
                "start": start_time.isoformat(),
                "end": end_time.isoformat(),
            }
        )
        async with self._http_session.get(
            headers=self._build_headers(api_version="2024-09-04"),
            url=f"{BASE_URL}slots/?{query}",
        ) as resp:
            resp.raise_for_status()
            raw_data = (await resp.json())["data"]

            available_slots = []
            for _, slots in raw_data.items():
                for slot in slots:
                    start_dt = datetime.datetime.fromisoformat(
                        slot["start"].replace("Z", "+00:00")
                    )
                    available_slots.append(
                        AvailableSlot(
                            start_time=start_dt, duration_min=EVENT_DURATION_MIN
                        )
                    )

        return available_slots

    async def list_bookings(
        self, *, start_time: datetime.datetime, end_time: datetime.datetime
    ) -> list[dict]:
        start_time = start_time.astimezone(datetime.timezone.utc)
        end_time = end_time.astimezone(datetime.timezone.utc)
        query = urlencode(
            {
                "eventTypeId": self._lk_event_id,
                "afterStart": start_time.isoformat(),
                "beforeEnd": end_time.isoformat(),
            }
        )
        async with self._http_session.get(
            headers=self._build_headers(api_version="2026-02-25"),
            url=f"{BASE_URL}bookings/?{query}",
        ) as resp:
            resp.raise_for_status()
            raw_data = (await resp.json())["data"]
            return [b for b in raw_data if b.get("status") == "accepted"]

    def _build_headers(self, *, api_version: str | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._api_key}"}
        if api_version:
            h["cal-api-version"] = api_version
        return h
