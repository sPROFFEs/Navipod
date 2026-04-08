"""
Unified search: local library plus remote providers.
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel

import database
import spotify_service
import youtube_service
import metadata_service
import metadata_cache
from lastfm_service import lastfm_service
from musicbrainz_service import musicbrainz_service
from limiter import limiter
from search_utils import build_fts_query, spotify_source_candidates, youtube_source_candidates

from .core import get_db, get_current_user_safe


router = APIRouter()


def _search_local_tracks(db: Session, raw_query: str, limit: int = 50):
    fts_query = build_fts_query(raw_query)
    if fts_query:
        try:
            rows = db.execute(
                text("SELECT rowid FROM tracks_fts WHERE tracks_fts MATCH :query LIMIT :limit"),
                {"query": fts_query, "limit": limit},
            ).fetchall()
            track_ids = [row[0] for row in rows]
            if track_ids:
                tracks = db.query(database.Track).filter(database.Track.id.in_(track_ids)).all()
                track_map = {track.id: track for track in tracks}
                return [track_map[track_id] for track_id in track_ids if track_id in track_map]
            return []
        except Exception:
            pass

    return db.query(database.Track).filter(
        (database.Track.title.ilike(f"%{raw_query}%")) |
        (database.Track.artist.ilike(f"%{raw_query}%"))
    ).limit(limit).all()


def _fetch_existing_source_ids(db: Session, candidate_ids: set[str]) -> set[str]:
    normalized = {candidate for candidate in candidate_ids if candidate}
    if not normalized:
        return set()
    rows = db.query(database.Track.source_id).filter(database.Track.source_id.in_(normalized)).all()
    return {row[0] for row in rows if row and row[0]}
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
    local_tracks = _search_local_tracks(db, q, limit=50) if source in ["all", "local"] else []
    
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
            youtube_existing_ids = _fetch_existing_source_ids(
                db,
                {
                    candidate
                    for item in yt_items
                    for candidate in youtube_source_candidates(item.get("id"))
                },
            )
            for item in yt_items:
                vid = item['id']
                if not youtube_source_candidates(vid).intersection(local_ids | youtube_existing_ids):
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
                spotify_existing_ids = _fetch_existing_source_ids(
                    db,
                    {
                        candidate
                        for item in sp_items
                        for candidate in spotify_source_candidates(item.get("id") if isinstance(item.get("id"), str) else "")
                    },
                )
                for item in sp_items:
                    item_id = item.get("id")
                    normalized_id = item_id if isinstance(item_id, str) else ""
                    source_name = "spotify"
                    dedup_id = normalized_id
                    if normalized_id and not normalized_id.startswith("spotify:track:"):
                        dedup_id = f"spotify:track:{normalized_id}"

                    if dedup_id and dedup_id not in (local_ids | spotify_existing_ids):
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
