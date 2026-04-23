from __future__ import annotations

import database
import wrapped_service
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .core import get_current_user_safe, get_db

router = APIRouter()


@router.get("/api/wrapped/{year}")
async def get_user_wrapped(
    year: int,
    request: Request,
    force_refresh: bool = Query(False),
    db: Session = Depends(get_db),
):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    visibility = wrapped_service.get_wrapped_settings(db)
    if not visibility["visible"]:
        return JSONResponse({"enabled": False, "visible": False, "year": year})
    operational_year = int(visibility.get("year") or wrapped_service.get_operational_wrapped_year(db))

    try:
        payload = wrapped_service.get_or_build_user_wrapped_summary(
            db, user, operational_year, force_refresh=force_refresh
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if int(year) != operational_year:
        payload["requested_year"] = int(year)
        payload["resolved_year"] = operational_year
    return JSONResponse(payload)


@router.get("/api/wrapped/{year}/party")
async def get_wrapped_party(
    year: int,
    request: Request,
    force_refresh: bool = Query(False),
    db: Session = Depends(get_db),
):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    visibility = wrapped_service.get_wrapped_settings(db)
    if not visibility["visible"]:
        return JSONResponse({"enabled": False, "visible": False, "year": year})
    operational_year = int(visibility.get("year") or wrapped_service.get_operational_wrapped_year(db))

    try:
        payload = wrapped_service.get_or_build_party_summary(db, operational_year, force_refresh=force_refresh)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if int(year) != operational_year:
        payload["requested_year"] = int(year)
        payload["resolved_year"] = operational_year
    return JSONResponse(payload)


@router.post("/api/wrapped/{year}/top-songs/playlist")
async def save_wrapped_top_songs_playlist(year: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    visibility = wrapped_service.get_wrapped_settings(db)
    if not visibility["visible"]:
        return JSONResponse({"error": "Wrapped is not available"}, status_code=403)

    visibility = wrapped_service.get_wrapped_settings(db)
    operational_year = int(visibility.get("year") or wrapped_service.get_operational_wrapped_year(db))
    try:
        summary = wrapped_service.get_or_build_user_wrapped_summary(db, user, operational_year)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    tracks = summary.get("top_songs_playlist", {}).get("tracks") or []
    if not tracks:
        return JSONResponse({"error": "No Wrapped tracks available"}, status_code=404)

    from .favorites import schedule_navidrome_sync
    from .playlists import build_unique_copy_name, generate_m3u_for_playlist, schedule_playlist_sync

    playlist_name = build_unique_copy_name(db, user.id, f"Your Top Songs {summary['year']}")
    playlist = database.Playlist(name=playlist_name, owner_id=user.id)
    db.add(playlist)
    db.commit()
    db.refresh(playlist)

    position = 0
    for item in tracks:
        track_id = int(item.get("db_id") or item.get("id") or 0)
        if not track_id:
            continue
        exists = db.query(database.Track.id).filter(database.Track.id == track_id).first()
        if not exists:
            continue
        db.add(database.PlaylistItem(playlist_id=playlist.id, track_id=track_id, position=position))
        position += 1

    db.commit()
    generate_m3u_for_playlist(db, playlist, user.username)
    schedule_playlist_sync(db, user, force_now=True)
    schedule_navidrome_sync(user.id, user.username, delay_seconds=2.0)
    return JSONResponse({"id": playlist.id, "name": playlist.name, "track_count": position})


@router.post("/admin/api/wrapped/{year}/regenerate")
async def admin_regenerate_wrapped(
    year: int,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user_safe(db, request)
    if not user or not user.is_admin:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    try:
        job_id = wrapped_service.queue_wrapped_regeneration(user.username, year)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    background_tasks.add_task(wrapped_service.run_wrapped_regeneration_job, job_id, year)
    return JSONResponse({"job_id": job_id, "year": year})


@router.post("/admin/api/wrapped/{year}/regenerate-user")
async def admin_regenerate_wrapped_user(
    year: int,
    request: Request,
    background_tasks: BackgroundTasks,
    username: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    user = get_current_user_safe(db, request)
    if not user or not user.is_admin:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    target = db.query(database.User).filter(database.User.username == username.strip()).first()
    if not target:
        return JSONResponse({"error": "User not found"}, status_code=404)

    try:
        job_id = wrapped_service.queue_wrapped_user_regeneration(user.username, year, target.username)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    background_tasks.add_task(wrapped_service.run_wrapped_user_regeneration_job, job_id, year, target.username)
    return JSONResponse({"job_id": job_id, "year": year, "username": target.username})


@router.get("/admin/api/wrapped/{year}/status")
async def admin_wrapped_status(year: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user or not user.is_admin:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    try:
        year = wrapped_service.normalize_year(year)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    user_count = db.query(database.User).filter(database.User.is_active == True).count()
    party = wrapped_service.get_cached_party_summary(year)
    latest_audit = wrapped_service.get_latest_regeneration_audit(year)
    settings = wrapped_service.get_wrapped_settings(db)
    return JSONResponse(
        {
            "year": year,
            "summary_db": str(wrapped_service.get_wrapped_summary_db_path()),
            "summary_db_exists": wrapped_service.get_wrapped_summary_db_path().exists(),
            "active_user_count": user_count,
            "party_generated_at": party.get("generated_at") if party else None,
            "latest_regeneration_audit": latest_audit,
            "settings": settings,
        }
    )
