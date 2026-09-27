/**
 * Single place every API call goes through.
 *
 * The login gate works by a cookie, so two things matter here and neither is
 * obvious at a call site:
 *
 * 1. The cookie has to be sent. It is issued with `Secure`, so it only travels
 *    over HTTPS, and same-origin requests send it by default. `same-origin` is
 *    stated explicitly because dropping it looks harmless and silently logs the
 *    user out. If VITE_API_URL ever points at a different origin this has to
 *    become 'include' (and the server needs credentialed CORS, which it
 *    already allows) -- otherwise the cookie is withheld.
 *
 * 2. A 401 has to bounce the user to the login screen. The session expires
 *    after 12 hours, and a tab left open overnight would otherwise sit there
 *    showing stale data and silent failures on every later click. Catching that
 *    centrally means no individual view has to remember to handle it.
 */

const API_BASE = import.meta.env.VITE_API_URL || '';

type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler | null = null;

/** Registered once by App; called whenever the API answers 401. */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler;
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'same-origin',
    ...init,
  });

  if (response.status === 401) {
    onUnauthorized?.();
  }

  return response;
}

export interface SessionState {
  authenticated: boolean;
  username: string | null;
}

/**
 * Whether the browser currently holds a valid session.
 *
 * Answers 200 either way rather than 401 when logged out, so "not signed in" is
 * a normal result to read rather than an error to catch.
 */
export async function checkSession(): Promise<SessionState> {
  try {
    const response = await fetch(`${API_BASE}/api/auth/session`, {
      credentials: 'same-origin',
    });
    if (!response.ok) return { authenticated: false, username: null };
    return await response.json();
  } catch {
    // Network-level failure. Treated as "not signed in": showing the login form
    // is recoverable, showing an empty dashboard that cannot load anything is not.
    return { authenticated: false, username: null };
  }
}

export interface LoginResult {
  ok: boolean;
  error?: string;
}

/**
 * Exchange credentials for a session cookie.
 *
 * Uses a raw fetch rather than apiFetch on purpose: a 401 here means "wrong
 * password", and routing that through the global handler would log the user out
 * of the screen they are already on.
 */
export async function login(username: string, password: string): Promise<LoginResult> {
  try {
    const response = await fetch(`${API_BASE}/api/auth/login`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });

    if (response.ok) return { ok: true };

    const data = await response.json().catch(() => ({}));
    return { ok: false, error: data.detail || 'Login failed' };
  } catch {
    return { ok: false, error: 'Could not reach the server' };
  }
}

/**
 * Clear the session cookie server-side.
 *
 * The signed token stays valid until it expires, since sessions are stateless.
 * That is a deliberate trade for a 12-hour single-user session: logging out
 * ends this browser's session immediately, but a token captured earlier stays
 * replayable until it ages out.
 */
export async function logout(): Promise<void> {
  try {
    await fetch(`${API_BASE}/api/auth/logout`, {
      method: 'POST',
      credentials: 'same-origin',
    });
  } catch {
    // Nothing useful to do. The cookie is cleared by the server response; if
    // that never arrived the caller still returns to the login screen.
  }
}
