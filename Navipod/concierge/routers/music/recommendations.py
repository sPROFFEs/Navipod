"""
Recommendations engine: Spotify, YouTube, Last.fm, MusicBrainz, and local.
"""

import json
import logging
import os
import random
import time

import database
import manager
import spotify_service
import youtube_service
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from http_client import http_client
from lastfm_service import lastfm_service
from musicbrainz_service import musicbrainz_service
from sqlalchemy.orm import Session

from .core import get_current_user_safe, get_db

router = APIRouter()
logger = logging.getLogger(__name__)


# 48-hour cache for recommendations (in seconds)
RECS_CACHE_TTL = 48 * 3600
RECS_CACHE_DIR = "/saas-data/cache"


# --- HELPER FUNCTIONS ---


def get_user_country(request: Request) -> str:
    """Detect user country from headers"""
    # 1. Cloudflare Priority
    cf_country = request.headers.get("cf-ipcountry")
    if cf_country and cf_country != "XX":
        return cf_country

    # 2. Accept-Language Fallback
    accept_lang = request.headers.get("accept-language")
    if accept_lang:
        try:
            primary = accept_lang.split(",")[0].split(";")[0]
            if "-" in primary:
                return primary.split("-")[1].upper()
            else:
                lang_map = {"es": "ES", "en": "US", "fr": "FR", "de": "DE", "it": "IT", "pt": "PT"}
                return lang_map.get(primary.lower(), "US")
        except Exception as e:
            logger.debug("Accept-Language country parse failed for %s: %s", accept_lang, e)

    return "US"


async def get_navidrome_top_songs(user, db: Session, limit: int = 5):
    """Get most played songs from Navidrome"""
    try:
        target_ip = manager.get_or_spawn_container(user.username)
        url = f"http://{target_ip}:4533/{user.username}/rest/getTopSongs"
        params = {
            "u": user.username,
            "p": "enc:000000",
            "v": "1.16.1",
            "c": "concierge-internal",
            "f": "json",
            "count": limit,
        }
        headers = {"x-navidrome-user": user.username}

        resp = await http_client.get(url, params=params, headers=headers, timeout=5.0, follow_redirects=True)
        if resp.status_code == 200:
            data = resp.json()
            songs = data.get("subsonic-response", {}).get("topSongs", {}).get("song", [])
            if isinstance(songs, dict):
                songs = [songs]

            return [{"title": s.get("title"), "artist": s.get("artist")} for s in songs]
        else:
            logger.warning("Navidrome top songs failed with status %s", resp.status_code)
    except Exception as e:
        logger.warning("Error fetching Navidrome history: %s", e)

    return []


