"""Distributed lock helpers backed by Redis.

Prevents concurrent bulk lead imports: exactly one import runs at a time.
The lock is acquired with SET NX EX and released with an atomic
compare-and-delete so a crashed owner cannot block imports forever and a
TTL-expired owner cannot delete a newer owner's lock.
"""

import uuid

import redis

from core.config import settings

BULK_IMPORT_LOCK = "campaign:bulk-import:lock"
BULK_IMPORT_LOCK_TTL_SECONDS = 1800

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