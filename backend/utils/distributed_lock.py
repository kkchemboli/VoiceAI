"""Distributed lock helpers backed by Redis.

Used to keep expensive work to a single owner: one bulk lead import at a time,
and one RAG index build per knowledge-base fingerprint across every process.
Locks are acquired with SET NX EX and released with an atomic compare-and-delete
so a crashed owner cannot block work forever and a TTL-expired owner cannot
delete a newer owner's lock.
"""

import time
import uuid

import redis

from core.config import settings

BULK_IMPORT_LOCK = "campaign:bulk-import:lock"
BULK_IMPORT_LOCK_TTL_SECONDS = 1800

RAG_BUILD_LOCK = "rag:build:{hash}"
RAG_BUILD_LOCK_TTL_SECONDS = 120

redis_client = redis.from_url(settings.redis_url, decode_responses=True)

_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


def acquire_lock(client, name, ttl_seconds):
    """Try to acquire `name`; return the ownership token, or None if held."""
    token = uuid.uuid4().hex
    if client.set(name, token, nx=True, ex=ttl_seconds):
        return token
    return None


def release_lock(client, name, token):
    """Release `name` only if we still own it (compare-and-delete)."""
    client.eval(_RELEASE_LOCK_SCRIPT, 1, name, token)


def is_lock_held(client, name) -> bool:
    """Return True when `name` is currently held."""
    return bool(client.exists(name))


def wait_for_lock(client, name, ttl_seconds, wait_seconds, poll_seconds=0.25):
    """Acquire `name`, polling for up to `wait_seconds`. Return a token or None.

    Callers holding this lock are optimising for cost, not correctness, so a
    `None` return means "carry on without the lock" rather than "fail".
    """
    deadline = time.monotonic() + wait_seconds
    while True:
        token = acquire_lock(client, name, ttl_seconds)
        if token:
            return token
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_seconds)
