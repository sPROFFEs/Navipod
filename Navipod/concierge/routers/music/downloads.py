"""
Download management endpoints.
"""
import os
from urllib.parse import urlparse, parse_qs
from fastapi import APIRouter, Request, Depends, Form, BackgroundTasks
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

import database
import manager
import downloader_service
import metadata_service

from .core import get_db, get_current_user_safe


router = APIRouter()

DOWNLOAD_JOB_HISTORY_LIMIT = 100
TERMINAL_DOWNLOAD_STATUSES = ("completed", "finished", "failed", "error")


# --- PYDANTIC MODELS ---

class DownloadRequest(BaseModel):
    url: str
    title: str = None
    artist: str = None
    album: str = None
    source: str = None


def _infer_source_label(raw_source: str | None, raw_url: str | None) -> str:
    source = (raw_source or "").strip().lower()
    if source in {"spotify", "youtube", "musicbrainz", "lastfm", "soundcloud", "audius", "jamendo"}:
        return source

    url = (raw_url or "").strip().lower()
    if "spotify.com" in url:
        return "spotify"
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    if "musicbrainz.org" in url:
        return "musicbrainz"
    if "last.fm" in url:
        return "lastfm"
    if "soundcloud.com" in url:
        return "soundcloud"
    if "audius.co" in url:
        return "audius"
    if "jamendo.com" in url:
        return "jamendo"
    return "external"


async def _resolve_download_url(user, req: DownloadRequest) -> tuple[str, str]:
    """
    Prefer a Spotify track URL for metadata-only sources so the downloader can
    use spotDL first. Falls back to the original URL when no exact-enough match
    is available.
    """
    raw_url = (req.url or "").strip()
    title = (req.title or "").strip()
    artist = (req.artist or "").strip()

    if not raw_url:
        return raw_url, "empty"

    if "youtube.com/watch" in raw_url or "youtu.be/" in raw_url:
        return raw_url, "youtube-direct"

    if "spotify.com/track/" in raw_url:
        return raw_url, "spotify-direct"

    if not title or not artist or not user or not user.download_settings:
        return raw_url, "original"

    settings = user.download_settings
    try:
        resolved = await metadata_service.resolve_download_target(
            settings=settings,
            raw_url=raw_url,
            title=title,
            artist=artist,
            album=(req.album or "").strip(),
            source=(req.source or "").strip(),
        )
        return resolved.get("url") or raw_url, resolved.get("resolution_mode") or "original"
    except Exception as e:
        print(f"[DOWNLOAD] Cross-provider resolve failed: {e}")

    return raw_url, "original"


# --- BACKGROUND TASK ---

async def run_download_in_background(job_id: int, user_id: int):
    """Process a download job in the background"""
    bg_db = database.SessionLocal()
    try:
        dm = downloader_service.DownloadManager(bg_db, user_id)
        await dm.process_download(job_id)
    except Exception as e:
        print(f"Error background: {e}")
    finally:
        bg_db.close()


def _prune_terminal_download_jobs(db: Session, user_id: int, keep: int = DOWNLOAD_JOB_HISTORY_LIMIT):
    terminal_jobs = db.query(database.DownloadJob.id).filter(
        database.DownloadJob.user_id == user_id,
        database.DownloadJob.status.in_(TERMINAL_DOWNLOAD_STATUSES)
    ).order_by(database.DownloadJob.created_at.desc(), database.DownloadJob.id.desc()).all()

    stale_ids = [row.id for row in terminal_jobs[keep:]]
    if not stale_ids:
        return 0

    deleted = db.query(database.DownloadJob).filter(
        database.DownloadJob.user_id == user_id,
        database.DownloadJob.id.in_(stale_ids)
    ).delete(synchronize_session=False)
    db.commit()
    return deleted


# --- ENDPOINTS ---

