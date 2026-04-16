"""
Audio streaming and cover art endpoints.
"""
import os
import io
import mimetypes
import random
import logging
from pathlib import Path
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse, StreamingResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import func
from PIL import Image
import mutagen
import httpx

import database
import cover_cache
import metadata_cache
import path_security

from .core import get_db, get_current_user_safe


router = APIRouter()
logger = logging.getLogger(__name__)

MEDIA_ROOTS = ("/saas-data/pool", "/saas-data/users")
CACHE_ROOTS = ("/saas-data/cache", "/saas-data/cover_cache")


def _resolve_allowed_media_path(raw_path: str | None) -> Path | None:
    if not raw_path:
        return None
    for root in MEDIA_ROOTS:
        try:
            path = path_security.resolve_under(raw_path, root)
            if path.exists() and path.is_file():
                return path
        except path_security.UnsafePathError:
            continue
    logger.warning("Blocked media path outside allowed roots: %s", raw_path)
    return None


def _resolve_allowed_cache_path(raw_path: str | Path | None) -> Path | None:
    if not raw_path:
        return None
    for root in CACHE_ROOTS:
        try:
            path = path_security.resolve_under(raw_path, root)
            if path.exists() and path.is_file():
                return path
        except path_security.UnsafePathError:
            continue
    logger.warning("Blocked cache path outside allowed roots: %s", raw_path)
    return None


def _cover_metadata_key(artist: str, title: str) -> str:
    return metadata_cache.make_key("cover-proxy", artist=artist, title=title)


def _pick_random_track(db: Session):
    bounds = db.query(
        func.min(database.Track.id),
        func.max(database.Track.id),
    ).one()
    min_id, max_id = bounds
    if min_id is None or max_id is None:
        return None

    pivot = random.randint(min_id, max_id)
    track = db.query(database.Track).filter(database.Track.id >= pivot).order_by(database.Track.id.asc()).first()
    if track:
        return track
    return db.query(database.Track).filter(database.Track.id < pivot).order_by(database.Track.id.asc()).first()


@router.get("/api/cover/{track_id:int}")
async def get_cover(track_id: int, db: Session = Depends(get_db)):
    """Extract cover art from ID3 tags with disk caching"""
    # 1. Check Cache First (using cover_cache module)
    cached = cover_cache.get_cached_cover(track_id)
    cached = _resolve_allowed_cache_path(cached)
    if cached:
        return FileResponse(str(cached))

    # 2. Extract if not cached
    track = db.query(database.Track).filter(database.Track.id == track_id).first()
    media_path = _resolve_allowed_media_path(track.filepath if track else None)
    if not track or not media_path:
        return RedirectResponse("/static/img/default_cover.png")
        
    try:
        audio = mutagen.File(str(media_path))
        cover_data = None
        
        # ID3 v2.3+
        if audio and 'APIC:' in audio:
            cover_data = audio['APIC:'].data
        else:
            # Fallback scan
            for key in audio.keys():
                if key.startswith('APIC'):
                    cover_data = audio[key].data
                    break
        
        if cover_data:
            # Resize and optimize
            img = Image.open(io.BytesIO(cover_data))
            img.thumbnail((400, 400))  # Reasonable size for web
            img = img.convert("RGB")
            
            # Save to cache using cover_cache module
            img_bytes = io.BytesIO()
            img.save(img_bytes, "JPEG", quality=80)
            cache_path = cover_cache.cache_cover(track_id, img_bytes.getvalue())
            return FileResponse(str(cache_path))

    except Exception as e:
        logger.warning("Error extracting cover for %s: %s", track_id, e)
        
    # Redirect to default
    return RedirectResponse("/static/img/default_cover.png")


@router.get("/api/stream/{track_id}")
async def stream_track(track_id: int, request: Request, db: Session = Depends(get_db)):
    """Stream local audio file with Range support (Essential for correct duration/seeking)"""
    track = db.query(database.Track).filter(database.Track.id == track_id).first()
    file_path = _resolve_allowed_media_path(track.filepath if track else None)
    if not track or not file_path:
        return Response(status_code=404)
        
    file_size = file_path.stat().st_size
    content_type, _ = mimetypes.guess_type(str(file_path))
    content_type = content_type or "audio/mpeg"
    
    # Handle Range Header
    range_header = request.headers.get("range")
    if not range_header:
        # No range: Serve full file
        def iterfile():
            with file_path.open("rb") as f:
                yield from f
        return StreamingResponse(
            iterfile(), 
            media_type=content_type,
            headers={"Content-Length": str(file_size), "Accept-Ranges": "bytes"}
        )
    
    # Range Requested
    try:
        start, end = range_header.replace("bytes=", "").split("-")
        start = int(start)
        end = int(end) if end else file_size - 1
        if start < 0 or end < start or start >= file_size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
        end = min(end, file_size - 1)
        chunk_size = (end - start) + 1
        
        def iterfile():
            with file_path.open("rb") as f:
                f.seek(start)
                yield f.read(chunk_size)
                
        return StreamingResponse(
            iterfile(),
            status_code=206,
            media_type=content_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(chunk_size)
            }
        )
    except Exception as e:
        logger.warning("Range error for track %s: %s", track_id, e)
        # Fallback to full file
        def iterfile():
            with file_path.open("rb") as f:
                yield from f
        return StreamingResponse(iterfile(), media_type=content_type)


@router.get("/api/random-track")
async def get_random_track(db: Session = Depends(get_db)):
    """Return a random track from the local library."""
    track = _pick_random_track(db)
    if not track:
        return JSONResponse({"error": "Library is empty"}, status_code=404)
    
    return {
        "id": track.id,
        "db_id": track.id,
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "thumbnail": f"/api/cover/{track.id}",
        "is_local": True,
        "source": "local"
    }


