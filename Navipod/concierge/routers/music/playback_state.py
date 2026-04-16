"""
Persistent playback queue state per user.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import database

from .core import get_db, get_current_user_safe


router = APIRouter()

MAX_QUEUE_ITEMS = 500
MAX_TRACK_PAYLOAD_CHARS = 4000


class PlaybackQueueStateRequest(BaseModel):
    manual_queue: list[dict[str, Any]] = Field(default_factory=list)
    context_queue: list[dict[str, Any]] = Field(default_factory=list)
    original_context_queue: list[dict[str, Any]] = Field(default_factory=list)
    current_track: dict[str, Any] | None = None
    current_view_name: str | None = None
    current_view_param: Any = None
    context_index: int = -1
    shuffle_mode: bool = False
    repeat_mode: str = "off"
    current_time: float | int = 0
    duration: float | int = 0
    was_playing: bool = False
    persist_enabled: bool = True


def _safe_json_loads(raw: str | None, fallback):
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _isoformat(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _trim_track_payload(track: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(track, dict):
        return None

    allowed_keys = {
        "id",
        "db_id",
        "title",
        "artist",
        "album",
        "duration",
        "thumbnail",
        "source",
        "is_local",
        "mix_key",
    }
    trimmed = {key: track.get(key) for key in allowed_keys if key in track}
    encoded = json.dumps(trimmed, ensure_ascii=False)
    if len(encoded) > MAX_TRACK_PAYLOAD_CHARS:
        trimmed.pop("thumbnail", None)
    return trimmed


def _trim_queue(raw_queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(raw_queue, list):
        return []
    trimmed = []
    for item in raw_queue[:MAX_QUEUE_ITEMS]:
        track = _trim_track_payload(item)
        if track:
            trimmed.append(track)
    return trimmed


def _serialize_state(row: database.PlaybackQueueState | None):
    if not row:
        return {
            "manual_queue": [],
            "context_queue": [],
            "original_context_queue": [],
            "current_track": None,
            "current_view_name": None,
            "current_view_param": None,
            "context_index": -1,
            "shuffle_mode": False,
            "repeat_mode": "off",
            "current_time": 0,
            "duration": 0,
            "was_playing": False,
            "persist_enabled": True,
            "updated_at": None,
        }

    return {
        "manual_queue": _safe_json_loads(row.manual_queue_json, []),
        "context_queue": _safe_json_loads(row.context_queue_json, []),
        "original_context_queue": _safe_json_loads(row.original_context_queue_json, []),
        "current_track": _safe_json_loads(row.current_track_json, None),
        "current_view_name": row.current_view_name,
        "current_view_param": _safe_json_loads(row.current_view_param_json, None),
        "context_index": row.context_index if row.context_index is not None else -1,
        "shuffle_mode": bool(row.shuffle_mode),
        "repeat_mode": row.repeat_mode or "off",
        "current_time": row.current_time or 0,
        "duration": row.duration or 0,
        "was_playing": bool(row.was_playing),
        "persist_enabled": True,
        "updated_at": _isoformat(row.updated_at),
    }


@router.get("/api/playback/queue-state")
async def get_playback_queue_state(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    row = db.query(database.PlaybackQueueState).filter(
        database.PlaybackQueueState.user_id == user.id
    ).first()
    return JSONResponse(_serialize_state(row))


@router.put("/api/playback/queue-state")
async def save_playback_queue_state(payload: PlaybackQueueStateRequest, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    existing = db.query(database.PlaybackQueueState).filter(
        database.PlaybackQueueState.user_id == user.id
    ).first()

    if not payload.persist_enabled:
        if existing:
            db.delete(existing)
            db.commit()
        return JSONResponse({"status": "disabled"})

    row = existing or database.PlaybackQueueState(user_id=user.id)
    row.manual_queue_json = json.dumps(_trim_queue(payload.manual_queue), ensure_ascii=False)
    row.context_queue_json = json.dumps(_trim_queue(payload.context_queue), ensure_ascii=False)
    row.original_context_queue_json = json.dumps(_trim_queue(payload.original_context_queue), ensure_ascii=False)
    row.current_track_json = json.dumps(_trim_track_payload(payload.current_track), ensure_ascii=False)
    row.current_view_name = (payload.current_view_name or "")[:120] or None
    row.current_view_param_json = json.dumps(payload.current_view_param, ensure_ascii=False)
    row.context_index = int(payload.context_index) if isinstance(payload.context_index, int) else -1
    row.shuffle_mode = bool(payload.shuffle_mode)
    row.repeat_mode = (payload.repeat_mode or "off")[:20]
    row.current_time = max(0, int(float(payload.current_time or 0)))
    row.duration = max(0, int(float(payload.duration or 0)))
    row.was_playing = bool(payload.was_playing)

    if not existing:
        db.add(row)
    db.commit()
    db.refresh(row)
    return JSONResponse({"status": "saved", "updated_at": _isoformat(row.updated_at)})


@router.delete("/api/playback/queue-state")
async def clear_playback_queue_state(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    row = db.query(database.PlaybackQueueState).filter(
        database.PlaybackQueueState.user_id == user.id
    ).first()
    if row:
        db.delete(row)
        db.commit()
    return JSONResponse({"status": "cleared"})
