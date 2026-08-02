"""Rule-based playlists materialized into normal playlist items."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import database
import library_service
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .core import get_current_user_safe, get_db

router = APIRouter()


class SmartRules(BaseModel):
    artist: str = ""
    album: str = ""
    genre: str = ""
    year_min: int | None = Field(default=None, ge=1000, le=9999)
    year_max: int | None = Field(default=None, ge=1000, le=9999)
    favorite_only: bool = False
    added_within_days: int | None = Field(default=None, ge=0)
    min_plays: int | None = Field(default=None, ge=0)
    not_played_days: int | None = Field(default=None, ge=0)
    limit: int = Field(default=50, ge=1, le=500)
    sort: str = "newest"


class SmartPlaylistRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    rules: SmartRules


def _rules_dict(payload: SmartRules) -> dict:
    return payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()


def _materialize(db: Session, playlist, user, rules: dict) -> int:
    track_ids = library_service.smart_track_ids(db, user, rules)
    db.query(database.PlaylistItem).filter(database.PlaylistItem.playlist_id == playlist.id).delete(
        synchronize_session=False
    )
    for position, track_id in enumerate(track_ids, start=1):
        db.add(database.PlaylistItem(playlist_id=playlist.id, track_id=track_id, position=position))
    playlist.smart_rules_json = json.dumps(library_service.normalize_smart_rules(rules), sort_keys=True)
    playlist.smart_updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(playlist)

    from .favorites import schedule_navidrome_sync
    from .playlists import generate_m3u_for_playlist, schedule_playlist_sync

    generate_m3u_for_playlist(db, playlist, user.username)
    schedule_playlist_sync(db, user)
    schedule_navidrome_sync(user.id, user.username, delay_seconds=2.0)
    return len(track_ids)


@router.post("/api/smart-playlists")
async def create_smart_playlist(payload: SmartPlaylistRequest, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    name = payload.name.strip()
    if not name:
        return JSONResponse({"error": "Playlist name is required"}, status_code=400)
    rules = _rules_dict(payload.rules)
    try:
        library_service.normalize_smart_rules(rules)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    playlist = database.Playlist(name=name, owner_id=user.id, smart_rules_json="{}")
    db.add(playlist)
    db.flush()
    try:
        count = _materialize(db, playlist, user, rules)
    except Exception:
        db.rollback()
        raise
    return JSONResponse({"id": playlist.id, "name": playlist.name, "track_count": count}, status_code=201)


@router.put("/api/smart-playlists/{playlist_id}")
async def update_smart_playlist(
    playlist_id: int,
    payload: SmartPlaylistRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    playlist = (
        db.query(database.Playlist)
        .filter(
            database.Playlist.id == playlist_id,
            database.Playlist.owner_id == user.id,
            database.Playlist.smart_rules_json.isnot(None),
        )
        .first()
    )
    if not playlist:
        return JSONResponse({"error": "Smart playlist not found"}, status_code=404)
    name = payload.name.strip()
    if not name:
        return JSONResponse({"error": "Playlist name is required"}, status_code=400)
    playlist.name = name
    try:
        count = _materialize(db, playlist, user, _rules_dict(payload.rules))
    except (TypeError, ValueError) as exc:
        db.rollback()
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"id": playlist.id, "name": playlist.name, "track_count": count})


@router.post("/api/smart-playlists/{playlist_id}/refresh")
async def refresh_smart_playlist(playlist_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    playlist = (
        db.query(database.Playlist)
        .filter(
            database.Playlist.id == playlist_id,
            database.Playlist.owner_id == user.id,
            database.Playlist.smart_rules_json.isnot(None),
        )
        .first()
    )
    if not playlist:
        return JSONResponse({"error": "Smart playlist not found"}, status_code=404)
    try:
        rules = json.loads(playlist.smart_rules_json or "{}")
        count = _materialize(db, playlist, user, rules)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        db.rollback()
        return JSONResponse({"error": f"Invalid smart-playlist rules: {exc}"}, status_code=400)
    return JSONResponse(
        {"id": playlist.id, "track_count": count, "refreshed_at": playlist.smart_updated_at.isoformat()}
    )