@router.post("/api/download")
async def trigger_download(
    req: DownloadRequest, 
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db)
):
    """Trigger download from external URL"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    _prune_terminal_download_jobs(db, user.id)
    
    # Pre-check global pool quota for immediate feedback
    pool_usage = manager.get_pool_status(db)
    if pool_usage[0] >= pool_usage[1]:  # used_gb >= limit_gb
        return JSONResponse(
            {"error": f"Global pool limit reached ({pool_usage[1]}GB). Please delete some tracks."}, 
            status_code=403
        )

    resolved_url, resolution_mode = await _resolve_download_url(user, req)

    # Create Job
    job = database.DownloadJob(
        user_id=user.id,
        input_url=resolved_url,
        requested_title=(req.title or "").strip() or None,
        requested_source=_infer_source_label(req.source, req.url),
        status="pending",
        progress_percent=0.0,
        new_playlist_name=None,  # No default playlist - just add to library
        current_file="Queued for download",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    # Trigger Background Task using the correct service
    dm = downloader_service.DownloadManager(db, user.id)
    background_tasks.add_task(dm.process_download, job.id)
    
    message = "Download queued"
    if resolution_mode == "spotify-resolved":
        message = "Download queued using Spotify metadata fallback"
    elif resolution_mode == "youtube-resolved":
        message = "Download queued using cross-provider YouTube matching"

    return {
        "status": "queued",
        "job_id": job.id,
        "url": resolved_url,
        "message": message,
        "resolution_mode": resolution_mode,
    }


@router.get("/api/downloads/status")
async def downloads_status(request: Request, db: Session = Depends(get_db)):
    """Get status of recent downloads"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse([])

    _prune_terminal_download_jobs(db, user.id)

    jobs = db.query(database.DownloadJob).filter(
        database.DownloadJob.user_id == user.id
    ).order_by(database.DownloadJob.created_at.desc()).limit(10).all()

    target_playlist_ids = {
        job.target_playlist_id
        for job in jobs
        if job.target_playlist_id
    }
    legacy_playlist_names = {}
    if target_playlist_ids:
        legacy_playlist_names = {
            row.id: row.name
            for row in db.query(database.UserPlaylist.id, database.UserPlaylist.name).filter(
                database.UserPlaylist.id.in_(target_playlist_ids)
            ).all()
        }

    clean_jobs = []
    for job in jobs:
        target = "General"
        if job.new_playlist_name:
            target = f"✨ {job.new_playlist_name}"
        elif job.target_playlist_id:
            playlist_name = legacy_playlist_names.get(job.target_playlist_id)
            target = f"📂 {playlist_name}" if playlist_name else f"Playlist #{job.target_playlist_id}"

        current_file = os.path.basename(job.current_file) if job.current_file and "/" in job.current_file else job.current_file

        clean_jobs.append({
            "id": job.id,
            "input_url": job.input_url,
            "status": job.status,
            "progress": job.progress_percent,
            "current_file": current_file,
            "error": job.error_log,
            "target": target
        })
    return JSONResponse(clean_jobs)


@router.post("/api/downloads/start")
async def start_download(
    request: Request, background_tasks: BackgroundTasks,
    url: str = Form(...), target_mode: str = Form(...),
    target_playlist_id: int = Form(None), new_playlist_name: str = Form(None),
    is_playlist: str = Form("false"),
    db: Session = Depends(get_db)
):
    """Start download from form submission"""
    user = get_current_user_safe(db, request)
    if not user:
        return RedirectResponse("/login")

    _prune_terminal_download_jobs(db, user.id)
    
    playlist_mode = is_playlist.lower() == "true"
    
    # --- CRITICAL DESTINATION FIX ---
    if new_playlist_name and new_playlist_name.strip():
        target_mode = "new"
    
    if playlist_mode and target_mode != "new" and target_mode != "existing":
        target_mode = "new"
        new_playlist_name = "Playlist Importada"  # Security fallback

    # Clean URL if Single
    clean_url = url
    if not playlist_mode and ("youtube.com" in url or "youtu.be" in url):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if 'v' in query:
            clean_url = f"https://www.youtube.com/watch?v={query['v'][0]}"
        elif parsed.netloc == "youtu.be":
            clean_url = f"https://www.youtube.com/watch?v={parsed.path.lstrip('/')}"

    # Create Job
    job = database.DownloadJob(user_id=user.id, input_url=clean_url, status="pending")
    
    # Assign destination logic
    if target_mode == "existing" and target_playlist_id:
        job.target_playlist_id = target_playlist_id
    elif target_mode == "new" and new_playlist_name:
        job.new_playlist_name = new_playlist_name
    
    db.add(job)
    db.commit()
    db.refresh(job)
    
    background_tasks.add_task(run_download_in_background, job.id, user.id)
    return RedirectResponse("/downloads", status_code=303)


@router.get("/api/jobs")
async def list_download_jobs(request: Request, db: Session = Depends(get_db)):
    """Get status of all download jobs for the user"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    _prune_terminal_download_jobs(db, user.id)
    
    jobs = db.query(database.DownloadJob).filter(
        database.DownloadJob.user_id == user.id
    ).order_by(database.DownloadJob.created_at.desc()).limit(50).all()
    
    return JSONResponse([{
        "id": j.id,
        "url": j.input_url,
        "status": j.status,
        "progress": j.progress_percent,
        "filename": j.current_file,
        "track_title": j.requested_title,
        "source": j.requested_source,
        "detail": j.current_file,
        "error": j.error_log,
        "created_at": str(j.created_at)
    } for j in jobs])