async def get_navidrome_seeds(user, db: Session, limit: int = 5):
    """Hybrid strategy to get discovery seeds from Navidrome"""
    seeds = []
    seen = set()

    def add_seed(title, artist):
        key = f"{title}-{artist}"
        if key not in seen:
            seen.add(key)
            seeds.append({"title": title, "artist": artist})

    target_ip = manager.get_or_spawn_container(user.username)
    common_params = {"u": user.username, "p": "enc:000000", "v": "1.16.1", "c": "concierge-internal", "f": "json"}
    headers = {"x-navidrome-user": user.username}

    # A. TOP SONGS
    try:
        url_top = f"http://{target_ip}:4533/{user.username}/rest/getTopSongs"
        resp = await http_client.get(
            url_top, params={**common_params, "count": 10}, headers=headers, timeout=5.0, follow_redirects=True
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("subsonic-response", {}).get("topSongs", {}).get("song", [])
            if isinstance(items, dict):
                items = [items]
            for s in items:
                add_seed(s.get("title"), s.get("artist"))
    except Exception as e:
        logger.debug("Navidrome top-songs seed lookup failed for %s: %s", user.username, e)

    # B. RECENTLY ADDED
    try:
        url_recent = f"http://{target_ip}:4533/{user.username}/rest/getAlbumList2"
        resp = await http_client.get(
            url_recent,
            params={**common_params, "type": "newest", "size": 3},
            headers=headers,
            timeout=5.0,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            data = resp.json()
            albums = data.get("subsonic-response", {}).get("albumList2", {}).get("album", [])
            if isinstance(albums, dict):
                albums = [albums]
            for a in albums:
                add_seed("", a.get("artist"))
    except Exception as e:
        logger.debug("Navidrome recent-albums seed lookup failed for %s: %s", user.username, e)

    # Return shuffled mix
    if seeds:
        random.shuffle(seeds)
        return seeds[:limit]
    return []


# --- API ENDPOINTS ---


@router.get("/api/spotify/recommendations")
async def get_spotify_recommendations(request: Request, db: Session = Depends(get_db)):
    """Get Spotify recommendations based on user's listening history"""
    user = get_current_user_safe(db, request)
    if not user or not user.download_settings:
        return JSONResponse([])

    settings = user.download_settings
    if not settings.spotify_client_id or not settings.spotify_client_secret:
        return JSONResponse({"error": "Spotify not configured"}, status_code=400)

    country = get_user_country(request)
    user_cache_path = f"/saas-data/users/{user.username}/cache/discovery_spotify.json"

    # 1. Try personalization
    try:
        seeds = await get_navidrome_seeds(user, db, limit=10)

        if seeds:
            logger.info("Generating Spotify hybrid mix for %s with %s candidates", user.username, len(seeds))
            seed_tracks = []
            seed_artists = []

            for seed in seeds:
                if len(seed_tracks) + len(seed_artists) >= 5:
                    break

                # A. Try exact song match
                track_found = False
                if seed["title"]:
                    clean_title = seed["title"].split("(")[0].split("[")[0].strip()
                    query = f"track:{clean_title} artist:{seed['artist']}"

                    item = await spotify_service.spotify_service.search_item(
                        settings.spotify_client_id, settings.spotify_client_secret, query, type="track"
                    )
                    if item:
                        seed_tracks.append(item["id"])
                        track_found = True

                # B. Fallback to artist
                if not track_found and (len(seed_tracks) + len(seed_artists) < 5):
                    query = f"artist:{seed['artist']}"
                    item = await spotify_service.spotify_service.search_item(
                        settings.spotify_client_id, settings.spotify_client_secret, query, type="artist"
                    )
                    if item:
                        seed_artists.append(item["id"])

            if seed_tracks or seed_artists:
                logger.info("Spotify final seeds: tracks=%s artists=%s", len(seed_tracks), len(seed_artists))
                recommendations = await spotify_service.spotify_service.get_recommendations(
                    settings.spotify_client_id,
                    settings.spotify_client_secret,
                    seed_tracks=seed_tracks,
                    seed_artists=seed_artists,
                    country=country,
                    cache_path=user_cache_path,
                )
                if recommendations:
                    return JSONResponse(recommendations)

    except Exception as e:
        logger.warning("Spotify personalization failed for %s: %s", user.username, e)

    # 2. Fallback to new releases
    logger.info("Using Spotify global fallback: new releases")
    releases = await spotify_service.spotify_service.get_new_releases(
        settings.spotify_client_id, settings.spotify_client_secret, country=country, cache_path=user_cache_path
    )
    return JSONResponse(releases)


@router.get("/api/youtube/recommendations")
async def get_youtube_recommendations(request: Request, db: Session = Depends(get_db)):
    """Get YouTube recommendations"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse([])

    country = get_user_country(request)
    cookie_path = user.download_settings.youtube_cookies_path if user.download_settings else None
    user_cache_path = f"/saas-data/users/{user.username}/cache/discovery_youtube.json"

    query_override = None

    # Try personalization
    try:
        seeds = await get_navidrome_seeds(user, db, limit=1)
        if seeds:
            seed = seeds[0]
            if seed["title"]:
                query_override = f"{seed['artist']} {seed['title']} official audio"
            else:
                query_override = f"{seed['artist']} top songs official"

            logger.info("Using YouTube personalized mix for %s: %s", user.username, query_override)
    except Exception as e:
        logger.debug("YouTube personalization seed lookup failed for %s: %s", user.username, e)

    trending = await youtube_service.youtube_service.get_trending_music(
        country=country, cookie_path=cookie_path, query_override=query_override, cache_path=user_cache_path
    )
    return JSONResponse(trending)


@router.get("/api/recommendations")
async def get_recommendations(request: Request, db: Session = Depends(get_db)):
    """Get personalized, trending, and local recommendation rows"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # --- 48h CACHE CHECK ---
    os.makedirs(RECS_CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(RECS_CACHE_DIR, f"recs_{user.username}.json")
    try:
        if os.path.exists(cache_file):
            with open(cache_file, "r") as f:
                cached = json.load(f)
            if cached.get("expires_at", 0) > time.time():
                logger.info("Recommendations cache hit for %s", user.username)
                # Always refresh local section (cheap DB query)
                local_section = _get_local_section(db)
                sections = cached.get("sections", [])
                # Replace or append local section
                sections = [s for s in sections if s.get("title") != "Recently Added to Library"]
                if local_section:
                    sections.append(local_section)
                return JSONResponse(sections)
    except Exception as e:
        logger.warning("Recommendations cache read error: %s", e)

    logger.info("Recommendations cache miss for %s, fetching fresh data", user.username)

    sections = []
    top_artists = set()

    try:
        # 1. Seeds from Favorites
        favs = db.query(database.UserFavorite).filter(database.UserFavorite.user_id == user.id).limit(10).all()
        for f in favs:
            if f.track and f.track.artist:
                top_artists.add(f.track.artist)

        # 2. Seeds from Playlists
        playlist_tracks = (
            db.query(database.Track)
            .join(database.PlaylistItem)
            .join(database.Playlist)
            .filter(database.Playlist.owner_id == user.id)
            .limit(20)
            .all()
        )
        for t in playlist_tracks:
            if t.artist:
                top_artists.add(t.artist)

    except Exception as e:
        logger.warning("Recommendations seed query error: %s", e)

    top_artists_list = list(top_artists)
    random.shuffle(top_artists_list)

    # 1. Spotify Recommendations
    spotify_items = []
    settings_obj = user.download_settings
    if settings_obj and settings_obj.spotify_client_id and settings_obj.spotify_client_secret:
        try:
            search_term = top_artists_list[0] if top_artists_list else "pop hits"
            sp_tracks = await spotify_service.spotify_service.search_tracks(
                settings_obj.spotify_client_id,
                settings_obj.spotify_client_secret,
                search_term,
                type="track",
                limit=10,
            )
            for item in sp_tracks:
                title = item.get("name") or item.get("title") or "Unknown"
                artist = item.get("artist", "Unknown")
                album = item.get("album") or "Recommended"
                item_id = item.get("url") or f"https://open.spotify.com/track/{item['id']}"

                spotify_items.append(
                    {
                        "id": item_id,
                        "title": title,
                        "artist": artist,
                        "album": album,
                        "thumbnail": item.get("image", "/static/img/default_cover.png"),
                        "is_local": False,
                        "source": "spotify",
                        "preview": item.get("preview_url"),
                    }
                )
        except Exception as e:
            logger.warning("Recommendations Spotify error: %s", e)

    if spotify_items:
        sections.append({"title": "Spotify • For You", "items": spotify_items})

    # 2. YouTube Personalized
    yt_items = []
    try:
        yt_search_term = (
            top_artists_list[1] if len(top_artists_list) > 1 else (top_artists_list[0] if top_artists_list else None)
        )
        query = f"{yt_search_term} music" if yt_search_term else None
        cache_path = f"/saas-data/cache/yt_recs_{user.username}.json" if query else None

        yt_raw = await youtube_service.youtube_service.get_trending_music(
            limit=24, query_override=query, cache_path=cache_path
        )
        for item in yt_raw:
            yt_items.append(
                {
                    "id": item.get("url") or f"https://www.youtube.com/watch?v={item['id']}",
                    "title": item.get("title", "Unknown"),
                    "artist": item.get("artist", "Unknown"),
                    "album": "YouTube • Taste" if query else "Trending",
                    "thumbnail": item.get("image", "/static/img/default_cover.png"),
                    "is_local": False,
                    "source": "youtube",
                }
            )
    except Exception as e:
        logger.warning("Recommendations YouTube error: %s", e)

    if yt_items:
        sections.append(
            {"title": "YouTube • Based on Your Taste" if top_artists else "YouTube • Trending Now", "items": yt_items}
        )

    # 3. Last.fm Recommendations
    lastfm_items = []
    if settings_obj:
        lastfm_key = getattr(settings_obj, "lastfm_api_key", None)
        if lastfm_key:
            try:
                # Try chart top tracks first (better images)
                lfm_raw = await lastfm_service.get_top_tracks(lastfm_key, limit=12)
                # Fallback to artist-based search if charts empty
                if not lfm_raw:
                    lfm_seed = top_artists_list[0] if top_artists_list else "rock"
                    lfm_raw = await lastfm_service.search_tracks(lastfm_key, lfm_seed, limit=12)
                for item in lfm_raw:
                    title = item.get("name") or "Unknown"
                    artist = item.get("artist", "Unknown")
                    thumbnail = item.get("image") or ""
                    if not thumbnail:
                        thumbnail = f"/api/cover/resolve?artist={artist}&title={title}"
                    lastfm_items.append(
                        {
                            "id": f"ytsearch1:{artist} {title} official audio",
                            "title": title,
                            "artist": artist,
                            "album": "Last.fm",
                            "thumbnail": thumbnail or f"/api/cover/resolve?artist={artist}&title={title}",
                            "is_local": False,
                            "source": "lastfm",
                        }
                    )
            except Exception as e:
                logger.warning("Recommendations Last.fm error: %s", e)

    if lastfm_items:
        sections.append({"title": "Last.fm • Discover", "items": lastfm_items})

    # 4. MusicBrainz Recommendations
    mb_items = []
    try:
        mb_seed = (
            top_artists_list[3] if len(top_artists_list) > 3 else (top_artists_list[0] if top_artists_list else "pop")
        )
        mb_raw = await musicbrainz_service.search_recordings(mb_seed, limit=12)
        for item in mb_raw:
            title = item.get("name") or "Unknown"
            artist = item.get("artist", "Unknown")
            thumbnail = f"/api/cover/resolve?artist={artist}&title={title}"
            mb_items.append(
                {
                    "id": f"ytsearch1:{artist} {title} official audio",
                    "title": title,
                    "artist": artist,
                    "album": item.get("album") or "MusicBrainz",
                    "thumbnail": thumbnail,
                    "is_local": False,
                    "source": "musicbrainz",
                }
            )
    except Exception as e:
        logger.warning("Recommendations MusicBrainz error: %s", e)

    if mb_items:
        sections.append({"title": "MusicBrainz • Explore", "items": mb_items})

    # 5. Local Gems
    local_items = []
    try:
        local_raw = db.query(database.Track).order_by(database.Track.created_at.desc()).limit(12).all()
        for t in local_raw:
            local_items.append(
                {
                    "id": t.source_id or f"local:{t.id}",
                    "db_id": t.id,
                    "title": t.title,
                    "artist": t.artist,
                    "album": t.album,
                    "thumbnail": f"/api/cover/{t.id}",
                    "is_local": True,
                    "source": "local",
                }
            )
    except Exception as e:
        logger.warning("Recommendations local error: %s", e)

    if local_items:
        sections.append({"title": "Recently Added to Library", "items": local_items})

    # --- SAVE TO CACHE (only remote sections) ---
    try:
        remote_sections = [s for s in sections if s.get("title") != "Recently Added to Library"]
        with open(cache_file, "w") as f:
            json.dump({"sections": remote_sections, "expires_at": time.time() + RECS_CACHE_TTL}, f)
        logger.info("Recommendations cache saved for %s with %s remote sections", user.username, len(remote_sections))
    except Exception as e:
        logger.warning("Recommendations cache write error: %s", e)

    return JSONResponse(sections)


def _get_local_section(db: Session):
    """Build local 'Recently Added' section (cheap DB query, never cached)"""
    local_items = []
    try:
        local_raw = db.query(database.Track).order_by(database.Track.created_at.desc()).limit(12).all()
        for t in local_raw:
            local_items.append(
                {
                    "id": t.source_id or f"local:{t.id}",
                    "db_id": t.id,
                    "title": t.title,
                    "artist": t.artist,
                    "album": t.album,
                    "thumbnail": f"/api/cover/{t.id}",
                    "is_local": True,
                    "source": "local",
                }
            )
    except Exception as e:
        logger.warning("Recommendations local section error: %s", e)

    if local_items:
        return {"title": "Recently Added to Library", "items": local_items}
    return None
