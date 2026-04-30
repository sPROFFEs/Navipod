"""
Artist view + smart-radio endpoints.

The artist view uses cached external data from `artist_service`
(TTL 7d/14d). Smart radio is intentionally LIBRARY-ONLY: it builds
queues from tracks already in the user's local library, using
Last.fm similar-artists data as a *hint* for which library tracks
to surface. We do not return ytsearch1: pseudo-IDs because resolving
them at playback time was unreliable and broke the player.
"""

import logging
import random
from collections import defaultdict

import database
from artist_service import get_artist_view, get_radio_seeds
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func
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


def _track_to_dict(t: database.Track) -> dict:
    return {
        "id": t.source_id or f"local:{t.id}",
        "db_id": t.id,
        "title": t.title,
        "artist": t.artist,
        "album": t.album,
        "thumbnail": f"/api/cover/{t.id}",
        "is_local": True,
        "source": "local",
    }


@router.get("/api/radio/track")
async def smart_radio(
    request: Request,
    db: Session = Depends(get_db),
    artist: str = Query(...),
    title: str = Query(...),
    limit: int = Query(30, ge=1, le=60),
):
    """Build a smart-radio queue from LOCAL library tracks only.

    Strategy:
      1. Use Last.fm similar-artists (cached 7d via artist_service) to
         get a ranked list of "neighbor" artists.
      2. Match those names against the user's local library
         (case-insensitive).
      3. Pull a handful of tracks per matched neighbor, plus a tail of
         the seed artist's own catalog, plus a small random sample
         from the broader library to keep things fresh.

    All returned items are real local tracks with db_id, so the existing
    streaming pipeline plays them with no extra resolution step. If the
    library is small or has no matching neighbors we fall back to a
    random sample so the user always gets *something* playable."""

    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    settings = user.download_settings
    lastfm_key = getattr(settings, "lastfm_api_key", None) if settings else None

    # Last.fm gives us a ranked list of similar artists. We use it only
    # to *order* the local-library queue — we don't pull external
    # tracks because those weren't reliably playable.
    neighbor_seeds = []
    try:
        neighbor_seeds = await get_radio_seeds(
            artist=artist,
            title=title,
            lastfm_api_key=lastfm_key,
            fallback_seed_artist=artist,
        )
    except Exception as e:
        logger.debug("Last.fm seeds lookup failed for radio %s — %s: %s", artist, title, e)

    neighbor_artists = []
    seen_names = set()
    for s in neighbor_seeds:
        name = (s.get("artist") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        neighbor_artists.append(name)

    # Build the queue from the local DB.
    queue: list[dict] = []
    used_ids: set[int] = set()

    seed_key_artist = (artist or "").strip().lower()
    seed_key_title = (title or "").strip().lower()

    def _add_track(t: database.Track) -> bool:
        if not t or t.id in used_ids:
            return False
        # Skip the exact seed track so we don't echo it back.
        if (t.title or "").strip().lower() == seed_key_title and \
           (t.artist or "").strip().lower() == seed_key_artist:
            return False
        used_ids.add(t.id)
        queue.append(_track_to_dict(t))
        return True

    # 1. A few more tracks from the seed artist itself — keeps the
    # opening tracks tonally consistent with what the user clicked.
    if artist:
        own = (
            db.query(database.Track)
            .filter(database.Track.artist.ilike(artist))
            .order_by(func.random())
            .limit(6)
            .all()
        )
        for t in own:
            _add_track(t)

    # 2. Neighbor artists, in Last.fm-similarity order.
    for name in neighbor_artists:
        if len(queue) >= limit:
            break
        rows = (
            db.query(database.Track)
            .filter(database.Track.artist.ilike(name))
            .order_by(func.random())
            .limit(4)
            .all()
        )
        for t in rows:
            if len(queue) >= limit:
                break
            _add_track(t)

    # 3. Top-up: random library tracks. Guarantees a non-empty queue
    # for libraries with no metadata overlap with Last.fm's corpus.
    if len(queue) < limit:
        needed = limit - len(queue)
        # Heuristic over-fetch — random() doesn't guarantee uniqueness
        # against `used_ids`, so grab a few extras and filter.
        randoms = (
            db.query(database.Track)
            .order_by(func.random())
            .limit(needed * 3)
            .all()
        )
        for t in randoms:
            if len(queue) >= limit:
                break
            _add_track(t)

    # Light shuffle of the tail to avoid a long monotonous run of one
    # artist. Keep the first 3 in place so the radio "starts" near the
    # seed before drifting.
    if len(queue) > 5:
        head = queue[:3]
        tail = queue[3:]
        random.shuffle(tail)
        queue = head + tail

    return JSONResponse({
        "seed": {"title": title, "artist": artist},
        "tracks": queue[:limit],
        "total": len(queue),
    })
