"""Shared runtime configuration for the API, voice agent, and Celery services."""

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _optional_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _int_or(value: Optional[str], fallback: int) -> int:
    parsed = _optional_int(value)
    return fallback if parsed is None else parsed


def _float_or(value: Optional[str], fallback: float) -> float:
    if not value:
        return fallback
    try:
        return float(value)
    except ValueError:
        return fallback


@dataclass(frozen=True)
class Settings:
    timezone: str
    cors_allowed_origins: tuple[str, ...]
    supabase_url: Optional[str]
    supabase_key: Optional[str]
    redis_url: str
    livekit_url: Optional[str]
    livekit_api_key: Optional[str]
    livekit_api_secret: Optional[str]
    livekit_sip_outbound_trunk_id: Optional[str]
    livekit_sip_room: str
    openai_api_key: Optional[str]
    openai_llm_model: str
    cal_api_key: Optional[str]
    cal_event_id: Optional[int]
    google_sheet_url: Optional[str]
    default_transfer_number: Optional[str]
    sarvam_api_key: Optional[str]
    groq_api_key: Optional[str]
    groq_llm_model: Optional[str]
    # LiveKit worker capacity. livekit-agents 1.5.x has no hard concurrency cap:
    # num_idle_processes is the size of the warm job-process pool, and load
    # beyond that is shed by CPU-based admission control at load_threshold.
    voice_num_idle_processes: int
    voice_load_threshold: float

    @classmethod
    def from_env(cls) -> "Settings":
        origins = os.getenv(
            "CORS_ALLOWED_ORIGINS",
            "http://localhost:5173,http://localhost:3000",
        )
        return cls(
            timezone=os.getenv("TZ", "Asia/Kolkata"),
            cors_allowed_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
            supabase_url=os.getenv("SUPABASE_URL"),
            supabase_key=os.getenv("SUPABASE_KEY"),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            livekit_url=os.getenv("LIVEKIT_URL"),
            livekit_api_key=os.getenv("LIVEKIT_API_KEY"),
            livekit_api_secret=os.getenv("LIVEKIT_API_SECRET"),
            livekit_sip_outbound_trunk_id=os.getenv("LIVEKIT_SIP_OUTBOUND_TRUNK_ID"),
            livekit_sip_room=os.getenv("LIVEKIT_SIP_ROOM", "outbound-call"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_llm_model=os.getenv("OPENAI_LLM_MODEL", "gpt-4.1-mini"),
            cal_api_key=os.getenv("CAL_API_KEY"),
            cal_event_id=_optional_int(os.getenv("CAL_EVENT_ID")),
            google_sheet_url=os.getenv("GOOGLE_SHEET_URL"),
            default_transfer_number=os.getenv("DEFAULT_TRANSFER_NUMBER"),
            sarvam_api_key=os.getenv("SARVAM_API_KEY"),
            groq_api_key=os.getenv("GROQ_API_KEY"),
            groq_llm_model=os.getenv("GROQ_LLM_MODEL"),
            voice_num_idle_processes=max(
                0, _int_or(os.getenv("VOICE_NUM_IDLE_PROCESSES"), 4)
            ),
            voice_load_threshold=_float_or(os.getenv("VOICE_LOAD_THRESHOLD"), 0.7),
        )


settings = Settings.from_env()
