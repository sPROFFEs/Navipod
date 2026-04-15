"""
Sync state and heartbeat for multi-user scenarios.
"""
import hashlib
import re
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse, Response, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session
import httpx

import database
import spotify_service
import youtube_service
import track_identity

from .core import get_db, get_current_user_safe
from .favorites import schedule_navidrome_sync


router = APIRouter()


@router.get("/api/sync-state")
async def get_sync_state(request: Request, db: Session = Depends(get_db)):
    """
    Lightweight heartbeat endpoint for UI sync.
    Returns counts and a version hash. Frontend polls this and only
    refetches full data when the version changes.
    """
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        # Get counts
        fav_count = db.query(database.UserFavorite).filter(
            database.UserFavorite.user_id == user.id
        ).count()
        
        playlist_count = db.query(database.Playlist).filter(
            database.Playlist.owner_id == user.id
        ).count()
        
        # Get favorite IDs for quick state comparison
        fav_ids = db.query(database.UserFavorite.track_id).filter(
            database.UserFavorite.user_id == user.id
        ).all()
        fav_id_list = sorted([f[0] for f in fav_ids])
        
        # Get playlist data including item counts for change detection
        playlists = db.query(database.Playlist).filter(
            database.Playlist.owner_id == user.id
        ).all()
        
        playlist_state = []
        for p in playlists:
            item_count = len(p.items) if p.items else 0
            playlist_state.append((p.id, p.name, item_count))
        playlist_state = sorted(playlist_state)
        
        # Create version hash
        state_str = f"favs:{fav_id_list}|playlists:{playlist_state}"
        version_hash = hashlib.md5(state_str.encode()).hexdigest()[:12]
        
        return JSONResponse({
            "fav_count": fav_count,
            "fav_ids": fav_id_list,
            "playlist_count": playlist_count,
            "version": version_hash
        })
        
    except Exception as e:
        print(f"[SYNC-STATE] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/sync-refresh")
async def queue_sync_refresh(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    schedule_navidrome_sync(user.id, user.username, delay_seconds=0.5)
    return JSONResponse({"status": "queued"})


@router.get("/api/check-duplicate")
async def check_duplicate(url: str, request: Request, db: Session = Depends(get_db)):
    """
    Check if a URL already exists in the library before downloading.
    """
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    source_id = track_identity.extract_source_id_from_url(url)
    if not source_id:
        return JSONResponse({"exists": False})
    
    existing = db.query(database.Track).filter(database.Track.source_id == source_id).first()
    if existing:
        return JSONResponse({
            "exists": True,
            "track": {
                "id": existing.id,
                "title": existing.title,
                "artist": existing.artist
            }
        })
    
    return JSONResponse({"exists": False})


@router.get("/api/playback/preview")
async def preview_playback(request: Request, url: str = None, title: str = None, spotify_id: str = None, db: Session = Depends(get_db)):
    """
    Hybrid Preview Handler:
    1. Spotify: Direct Redirect (CDN allows CORS) - Best performance
    2. YouTube: Streaming Proxy (CDN blocks CORS) - Bypasses restrictions
    """
    user = get_current_user_safe(db, request)
    # Allow previews for auth users only
    if not user:
        return Response(status_code=401)

    # --- STRATEGY 1: SPOTIFY (Direct Redirect) ---
    if spotify_id or (url and "spotify" in url):
        try:
            # Extract ID if url provided
            sid = spotify_id
            if not sid and url:
                sid = url.split("track/")[-1].split("?")[0]

            preview_url = await spotify_service.spotify_service.get_embed_preview(sid)
            if preview_url:
                # Spotify CDN allows remote playback, so redirect is fine/better
                return RedirectResponse(preview_url)
        except Exception:
            pass  # Fallback to YouTube if Spotify fails

    # --- STRATEGY 2: YOUTUBE (Streaming Proxy) ---
    target_url = None
    
    # A. Direct URL
    if url and ("youtube.com" in url or "youtu.be" in url):
        try:
            video_id = url.split("v=")[-1].split("&")[0] if "v=" in url else url.split("/")[-1]
            target_url = await youtube_service.youtube_service.get_audio_stream_url(video_id)
        except:
            pass

    # B. Search Fallback (Title)
    elif title:
        try:
            # Clean title
            clean_query = title
            if spotify_id:
                clean_query += " audio"  # Hint for better match
            
            yt_res = await youtube_service.youtube_service.search_videos(clean_query, limit=1)
            if yt_res:
                target_url = await youtube_service.youtube_service.get_audio_stream_url(yt_res[0]['id'])
        except:
            pass

    if not target_url:
        return Response(content="Preview not found", status_code=404)

    # C. Proxy Streamer (Fixes 403 Header Issues)
    async def stream_generator():
        # Headers to trick YouTube/GoogleVideo into thinking we are a browser/player
        upstream_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.youtube.com/",
        }

        # Use a longer timeout for the initial connection to YouTube CDN
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            try:
                async with client.stream("GET", target_url, headers=upstream_headers) as resp:
                    if resp.status_code >= 400:
                        print(f"[PREVIEW-PROXY] Remote error {resp.status_code} for {target_url[:60]}...")
                        return
                    
                    async for chunk in resp.aiter_bytes(chunk_size=16384):
                        yield chunk
            except (httpx.RequestError, OSError, RuntimeError, GeneratorExit) as e:
                # Client disconnected or network error - suppress noise
                pass
            except Exception as e:
                print(f"[PREVIEW-PROXY] Proxy runtime error: {e}")

    return StreamingResponse(stream_generator(), media_type="audio/mpeg", headers={
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive"
    })
