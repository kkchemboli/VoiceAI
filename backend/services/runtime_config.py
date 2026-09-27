"""Runtime-editable configuration persisted in Redis.

The environment holds secrets and must never be mutated from an API endpoint.
Values an administrator may change at runtime (currently the Google Sheet URL)
live in Redis instead, because a JSON file on a container filesystem is not
shared between replicas and does not survive a rebuild.

Env always wins over the stored value, so an operator can pin the URL in the
deployment environment and have the admin panel stop overriding it.
"""

import asyncio
import logging
import os

import redis

from core.config import settings

logger = logging.getLogger("voice-agent")

SHEET_URL_KEY = "config:google_sheet_url"

# decode_responses=True: every value here is text. The RAG index cache needs a
# separate bytes-mode client because it stores raw float32.
_redis = redis.from_url(settings.redis_url, decode_responses=True)

_warned = False


def _warn_once(message: str) -> None:
    """Redis problems here are degraded, not fatal: never fail a call over it."""
    global _warned
    if not _warned:
        _warned = True
        logger.warning("runtime config: %s", message)


def _get_sync() -> str:
    try:
        return _redis.get(SHEET_URL_KEY) or ""
    except Exception as e:
        _warn_once(f"Redis unavailable, sheet URL falls back to env ({type(e).__name__}: {e})")
        return ""


def _set_sync(url: str) -> None:
    try:
        _redis.set(SHEET_URL_KEY, url)
    except Exception as e:
        _warn_once(f"could not persist sheet URL ({type(e).__name__}: {e})")


async def get_sheet_url() -> str:
    """Return the Google Sheet URL: env first, then the value stored in Redis."""
    url = os.getenv("GOOGLE_SHEET_URL")
    if url:
        return url
    return await asyncio.to_thread(_get_sync)


async def set_sheet_url(url: str) -> None:
    """Persist the Google Sheet URL and update the in-memory environment."""
    os.environ["GOOGLE_SHEET_URL"] = url
    await asyncio.to_thread(_set_sync, url)
    logger.info("Google Sheet URL updated in runtime config")
