"""Service integration adapters for Supabase, WhatsApp, and Groq Summarizer."""
from .supabase_service import fetch_agent_config_from_supabase, save_call_log_to_supabase
from .whatsapp_service import send_ziper_whatsapp, send_wabridge_whatsapp
from .summary_service import generate_call_summary

__all__ = [
    "fetch_agent_config_from_supabase",
    "save_call_log_to_supabase",
    "send_ziper_whatsapp",
    "send_wabridge_whatsapp",
    "generate_call_summary",
]
