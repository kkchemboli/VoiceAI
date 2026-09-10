"""Shared Supabase client lifecycle.

Every module that talks to Supabase uses the same lazy-initialized singleton
instead of creating its own client. `execute_query` runs the synchronous
`.execute()` on a worker thread so it never blocks the asyncio event loop.
"""

import asyncio
import logging
from typing import Optional

from supabase import Client, create_client

from core.config import settings

logger = logging.getLogger("voice-agent")

_client: Optional[Client] = None


def supabase_configured() -> bool:
    """Return True when Supabase credentials are available."""
    return bool(settings.supabase_url and settings.supabase_key)


def get_supabase_client() -> Optional[Client]:
    """Return the shared Supabase client, or None when not configured."""
    global _client

    if _client is not None:
        return _client

    if not supabase_configured():
        logger.info("Supabase not configured, disabling Supabase integration")
        return None

    try:
        _client = create_client(settings.supabase_url, settings.supabase_key)
        logger.info("Connected to Supabase")
    except Exception:
        logger.exception("Failed to connect to Supabase")
        _client = None

    return _client


async def execute_query(builder) -> object:
    """Execute a Supabase query builder without blocking the event loop."""
    return await asyncio.to_thread(builder.execute)