@router.get("/api/cover/resolve")
async def resolve_cover(request: Request, artist: str = "", title: str = "", db: Session = Depends(get_db)):
    """
    Cover art resolution proxy. Tries multiple sources to find cover art.
    Used when Last.fm/MusicBrainz don't provide images directly.
    Results are cached on disk and in SQLite metadata cache.
    """
    if not artist and not title:
        return RedirectResponse("/static/img/default_cover.png")

    import hashlib
    import time

    cache_key = hashlib.md5(f"{artist}:{title}".lower().encode()).hexdigest()
    cache_dir = "/saas-data/cache/covers"
    os.makedirs(cache_dir, exist_ok=True)
    cached_path = os.path.join(cache_dir, f"{cache_key}.jpg")
    neg_cache = os.path.join(cache_dir, f"{cache_key}.nocover")
    metadata_key = _cover_metadata_key(artist, title)

    if os.path.exists(cached_path):
        metadata_cache.set(metadata_key, {"negative": False, "provider": "disk-cache"})
        return FileResponse(
            cached_path,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=604800"},
        )

    cached_metadata = metadata_cache.get(metadata_key)
    if cached_metadata:
        if cached_metadata.get("negative"):
            return RedirectResponse("/static/img/default_cover.png")

        cached_image_url = (cached_metadata.get("image_url") or "").strip()
        if cached_image_url:
            try:
                async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                    resp = await client.get(cached_image_url)
                    if resp.status_code == 200 and len(resp.content) > 500:
                        img = Image.open(io.BytesIO(resp.content))
                        img.thumbnail((400, 400))
                        img = img.convert("RGB")
                        img_bytes = io.BytesIO()
                        img.save(img_bytes, "JPEG", quality=80)
                        with open(cached_path, "wb") as f:
                            f.write(img_bytes.getvalue())
                        return FileResponse(
                            cached_path,
                            media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=604800"},
                        )
            except Exception as e:
                print(f"[COVER-RESOLVE] Cached image download failed: {e}")

    if os.path.exists(neg_cache):
        stat = os.stat(neg_cache)
        if time.time() - stat.st_mtime < 86400:
            metadata_cache.set(metadata_key, {"negative": True, "provider": "file-negative-cache"})
            return RedirectResponse("/static/img/default_cover.png")

    user = get_current_user_safe(db, request) if request else None
    image_url = None
    provider = "unknown"

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        if user and user.download_settings:
            settings = user.download_settings
            if settings.spotify_client_id and settings.spotify_client_secret:
                try:
                    import spotify_service

                    query = f"track:{title} artist:{artist}" if title else f"artist:{artist}"
                    sp = await spotify_service.spotify_service.search_item(
                        settings.spotify_client_id,
                        settings.spotify_client_secret,
                        query,
                        type="track",
                        limit=1,
                    )
                    if not sp:
                        fallback_query = f"{artist} {title}".strip()
                        sp = await spotify_service.spotify_service.search_item(
                            settings.spotify_client_id,
                            settings.spotify_client_secret,
                            fallback_query,
                            type="track",
                            limit=1,
                        )
                    if sp and sp.get("image"):
                        image_url = sp["image"]
                        provider = "spotify"
                except Exception as e:
                    print(f"[COVER-RESOLVE] Spotify lookup failed: {e}")

        if not image_url and user and user.download_settings:
            lastfm_key = getattr(user.download_settings, "lastfm_api_key", None)
            if lastfm_key and title and artist:
                try:
                    from lastfm_service import lastfm_service as lfm_svc

                    info = await lfm_svc.get_track_info(lastfm_key, artist, title)
                    if info and info.get("image"):
                        image_url = info["image"]
                        provider = "lastfm"
                except Exception as e:
                    print(f"[COVER-RESOLVE] Last.fm lookup failed: {e}")

        if not image_url:
            try:
                from musicbrainz_service import musicbrainz_service as mb_svc

                query = f"{artist} {title}".strip()
                results = await mb_svc.search_recordings(query, limit=1)
                if results and results[0].get("image"):
                    test_url = results[0]["image"]
                    resp = await client.head(test_url)
                    if resp.status_code == 200:
                        image_url = test_url
                        provider = "musicbrainz"
            except Exception as e:
                print(f"[COVER-RESOLVE] MusicBrainz lookup failed: {e}")

        if not image_url:
            try:
                import youtube_service as yt_svc

                yt_results = await yt_svc.youtube_service.search_videos(
                    f"{artist} {title} official",
                    limit=1,
                )
                if yt_results:
                    vid_id = yt_results[0].get("id")
                    if vid_id:
                        image_url = f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
                        provider = "youtube"
            except Exception as e:
                print(f"[COVER-RESOLVE] YouTube lookup failed: {e}")

        if image_url:
            try:
                resp = await client.get(image_url)
                if resp.status_code == 200 and len(resp.content) > 500:
                    img = Image.open(io.BytesIO(resp.content))
                    img.thumbnail((400, 400))
                    img = img.convert("RGB")
                    img_bytes = io.BytesIO()
                    img.save(img_bytes, "JPEG", quality=80)

                    with open(cached_path, "wb") as f:
                        f.write(img_bytes.getvalue())

                    metadata_cache.set(
                        metadata_key,
                        {"image_url": image_url, "provider": provider, "negative": False},
                    )

                    return FileResponse(
                        cached_path,
                        media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=604800"},
                    )
            except Exception as e:
                print(f"[COVER-RESOLVE] Download/cache failed: {e}")

    try:
        with open(neg_cache, "w") as f:
            f.write("")
    except Exception:
        pass

    metadata_cache.set(metadata_key, {"negative": True, "provider": "none"})
    return RedirectResponse("/static/img/default_cover.png")

