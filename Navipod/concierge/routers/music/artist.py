"""
Artist view + smart-radio endpoints.

These endpoints prefer cached data from `artist_service` (TTL 7d/14d).
External API calls only fire when the cache is cold for a given
(artist) or (artist, title) tuple — so even a busy instance hits
Last.fm/Spotify a few times per artist per week, not per request.
"""

import logging
from collections import defaultdict

import database
from artist_service import get_artist_view, get_radio_seeds
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .core import get_current_user_safe, get_db
from .recommendations import get_user_country

router = APIRouter()
logger = logging.getLogger(__name__)


def _local_tracks_for_artist(db: Session, user, artist_name: str) -> list[dict]:
    """All library tracks whose artist field matches (case-insensitive).
    Fast — there's an index on artist; returns list ordered by album."""
    if not artist_name:
        return []
    rows = (
        db.query(database.Track)
        .filter(database.Track.artist.ilike(artist_name))
        .order_by(database.Track.album.asc(), database.Track.title.asc())
        .all()
    )
    out = []
    for t in rows:
        out.append({
            "id": t.source_id or f"local:{t.id}",
            "db_id": t.id,
            "title": t.title,
            "artist": t.artist,
            "album": t.album,
            "thumbnail": f"/api/cover/{t.id}",
            "is_local": True,
            "source": "local",
        })
    return out


def _group_local_albums(local_tracks: list[dict]) -> dict[str, list[dict]]:
    """Group local tracks by album so we can mark which albums the user
    already has and how many tracks are missing from each."""
    by_album: dict[str, list[dict]] = defaultdict(list)
    for t in local_tracks:
        key = (t.get("album") or "").strip().lower()
        if not key:
            key = "__no_album__"
        by_album[key].append(t)
    return by_album


@router.get("/api/artist/{name}")
async def get_artist(name: str, request: Request, db: Session = Depends(get_db)):
    """Artist detail view: local tracks, full discography (with
    have/missing counts), similar artists, top tracks, bio."""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    settings = user.download_settings
    spotify_id = getattr(settings, "spotify_client_id", None) if settings else None
    spotify_secret = getattr(settings, "spotify_client_secret", None) if settings else None
    lastfm_key = getattr(settings, "lastfm_api_key", None) if settings else None

    country = get_user_country(request)

    artist_data = await get_artist_view(
        name,
        spotify_client_id=spotify_id,
        spotify_client_secret=spotify_secret,
        lastfm_api_key=lastfm_key,
        country=country,
    )

    local_tracks = _local_tracks_for_artist(db, user, name)
    local_by_album = _group_local_albums(local_tracks)

    # Annotate each Spotify album with how many of its tracks the user
    # already has locally. Powers the "Complete this album" CTA.
    annotated_albums = []
    for alb in artist_data.get("albums", []):
        key = (alb.get("name") or "").strip().lower()
        owned = local_by_album.get(key, [])
        missing = max(0, (alb.get("total_tracks") or 0) - len(owned))
        annotated_albums.append({
            **alb,
            "owned_count": len(owned),
            "missing_count": missing,
            "fully_owned": (alb.get("total_tracks") or 0) > 0 and missing == 0,
        })

    return JSONResponse({
        "name": artist_data.get("name") or name,
        "info": artist_data.get("info") or {},
        "spotify": artist_data.get("spotify"),
        "albums": annotated_albums,
        "similar": artist_data.get("similar") or [],
        "top_tracks": artist_data.get("top_tracks") or [],
        "local_tracks": local_tracks,
        "local_album_count": len([k for k in local_by_album if k != "__no_album__"]),
    })


@router.get("/api/radio/track")
async def smart_radio(
    request: Request,
    db: Session = Depends(get_db),
    artist: str = Query(...),
    title: str = Query(...),
    limit: int = Query(30, ge=1, le=60),
):
    """Build a smart-radio queue starting from a seed track. Returns a
    list of (title, artist) seeds — the frontend resolves each to a
    playable source via the existing search/streaming pipeline so we
    don't duplicate that logic here."""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    settings = user.download_settings
    lastfm_key = getattr(settings, "lastfm_api_key", None) if settings else None

    seeds = await get_radio_seeds(
        artist=artist,
        title=title,
        lastfm_api_key=lastfm_key,
        fallback_seed_artist=artist,
    )

    # Filter out the seed track itself so the radio doesn't open with
    # the song the user just clicked.
    seed_key = f"{title.strip().lower()}|{artist.strip().lower()}"
    filtered = [
        s for s in seeds
        if f"{(s['title'] or '').strip().lower()}|{(s['artist'] or '').strip().lower()}" != seed_key
    ]

    return JSONResponse({
        "seed": {"title": title, "artist": artist},
        "seeds": filtered[:limit],
        "total": len(filtered),
    })
