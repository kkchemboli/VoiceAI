import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Any

logger = logging.getLogger("voice-agent")


@dataclass
class AgentState:
    """Manages Agent Session Conversational State including language locking and booking memory."""

    # Language State
    current_lang: Optional[str] = None

    # Conversational Memory State
    selected_course: Optional[str] = None
    caller_name: Optional[str] = None
    name_confirmed: bool = False
    phone_number: Optional[str] = None
    phone_confirmed: bool = False
    selected_slot: Optional[str] = None
    booking_stage: str = "NOT_STARTED"

    booking_info: Dict[str, Any] = field(
        default_factory=lambda: {
            "booked": False,
            "name": "",
            "phone": "",
            "date": "",
            "time": "",
        }
    )

    LANGUAGE_CONFIG: Dict[str, Dict[str, Any]] = field(
        default_factory=lambda: {
            "hi": {"lang": "hi-IN", "speaker": "roopa", "pace": 1.05},
            "en": {"lang": "hi-IN", "speaker": "roopa", "pace": 1.05},
        }
    )

    def lock_language(self, text: str, STT_language: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Determine language from transcript/STT metadata and lock language if unassigned (100% UNCHANGED)."""
        if self.current_lang is not None:
            return None

        detected_lang = None
        lower = text.lower()

        if "hindi" in lower or lower == "hi" or "हिंदी" in lower or "hinglish" in lower:
            detected_lang = "hi"
        elif "english" in lower or lower == "en" or "अंग्रेजी" in lower:
            detected_lang = "en"
        elif STT_language:
            detected_lang = STT_language.split("-")[0]

        if detected_lang:
            config = self.LANGUAGE_CONFIG.get(detected_lang, self.LANGUAGE_CONFIG["hi"])
            self.current_lang = str(config["lang"])
            logger.info(
                f"Language LOCKED to {config['lang']} based on transcript/metadata: '{text}'"
            )
            return config

        return None

    def update_course_selection(self, text: str) -> Optional[str]:
        """Deterministically match and update selected_course if user specifies a course."""
        if not text:
            return self.selected_course

        lower = text.lower()
        discovery_triggers = [
            "what courses",
            "which courses",
            "type of courses",
            "courses do you offer",
            "courses do you provide",
            "kaun kaun se",
            "kya courses",
        ]
        if any(trig in lower for trig in discovery_triggers):
            return self.selected_course

        course_map = {
            "iphone": "iPhone Repairing Course",
            "mobile": "Mobile Repairing Course",
            "laptop": "Laptop Repairing Course",
            "macbook": "MacBook Repairing Course",
            "cctv": "CCTV Camera Training",
            "smart tv": "LED/Smart TV Repairing Course",
            "led tv": "LED/Smart TV Repairing Course",
            "lcd tv": "LED/Smart TV Repairing Course",
            "ac pcb": "AC PCB Repairing Course",
            "pcb": "AC PCB Repairing Course",
        }

        for key, canonical in course_map.items():
            if key in lower:
                self.selected_course = canonical
                logger.info(f"AgentState: selected_course updated to '{canonical}'")
                return canonical

        return self.selected_course

    def update_booking_stage(self, stage: str):
        """Update the active booking stage."""
        valid_stages = ["NOT_STARTED", "SLOT_SELECTION", "NAME_COLLECTION", "PHONE_COLLECTION", "CONFIRMATION", "COMPLETED"]
        if stage in valid_stages:
            self.booking_stage = stage
            logger.info(f"AgentState: booking_stage transitioned to '{stage}'")

    def get_state_summary(self) -> Optional[str]:
        """Return a concise state summary for LLM context injection."""
        items = []
        if self.selected_course:
            items.append(f"Selected Course: {self.selected_course}")
        if self.booking_stage != "NOT_STARTED":
            items.append(f"Booking Stage: {self.booking_stage}")
        if self.caller_name:
            confirmed_str = "Confirmed" if self.name_confirmed else "Unconfirmed"
            items.append(f"Caller Name: {self.caller_name} ({confirmed_str})")
        if self.phone_number:
            confirmed_str = "Confirmed" if self.phone_confirmed else "Unconfirmed"
            items.append(f"Phone: {self.phone_number} ({confirmed_str})")

        if not items:
            return None

        return "CURRENT CONVERSATIONAL STATE:\n- " + "\n- ".join(items)

