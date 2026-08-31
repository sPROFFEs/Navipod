import importlib.util
import sys
from datetime import timedelta
from pathlib import Path

import auth
import database
import pytest
import security
from fastapi import HTTPException
from starlette.requests import Request

AUTH_BROWSER_SPEC = importlib.util.spec_from_file_location(
    "navipod_concierge_auth_browser",
    Path(__file__).resolve().parents[1] / "auth_browser.py",
)
auth_browser = importlib.util.module_from_spec(AUTH_BROWSER_SPEC)
assert AUTH_BROWSER_SPEC and AUTH_BROWSER_SPEC.loader
sys.modules["navipod_concierge_auth_browser"] = auth_browser
AUTH_BROWSER_SPEC.loader.exec_module(auth_browser)


def request_for(method="GET", *, token=None, host="navipod.test", origin=None):
    headers = [(b"host", host.encode())]
    if token:
        headers.append((b"cookie", f"access_token={token}".encode()))
    if origin:
        headers.append((b"origin", origin.encode()))
    return Request({"type": "http", "method": method, "path": "/", "headers": headers})


def test_revoked_token_is_rejected_by_central_auth(db_session):
    user = database.User(username="alice", hashed_password="unused", is_active=True)
    db_session.add(user)
    db_session.commit()
    token = auth.create_access_token({"sub": "alice"})
    auth.blacklist_token(db_session, token)

    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(request_for(token=token), db_session)

    assert exc.value.status_code == 401
    assert exc.value.detail == "Session revoked"


def test_inactive_user_is_rejected_even_with_valid_token(db_session):
    db_session.add(database.User(username="disabled", hashed_password="unused", is_active=False))
    db_session.commit()
    token = auth.create_access_token({"sub": "disabled"})

    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(request_for(token=token), db_session)

    assert exc.value.status_code == 401


def test_legacy_unsafe_username_is_rejected(db_session):
    db_session.add(database.User(username="../escape", hashed_password="unused", is_active=True))
    db_session.commit()
    token = auth.create_access_token({"sub": "../escape"})

    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(request_for(token=token), db_session)

    assert exc.value.status_code == 401


def test_password_session_version_revokes_existing_tokens(db_session):
    user = database.User(username="alice", hashed_password="unused", session_version=0)
    db_session.add(user)
    db_session.commit()
    token = auth.create_access_token({"sub": "alice", "sv": 0})

    user.session_version = 1
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(request_for(token=token), db_session)

    assert exc.value.status_code == 401


def test_remembered_session_is_longer_but_bounded(monkeypatch):
    monkeypatch.setattr(auth, "REMEMBER_SESSION_DAYS", 30)
    assert auth.session_expiry(False) == timedelta(days=1)
    assert auth.session_expiry(True) == timedelta(days=30)

    monkeypatch.setattr(auth, "REMEMBER_SESSION_DAYS", 10_000)
    assert auth.session_expiry(True) == timedelta(days=365)


def test_auth_browser_cookie_round_trips_without_exposing_separate_credentials():
    value = auth_browser.encode_cookie("session-123", "token-value-that-is-long-enough")

    assert auth_browser.decode_cookie(value) == ("session-123", "token-value-that-is-long-enough")
    assert auth_browser.decode_cookie("malformed") == ("", "")
    assert auth_browser.decode_cookie("short.token") == ("", "")


def test_cookie_authenticated_write_requires_same_origin():
    with pytest.raises(HTTPException) as exc:
        security.validate_same_origin(request_for("POST", token="token", origin="https://attacker.example"))
    assert exc.value.status_code == 403

    security.validate_same_origin(request_for("POST", token="token", origin="https://navipod.test"))


@pytest.mark.parametrize("username", ["../admin", "a", "space user", "semi;colon"])
def test_unsafe_usernames_are_rejected(username):
    assert not auth.is_valid_username(username)
