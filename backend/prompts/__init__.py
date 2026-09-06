"""Modular system prompt package."""
from .core_prompt import CORE_PROMPT
from .course_prompt import COURSE_PROMPT
from .booking_prompt import BOOKING_PROMPT
from .inbound_prompt import DEFAULT_SYSTEM_PROMPT, DEFAULT_GREETING
from .outbound_prompt import OUTBOUND_SYSTEM_PROMPT, format_outbound_prompt

__all__ = [
    "CORE_PROMPT",
    "COURSE_PROMPT",
    "BOOKING_PROMPT",
    "DEFAULT_SYSTEM_PROMPT",
    "DEFAULT_GREETING",
    "OUTBOUND_SYSTEM_PROMPT",
    "format_outbound_prompt",
]
