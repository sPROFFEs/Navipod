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


# --- PYDANTIC MODELS ---

class DownloadRequest(BaseModel):
    url: str
    title: str = None
    artist: str = None
    album: str = None
    source: str = None


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

    jobs = db.query(database.DownloadJob).filter(
        database.DownloadJob.user_id == user.id
    ).order_by(database.DownloadJob.created_at.desc()).limit(10).all()
    
    clean_jobs = []
    for j in jobs:
        target = "General"
        # PRIORITY 1: If a new folder was created, show its name
        if j.new_playlist_name: 
            target = f"✨ {j.new_playlist_name}"
        # PRIORITY 2: If added to existing, look up its name
        elif j.target_playlist_id:
            pl = db.query(database.UserPlaylist).filter(
                database.UserPlaylist.id == j.target_playlist_id
            ).first()
            target = f"📂 {pl.name}" if pl else f"Playlist #{j.target_playlist_id}"
        
        # Clean filename (remove full path)
        current_file = os.path.basename(j.current_file) if j.current_file and "/" in j.current_file else j.current_file

        clean_jobs.append({
            "id": j.id, 
            "input_url": j.input_url, 
            "status": j.status,
            "progress": j.progress_percent, 
            "current_file": current_file,
            "error": j.error_log, 
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
    
    jobs = db.query(database.DownloadJob).filter(
        database.DownloadJob.user_id == user.id
    ).order_by(database.DownloadJob.created_at.desc()).limit(50).all()
    
    return JSONResponse([{
        "id": j.id,
        "url": j.input_url,
        "status": j.status,
        "progress": j.progress_percent,
        "filename": j.current_file,
        "detail": j.current_file,
        "error": j.error_log,
        "created_at": str(j.created_at)
    } for j in jobs])

