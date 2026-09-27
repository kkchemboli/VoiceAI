"""Single-user login gate for the browser-facing API.

Scope and threat model, because the boundary here is easy to misread:

The API has two distinct client populations, and they are trusted differently.

1. **Browser traffic**, which reaches the API only through the frontend's nginx
   (``/api/`` -> ``api:8000``). nginx stamps every proxied request with
   ``X-Frontend-Proxy: 1``. Those requests must carry a valid session.

2. **The external server**, which talks to the published ``8000`` port directly
   and is trusted without credentials. Its requests carry no marker header.

So the rule is: a marked request must authenticate; an unmarked one is
server-to-server traffic whose protection is the network firewall, not this
code. That is a deliberate choice, recorded here because it looks like a
bypass if you only read the middleware. The marker cannot be forged into an
advantage by a browser: a browser cannot reach 8000 directly, and nginx is the
only thing that sets the header, so browser traffic is always marked and
therefore always challenged. The reverse -- an unmarked request being trusted --
is exactly the published-port case the firewall is there to constrain.

The password is stored only as a bcrypt digest (``ADMIN_PASSWORD_HASH``). A
plaintext password in an env file is instantly usable by anyone who reads it; a
digest is not. Generate one with ``python backend/tools/hash_password.py``.

Sessions are stateless: a signed JWT in an HttpOnly cookie. HttpOnly is the
point -- JavaScript cannot read it, so an XSS bug cannot exfiltrate the session.
``SameSite=Lax`` is the CSRF mitigation for the cookie-authenticated POSTs: a
cross-site form or fetch will not carry the cookie. ``Secure`` means the browser
only ever sends it over HTTPS, which is why the frontend terminates TLS.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from core.config import settings

logger = logging.getLogger(__name__)

# Header the frontend's nginx sets on everything it proxies. See module docstring.
PROXY_MARKER_HEADER = "X-Frontend-Proxy"
PROXY_MARKER_VALUE = "1"

SESSION_COOKIE = "vcagent_session"
# 12 hours: long enough for a working day, short enough that a forgotten browser
# is not a permanent key to the dialer.
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60

# Endpoints reachable without a session. Login and the session probe obviously;
# /health/* is included because the container healthcheck hits it over loopback
# and a healthcheck that needs a cookie is not a healthcheck.
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/api/auth/login",
        "/api/auth/session",
        "/health/live",
        "/health/ready",
    }
)


class AuthMisconfigured(RuntimeError):
    """Raised at API startup when the login gate cannot be enforced."""


def assert_auth_configured() -> None:
    """Fail loudly if the API would otherwise serve an open door.

    Called from the API's startup only. An API that boots without these serves
    every /api route to anyone who can reach it -- including the endpoints that
    place real phone calls -- so refusing to start is the correct failure mode.
    A worker or voice agent never calls this; they do not serve browsers.
    """
    missing = [
        name
        for name, value in (
            ("ADMIN_USERNAME", settings.admin_username),
            ("ADMIN_PASSWORD_HASH", settings.admin_password_hash),
            ("SESSION_SECRET", settings.session_secret),
        )
        if not value
    ]
    if missing:
        raise AuthMisconfigured(
            "The API refuses to start without a login gate configured. Missing: "
            + ", ".join(missing)
            + ". Set them in the environment, or generate a password hash with "
            "python backend/tools/hash_password.py and set SESSION_SECRET to "
            "`openssl rand -hex 32`."
        )
    if not (settings.admin_password_hash or "").startswith("$2"):
        raise AuthMisconfigured(
            "ADMIN_PASSWORD_HASH does not look like a bcrypt digest (expected a "
            "leading '$2'). Store the output of hash_password.py, not a "
            "plaintext password."
        )


def _secret_bytes() -> bytes:
    value = (settings.session_secret or "").strip()
    if not value:
        return b""
    # Accept a hex string as 32 raw bytes so `openssl rand -hex 32` yields a
    # full-entropy key rather than 64 characters of hex-encoded half-strength.
    try:
        if len(value) == 64:
            return bytes.fromhex(value)
    except ValueError:
        pass
    return value.encode("utf-8")


def verify_password(candidate: str) -> bool:
    """Constant-time-ish check of a plaintext password against the stored digest."""
    stored = (settings.admin_password_hash or "").strip()
    if not stored or not candidate:
        return False
    try:
        import bcrypt
    except ImportError:  # pragma: no cover - dependency is pinned
        logger.error("bcrypt is not installed; cannot verify login credentials")
        return False
    try:
        return bcrypt.checkpw(candidate.encode("utf-8"), stored.encode("utf-8"))
    except (ValueError, TypeError):
        # A malformed digest is a config error, not a wrong password. Log it
        # distinctly so a typo in ADMIN_PASSWORD_HASH is not misread as a
        # user typing the wrong thing.
        logger.error("ADMIN_PASSWORD_HASH is not a valid bcrypt digest")
        return False


def issue_session(username: str) -> str:
    """Mint a signed session token for a successfully authenticated user."""
    import jwt

    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=SESSION_MAX_AGE_SECONDS)).timestamp()),
        # A per-token nonce means two logins never share a token string, which
        # makes "is this token revoked" answerable by comparing against a stored
        # set later without changing the signing scheme.
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, _secret_bytes(), algorithm="HS256")


def read_session(request: Request) -> Optional[str]:
    """Return the authenticated username from the cookie, or None."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    import jwt

    try:
        payload = jwt.decode(token, _secret_bytes(), algorithms=["HS256"])
    except Exception:
        # Expired, tampered, or signed with an old secret: all mean "no session"
        # to a browser. Do not leak the reason.
        return None
    subject = payload.get("sub")
    return subject if isinstance(subject, str) and subject else None


def is_proxied(request: Request) -> bool:
    """True when the request arrived through the frontend's nginx."""
    return request.headers.get(PROXY_MARKER_HEADER) == PROXY_MARKER_VALUE


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS


def set_session_cookie(response: JSONResponse, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        # HttpOnly: JavaScript cannot read this, so an XSS bug cannot steal the
        # session. This is the main reason to prefer a cookie over localStorage.
        httponly=True,
        # Secure: only ever sent over HTTPS. The frontend terminates TLS for
        # exactly this reason -- a Secure cookie over plain http:// is silently
        # dropped by the browser, which looks like a login that never sticks.
        secure=True,
        # Lax still allows top-level navigation while blocking the cross-site
        # POSTs that CSRF needs. Strict would be tighter but makes a bookmarked
        # link from another site look logged-out on first load.
        samesite="lax",
        path="/",
        max_age=SESSION_MAX_AGE_SECONDS,
    )


def clear_session_cookie(response: JSONResponse) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def unauthorized() -> JSONResponse:
    return JSONResponse(
        {"detail": "Authentication required"},
        status_code=401,
        # Tell the SPA this is an auth problem, not a random failure, so it can
        # send the user to the login form instead of showing an error toast.
        headers={"WWW-Authenticate": "Cookie"},
    )
