from __future__ import annotations

import database
import personalization_service
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .core import get_current_user_safe, get_db

router = APIRouter()


class ListenEventRequest(BaseModel):
    track_id: int
    played_seconds: float
    duration_seconds: float | None = None
    completed: bool = False
    skipped_early: bool = False
    context_type: str = ""
    context_key: str = ""


class SaveMixRequest(BaseModel):
    name: str = ""


@router.post("/api/activity/listen")
async def record_listen_event(payload: ListenEventRequest, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    recorded = personalization_service.record_track_play(
        db,
        user,
        track_id=payload.track_id,
        played_seconds=payload.played_seconds,
        duration_seconds=payload.duration_seconds,
        completed=payload.completed,
        skipped_early=payload.skipped_early,
        context_type=payload.context_type,
        context_key=payload.context_key,
    )
    return JSONResponse({"status": "ok", "recorded": bool(recorded)})


@router.get("/api/mixes")
async def list_mixes(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return JSONResponse(personalization_service.get_mix_summaries(db, user))


@router.get("/api/mixes/{mix_key}")
async def get_mix(mix_key: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    mix = personalization_service.get_mix_detail(db, user, mix_key)
    if not mix:
        return JSONResponse({"error": "Mix not found"}, status_code=404)
    return JSONResponse(mix)


@router.post("/api/mixes/{mix_key}/save")
async def save_mix_as_playlist(mix_key: str, payload: SaveMixRequest, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    mix = personalization_service.get_mix_detail(db, user, mix_key)
    if not mix or not mix.get("items"):
        return JSONResponse({"error": "Mix not found"}, status_code=404)

    from .favorites import schedule_navidrome_sync
    from .playlists import build_unique_copy_name, generate_m3u_for_playlist, schedule_playlist_sync

    base_name = (payload.name or "").strip() or mix["title"]
    playlist_name = build_unique_copy_name(db, user.id, base_name)
    playlist = database.Playlist(name=playlist_name, owner_id=user.id)
    db.add(playlist)
    db.commit()
    db.refresh(playlist)

    position = 0
    for item in mix["items"]:
        track_id = int(item.get("db_id") or 0)
        if not track_id:
            continue
        exists = db.query(database.Track.id).filter(database.Track.id == track_id).first()
        if not exists:
            continue
        db.add(
            database.PlaylistItem(
                playlist_id=playlist.id,
                track_id=track_id,
                position=position,
            )
        )
        position += 1
    db.commit()

    generate_m3u_for_playlist(db, playlist, user.username)
    schedule_playlist_sync(db, user)
    schedule_navidrome_sync(user.id, user.username, delay_seconds=2.0)

    return JSONResponse({"id": playlist.id, "name": playlist.name})
