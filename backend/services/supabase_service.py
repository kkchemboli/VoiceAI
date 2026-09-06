import logging
import os
from prompts.inbound_prompt import DEFAULT_SYSTEM_PROMPT, DEFAULT_GREETING

logger = logging.getLogger("voice-agent")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")


async def fetch_agent_config_from_supabase():
    """Fetch system_prompt, opening_greeting, and knowledge_base from Supabase."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.info("Supabase not configured, using default config")
        return {
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "opening_greeting": DEFAULT_GREETING,
            "knowledge_texts": [],
        }

    try:
        from supabase import create_client

        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

        config = {
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "opening_greeting": DEFAULT_GREETING,
            "outbound_system_prompt": "",  # Empty by default to trigger fallback
            "outbound_opening_greeting": "",
            "knowledge_texts": [],
        }

        try:
            response = supabase.table("agent_config").select("key", "value").execute()
            if response.data:
                for item in response.data:
                    key = item.get("key")
                    value = item.get("value")
                    if key in config and value:
                        config[key] = value
        except Exception as e:
            logger.error(f"Error fetching config from agent_config table: {e}")

        knowledge_response = (
            supabase.table("knowledge_base").select("content").execute()
        )
        if knowledge_response.data:
            config["knowledge_texts"] = [
                item["content"]
                for item in knowledge_response.data
                if item.get("content")
            ]

        logger.info("Successfully fetched config from Supabase")
        return config
    except Exception as e:
        logger.error(f"Error fetching config from Supabase: {e}")
        return {
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "opening_greeting": DEFAULT_GREETING,
            "knowledge_texts": [],
        }


def save_call_log_to_supabase(
    user_phone: str,
    customer_name: str,
    summary: str,
    duration: str,
    is_booked: bool,
    room_name: str,
):
    """Save call log record to Supabase call_logs table."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return

    try:
        from supabase import create_client

        sb = create_client(SUPABASE_URL, SUPABASE_KEY)

        sb.table("call_logs").insert(
            {
                "phone_number": user_phone or "",
                "customer_name": customer_name or "",
                "summary": summary,
                "duration": duration,
                "status": "booked" if is_booked else "completed",
                "metadata": {"room_name": room_name},
            }
        ).execute()
        logger.info("Call log saved to Supabase")
    except Exception as e:
        logger.error(f"Error saving call log to Supabase: {e}")
