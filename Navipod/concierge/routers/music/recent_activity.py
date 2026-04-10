"""
Recent playlists and saved radios cache per user.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .core import get_db, get_current_user_safe
from .playlists import fetch_playlist_summaries


router = APIRouter()

RECENT_ITEMS_LIMIT = 3
RECENT_CACHE_LIMIT = 12
RECENT_CACHE_TTL_SECONDS = 365 * 24 * 3600


class RecentPlaylistRequest(BaseModel):
    playlist_id: int


class RecentRadioRequest(BaseModel):
    radio_id: str
    name: str = ""
    stream_url: str = ""


def _recent_cache_path(username: str) -> str:
    cache_dir = f"/saas-data/users/{username}/cache"
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "recent_activity.json")


def _default_payload() -> dict[str, Any]:
    now = time.time()
    return {
        "updated_at": now,
        "expires_at": now + RECENT_CACHE_TTL_SECONDS,
        "playlists": [],
        "radios": [],
    }


def _load_payload(username: str) -> dict[str, Any]:
    path = _recent_cache_path(username)
    if not os.path.exists(path):
        return _default_payload()

    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return _default_payload()
        payload.setdefault("playlists", [])
        payload.setdefault("radios", [])
        payload["updated_at"] = float(payload.get("updated_at") or time.time())
        payload["expires_at"] = float(payload.get("expires_at") or (time.time() + RECENT_CACHE_TTL_SECONDS))
        return payload
    except Exception:
        return _default_payload()


def _write_payload(username: str, payload: dict[str, Any]) -> None:
    path = _recent_cache_path(username)
    now = time.time()
    payload["updated_at"] = now
    payload["expires_at"] = now + RECENT_CACHE_TTL_SECONDS

    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, path)


def _upsert_playlist_entry(payload: dict[str, Any], playlist_id: int) -> None:
    now = time.time()
    playlist_entries = [
        entry for entry in payload.get("playlists", [])
        if isinstance(entry, dict) and int(entry.get("playlist_id") or 0) != int(playlist_id)
    ]
    playlist_entries.insert(0, {"playlist_id": int(playlist_id), "accessed_at": now})
    payload["playlists"] = playlist_entries[:RECENT_CACHE_LIMIT]


def _upsert_radio_entry(payload: dict[str, Any], radio_id: str, name: str = "", stream_url: str = "") -> None:
    now = time.time()
    radio_entries = [
        entry for entry in payload.get("radios", [])
        if isinstance(entry, dict) and str(entry.get("radio_id") or "") != radio_id
    ]
    radio_entries.insert(0, {
        "radio_id": radio_id,
        "name": name,
        "streamUrl": stream_url,
        "accessed_at": now,
    })
    payload["radios"] = radio_entries[:RECENT_CACHE_LIMIT]


def _remove_playlist_entry(payload: dict[str, Any], playlist_id: int) -> None:
    payload["playlists"] = [
        entry for entry in payload.get("playlists", [])
        if isinstance(entry, dict) and int(entry.get("playlist_id") or 0) != int(playlist_id)
    ]


def _remove_radio_entry(payload: dict[str, Any], radio_id: str) -> None:
    payload["radios"] = [
        entry for entry in payload.get("radios", [])
        if isinstance(entry, dict) and str(entry.get("radio_id") or "") != radio_id
    ]


async def _build_recent_response(db: Session, user) -> dict[str, Any]:
    payload = _load_payload(user.username)

    playlist_summaries = fetch_playlist_summaries(db, viewer_id=user.id, owner_id=user.id)
    playlist_lookup = {int(item["id"]): item for item in playlist_summaries}
    recent_playlists = []
    for entry in payload.get("playlists", []):
        playlist_id = int(entry.get("playlist_id") or 0)
        playlist = playlist_lookup.get(playlist_id)
        if playlist:
            recent_playlists.append(playlist)
        if len(recent_playlists) >= RECENT_ITEMS_LIMIT:
            break

    recent_radios = []
    for entry in payload.get("radios", []):
        radio_id = str(entry.get("radio_id") or "")
        stream_url = str(entry.get("streamUrl") or "")
        name = str(entry.get("name") or "").strip()
        if radio_id and name and stream_url:
            recent_radios.append({
                "id": radio_id,
                "name": name,
                "streamUrl": stream_url,
            })
        if len(recent_radios) >= RECENT_ITEMS_LIMIT:
            break

    return {
        "playlists": recent_playlists,
        "radios": recent_radios,
    }


@router.get("/api/recent-activity")
async def get_recent_activity(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    return JSONResponse(await _build_recent_response(db, user))


@router.post("/api/recent-activity/playlist")
async def track_recent_playlist(req: RecentPlaylistRequest, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    payload = _load_payload(user.username)
    _upsert_playlist_entry(payload, req.playlist_id)
    _write_payload(user.username, payload)
    return JSONResponse({"status": "ok"})


@router.post("/api/recent-activity/radio")
async def track_recent_radio(req: RecentRadioRequest, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    payload = _load_payload(user.username)
    _upsert_radio_entry(payload, req.radio_id, req.name.strip(), req.stream_url.strip())
    _write_payload(user.username, payload)
    return JSONResponse({"status": "ok"})


@router.delete("/api/recent-activity/playlist/{playlist_id}")
async def remove_recent_playlist(playlist_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    payload = _load_payload(user.username)
    _remove_playlist_entry(payload, playlist_id)
    _write_payload(user.username, payload)
    return JSONResponse({"status": "ok"})


@router.delete("/api/recent-activity/radio/{radio_id}")
async def remove_recent_radio(radio_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    payload = _load_payload(user.username)
    _remove_radio_entry(payload, radio_id)
    _write_payload(user.username, payload)
    return JSONResponse({"status": "ok"})
