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
    played_seconds: float = 0
    duration_seconds: float | None = None
    completed: bool = False
    skipped_early: bool = False
    context_type: str = ""
    context_key: str = ""
    event_type: str = ""
    session_id: str = ""
    played_ms: int | None = None
    duration_ms: int | None = None
    timestamp_utc: str = ""
    source_context: str = ""
    client_event_id: str = ""
    wrapped_schema_version: int = 1


class SaveMixRequest(BaseModel):
    name: str = ""


@router.post("/api/activity/listen")
async def record_listen_event(payload: ListenEventRequest, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    canonical_recorded = False
    normalized_event = personalization_service.normalize_tracking_event_type(payload.event_type)
    played_ms = payload.played_ms if payload.played_ms is not None else int(float(payload.played_seconds or 0) * 1000)
    duration_ms = payload.duration_ms
    if duration_ms is None and payload.duration_seconds is not None:
        duration_ms = int(float(payload.duration_seconds) * 1000)

    if normalized_event:
        canonical_recorded = personalization_service.record_tracking_event(
            username=user.username,
            user_id=int(user.id),
            track_id=payload.track_id,
            event_type=normalized_event,
            session_id=payload.session_id,
            played_ms=played_ms,
            duration_ms=duration_ms,
            timestamp_utc=payload.timestamp_utc or None,
            context_type=payload.context_type,
            context_key=payload.context_key,
            source_context=payload.source_context or payload.context_type,
            client_event_id=payload.client_event_id,
            wrapped_schema_version=max(1, int(payload.wrapped_schema_version or 1)),
            event_payload={
                "completed": bool(payload.completed),
                "skipped_early": bool(payload.skipped_early),
            },
        )

    should_write_legacy = not normalized_event or normalized_event in {"play_complete", "skip"}
    legacy_recorded = False
    if should_write_legacy:
        legacy_recorded = personalization_service.record_track_play(
            db,
            user,
            track_id=payload.track_id,
            played_seconds=float(payload.played_seconds or 0),
            duration_seconds=payload.duration_seconds,
            completed=payload.completed or normalized_event == "play_complete",
            skipped_early=payload.skipped_early or normalized_event == "skip",
            context_type=payload.context_type,
            context_key=payload.context_key,
            write_tracking_backfill=not bool(normalized_event),
        )

    return JSONResponse(
        {
            "status": "ok",
            "recorded": bool(canonical_recorded or legacy_recorded),
            "canonical_recorded": bool(canonical_recorded),
            "legacy_recorded": bool(legacy_recorded),
        }
    )


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
