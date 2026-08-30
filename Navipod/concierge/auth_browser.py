"""Short-lived admin auth-browser sessions.

The worker owns Chromium; Concierge owns the admin authorization token used by
the reverse-proxy route. Tokens stay in memory and are never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass


@dataclass
class AdminBrowserSession:
    session_id: str
    token_digest: str
    admin_id: int
    provider: str
    expires_at: float

    def matches(self, token: str, admin_id: int) -> bool:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return (
            self.admin_id == admin_id
            and hmac.compare_digest(digest, self.token_digest)
            and time.time() < self.expires_at
        )

    def serialize(self) -> dict:
        return {
            "session_id": self.session_id,
            "provider": self.provider,
            "expires_at": self.expires_at,
            "remaining_seconds": max(0, int(self.expires_at - time.time())),
        }


_lock = threading.RLock()
_sessions: dict[str, AdminBrowserSession] = {}


def create(session_id: str, provider: str, admin_id: int, ttl: int) -> tuple[AdminBrowserSession, str]:
    token = secrets.token_urlsafe(32)
    session = AdminBrowserSession(
        session_id=session_id,
        token_digest=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        admin_id=admin_id,
        provider=provider,
        expires_at=time.time() + ttl,
    )
    with _lock:
        _sessions[session_id] = session
    return session, token


def get(session_id: str) -> AdminBrowserSession | None:
    with _lock:
        session = _sessions.get(session_id)
        if not session or time.time() >= session.expires_at:
            _sessions.pop(session_id, None)
            return None
        return session


def validate(session_id: str, token: str, admin_id: int) -> AdminBrowserSession | None:
    session = get(session_id)
    return session if session and session.matches(token, admin_id) else None


def owned_by(session_id: str, admin_id: int) -> bool:
    session = get(session_id)
    return bool(session and session.admin_id == admin_id)


def remove(session_id: str) -> None:
    with _lock:
        _sessions.pop(session_id, None)


def remove_for_admin(admin_id: int) -> None:
    with _lock:
        for session_id, session in list(_sessions.items()):
            if session.admin_id == admin_id:
                _sessions.pop(session_id, None)


def clear_expired() -> None:
    with _lock:
        now = time.time()
        for session_id, session in list(_sessions.items()):
            if now >= session.expires_at:
                _sessions.pop(session_id, None)
