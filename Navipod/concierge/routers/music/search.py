"""
Unified search: local library plus remote providers.
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel

import database
import spotify_service
import youtube_service
import metadata_service
import metadata_cache
from lastfm_service import lastfm_service
from musicbrainz_service import musicbrainz_service
from limiter import limiter

from .core import get_db, get_current_user_safe


router = APIRouter()


def _cover_proxy(artist: str, title: str) -> str:
    return f"/api/cover/resolve?artist={artist}&title={title}"


class MetadataResolveRequest(BaseModel):
    title: str = ""
    artist: str = ""
    album: str = ""


@router.get("/api/search")
@limiter.limit("30/minute")
async def unified_search(request: Request, q: str = "", source: str = "all", db: Session = Depends(get_db)):
    """
    Unified Search: Local DB -> Remote (YouTube/Spotify)
    Rate limited to 30 requests per minute per IP.
    """
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse([], status_code=401)
    if not q:
        return JSONResponse([])

    source = (source or "all").lower()
    if source != "local":
        metadata_cache.ensure_available()
    results = []
    local_ids = set()

    # 1. LOCAL SEARCH (Database)
    # Keep this query for deduplication even when the active tab is remote-only.
    local_tracks = db.query(database.Track).filter(
        (database.Track.title.ilike(f"%{q}%")) | 
        (database.Track.artist.ilike(f"%{q}%"))
    ).limit(50).all()
    
    for t in local_tracks:
        if source in ["all", "local"]:
            res = {
                "id": t.source_id if t.source_id else f"local:{t.id}",
                "db_id": t.id,
                "title": t.title,
                "artist": t.artist,
                "album": t.album,
                "duration": t.duration,
                "thumbnail": f"/api/cover/{t.id}",
                "source": "local",
                "is_local": True,
                "file_hash": t.file_hash
            }
            results.append(res)
        if t.source_id:
            local_ids.add(t.source_id)

    # 2. REMOTE SEARCH (If requested)
    remote_results = []
    
    # YouTube Search
    if source in ["all", "youtube"]:
        try:
            yt_items = await youtube_service.youtube_service.search_videos(q, limit=20)
            for item in yt_items:
                # Normalizar ID para cotejar con DB
                vid = item['id']
                if vid not in local_ids and f"youtube:{vid}" not in local_ids:
                    remote_results.append({
                        "id": item.get('url') or f"https://www.youtube.com/watch?v={item['id']}",
                        "title": item.get('title', 'Unknown'),
                        "artist": item.get('artist', 'YouTube'),
                        "album": "YouTube",
                        "thumbnail": item.get('image', '/static/img/default_cover.png'),
                        "is_local": False,
                        "source": "youtube"
                    })
        except Exception as e:
            print(f"[Unified Search] YouTube Error: {e}")

    # Spotify Search (Optional, needs auth)
    if source in ["all", "spotify"]:
        try:
            settings = user.download_settings
            if settings and settings.spotify_client_id and settings.spotify_client_secret:
                sp_items = await spotify_service.spotify_service.search_tracks(
                    settings.spotify_client_id,
                    settings.spotify_client_secret,
                    q,
                    type="track",
                    limit=20,
                )
                for item in sp_items:
                    item_id = item.get("id")
                    normalized_id = item_id if isinstance(item_id, str) else ""
                    source_name = "spotify"
                    dedup_id = normalized_id
                    if normalized_id and not normalized_id.startswith("spotify:track:"):
                        dedup_id = f"spotify:track:{normalized_id}"

                    if dedup_id and dedup_id not in local_ids:
                        title = item.get('name') or item.get('title') or 'Unknown'
                        artist = item.get('artist', 'Unknown')
                        album = item.get('album') or source_name.title()
                        remote_id = item.get('url') or f"https://open.spotify.com/track/{item.get('id')}"

                        remote_results.append({
                            "id": remote_id,
                            "title": title,
                            "artist": artist,
                            "album": album,
                            "thumbnail": item.get('image', '/static/img/default_cover.png'),
                            "preview_url": item.get('preview_url'),
                            "is_local": False,
                            "source": source_name
                        })
        except Exception as e:
            print(f"[Unified Search] Spotify Error: {e}")

    # Last.fm Search
    if source in ["all", "lastfm"]:
        try:
            settings = user.download_settings
            lastfm_key = getattr(settings, "lastfm_api_key", None) if settings else None
            if lastfm_key:
                lfm_items = await lastfm_service.search_tracks(lastfm_key, q, limit=20)
                for item in lfm_items:
                    title = item.get('name') or item.get('title') or 'Unknown'
                    artist = item.get('artist', 'Unknown')
                    dedup_id = item.get('id', '')
                    if dedup_id and dedup_id not in local_ids:
                        # Use proxy for cover art (Last.fm track.search images are often blank)
                        thumbnail = item.get('image') or _cover_proxy(artist, title)
                        remote_results.append({
                            "id": f"ytsearch1:{artist} {title} official audio",
                            "title": title,
                            "artist": artist,
                            "album": item.get('album') or "Last.fm",
                            "thumbnail": thumbnail,
                            "is_local": False,
                            "source": "lastfm"
                        })
        except Exception as e:
            print(f"[Unified Search] Last.fm Error: {e}")

    # MusicBrainz Search
    if source in ["all", "musicbrainz"]:
        try:
            settings = user.download_settings
            mb_items = await musicbrainz_service.search_recordings(q, limit=20)
            for item in mb_items:
                title = item.get('name') or 'Unknown'
                artist = item.get('artist', 'Unknown')
                dedup_id = item.get('id', '')
                if dedup_id and dedup_id not in local_ids:
                    thumbnail = _cover_proxy(artist, title)
                    remote_results.append({
                        "id": f"ytsearch1:{artist} {title} official audio",
                        "title": title,
                        "artist": artist,
                        "album": item.get('album') or "MusicBrainz",
                        "thumbnail": thumbnail,
                        "is_local": False,
                        "source": "musicbrainz"
                    })
        except Exception as e:
            print(f"[Unified Search] MusicBrainz Error: {e}")

    return JSONResponse(results + remote_results)


@router.get("/api/search/{source}")
async def api_search(source: str, q: str, request: Request, db: Session = Depends(get_db)):
    """Search specific source (spotify or youtube)"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse([], status_code=401)
    
    if not q:
        return JSONResponse([])

    try:
        if source != "local":
            metadata_cache.ensure_available()
        if source == "spotify":
            settings = user.download_settings
            if not settings or not settings.spotify_client_id or not settings.spotify_client_secret:
                return JSONResponse({"error": "No providers configured"}, status_code=400)
            
            items = await spotify_service.spotify_service.search_tracks(
                settings.spotify_client_id,
                settings.spotify_client_secret,
                q,
                type="track",
                limit=20,
            )
            return JSONResponse(items)

        elif source == "youtube":
            items = await youtube_service.youtube_service.search_videos(q, limit=20)
            return JSONResponse(items)

        elif source == "lastfm":
            settings = user.download_settings
            lastfm_key = getattr(settings, "lastfm_api_key", None) if settings else None
            if not lastfm_key:
                return JSONResponse({"error": "Last.fm API key not configured"}, status_code=400)
            items = await lastfm_service.search_tracks(lastfm_key, q, limit=20)
            return JSONResponse(items)

        elif source == "musicbrainz":
            items = await musicbrainz_service.search_recordings(q, limit=20)
            return JSONResponse(items)
            
    except Exception as e:
        print(f"[SEARCH-ERROR] {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse([])


@router.post("/api/metadata/resolve")
async def resolve_metadata(payload: MetadataResolveRequest, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    settings = user.download_settings
    data = await metadata_service.enrich_metadata(
        settings=settings,
        title=payload.title,
        artist=payload.artist,
        album=payload.album,
    )
    return JSONResponse(data)
