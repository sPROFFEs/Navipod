"""
artist_service - Aggregates artist metadata across remote sources with
aggressive global caching to keep external API call volume low.

Caching strategy
----------------
We piggyback on `metadata_cache` (the existing SQLite-backed key/value
store at `/saas-data/cache/metadata_cache.db`). Each entry stores a
`cached_at` timestamp inside the payload; the wrapper functions enforce
the TTL on read. Caches are GLOBAL (not per user) because artist
discography and similar-artists data don't depend on the requesting
user — sharing the cache across the whole instance gives us a much
higher hit rate.

TTLs (chosen to keep data fresh enough to feel current while making
external API calls rare):

* artist_view  — 7 days   (discography rarely changes that fast; new
                            singles take a week to surface but that's
                            acceptable for a self-hosted music app)
* track_radio  — 14 days  (similar-tracks corpus is essentially static
                            for known catalog tracks)

If a user *needs* fresh data sooner they can re-trigger the fetch by
clearing /saas-data/cache/metadata_cache.db (handled by the existing
cache_maintenance job).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import metadata_cache
from lastfm_service import lastfm_service
from spotify_service import spotify_service

logger = logging.getLogger(__name__)


ARTIST_VIEW_TTL = 7 * 24 * 3600       # 7 days
TRACK_RADIO_TTL = 14 * 24 * 3600      # 14 days


def _is_fresh(payload: dict, ttl: int) -> bool:
    if not payload:
        return False
    cached_at = payload.get("cached_at") or 0
    return (time.time() - cached_at) < ttl


def _now_payload(data: dict) -> dict:
    return {**data, "cached_at": time.time()}


# --- ARTIST VIEW ----------------------------------------------------------


async def get_artist_view(
    artist_name: str,
    *,
    spotify_client_id: Optional[str],
    spotify_client_secret: Optional[str],
    lastfm_api_key: Optional[str],
    country: str = "ES",
) -> dict:
    """Return the remote slice of an artist view: bio, similar artists,
    full discography (Spotify), and Last.fm top tracks. Local tracks are
    appended by the router from the SQL Track table — this function
    deliberately stays user-agnostic so the cache is shareable."""

    if not artist_name:
        return {}

    cache_key = metadata_cache.make_key("artist_view", name=artist_name)
    cached = metadata_cache.get(cache_key)
    if _is_fresh(cached, ARTIST_VIEW_TTL):
        logger.info("artist_view cache hit for %s", artist_name)
        return cached

    logger.info("artist_view cache miss for %s — fetching", artist_name)

    out = {
        "name": artist_name,
        "info": {},
        "similar": [],
        "albums": [],
        "top_tracks": [],
        "spotify": None,
    }

    # 1. Last.fm artist info + similar (cheapest API, gives us bio/tags)
    if lastfm_api_key:
        try:
            info = await lastfm_service.get_artist_info(lastfm_api_key, artist_name)
            if info:
                out["info"] = info
        except Exception as e:
            logger.warning("lastfm artist info failed for %s: %s", artist_name, e)

        try:
            similar = await lastfm_service.get_similar_artists(lastfm_api_key, artist_name, limit=12)
            out["similar"] = similar
        except Exception as e:
            logger.warning("lastfm similar artists failed for %s: %s", artist_name, e)

        try:
            top = await lastfm_service.get_artist_top_tracks(lastfm_api_key, artist_name, limit=10)
            out["top_tracks"] = top
        except Exception as e:
            logger.warning("lastfm artist top tracks failed for %s: %s", artist_name, e)

    # 2. Spotify discography (only when configured — Spotify auth is per-user
    # but the artist data we pull is universal so we cache it globally).
    if spotify_client_id and spotify_client_secret:
        try:
            sp_artist = await spotify_service.get_artist_by_name(
                spotify_client_id, spotify_client_secret, artist_name
            )
            if sp_artist:
                out["spotify"] = sp_artist
                albums = await spotify_service.get_artist_albums(
                    spotify_client_id,
                    spotify_client_secret,
                    sp_artist["id"],
                    country=country,
                    limit=40,
                )
                out["albums"] = albums
        except Exception as e:
            logger.warning("spotify discography failed for %s: %s", artist_name, e)

    # Don't cache an artist whose remote fetches all came back empty —
    # the user is one outage away from getting served a 7-day-stale
    # blank shell. Caching is best-effort: if at least ONE source
    # produced data, we keep it (next visitor benefits); otherwise we
    # skip the write so the next request retries.
    has_real_data = bool(
        out.get("info") or out.get("similar") or out.get("top_tracks")
        or out.get("albums") or out.get("spotify")
    )
    if has_real_data:
        metadata_cache.set(cache_key, _now_payload(out))
    else:
        logger.info("artist_view skipped caching empty result for %s", artist_name)
    return out


# --- SMART RADIO ----------------------------------------------------------


async def get_radio_seeds(
    *,
    artist: str,
    title: str,
    lastfm_api_key: Optional[str],
    fallback_seed_artist: Optional[str] = None,
) -> list[dict]:
    """Build the seed pool for a smart-radio queue. The actual track
    resolution (cover, preview URL, source) happens in the frontend by
    feeding each seed name through ytsearch — keeping it that way avoids
    burning Spotify quota for every radio start.

    Seeds are cached globally by (artist|title) so two users opening a
    radio for the same track share one Last.fm hit."""

    if not (artist and title):
        return []

    cache_key = metadata_cache.make_key("track_radio", artist=artist, title=title)
    cached = metadata_cache.get(cache_key)
    if _is_fresh(cached, TRACK_RADIO_TTL):
        logger.info("track_radio cache hit for %s — %s", artist, title)
        return cached.get("seeds", [])

    logger.info("track_radio cache miss for %s — %s", artist, title)
    seeds: list[dict] = []

    if lastfm_api_key:
        try:
            similar = await lastfm_service.get_similar_tracks(
                lastfm_api_key, artist, title, limit=40
            )
            for s in similar:
                if s.get("title") and s.get("artist"):
                    seeds.append({"title": s["title"], "artist": s["artist"]})
        except Exception as e:
            logger.warning("lastfm similar tracks failed for %s — %s: %s", artist, title, e)

        # If track.getSimilar returned nothing, fall back to the artist's
        # top-tracks neighbourhood so the radio never comes back empty.
        if not seeds:
            try:
                tops = await lastfm_service.get_artist_top_tracks(
                    lastfm_api_key, fallback_seed_artist or artist, limit=20
                )
                for t in tops:
                    seeds.append({"title": t["title"], "artist": t["artist"]})
            except Exception as e:
                logger.warning("lastfm artist top tracks fallback failed: %s", e)

            # Then expand into similar artists' top tracks for variety.
            try:
                sim_artists = await lastfm_service.get_similar_artists(
                    lastfm_api_key, fallback_seed_artist or artist, limit=6
                )
                for sa in sim_artists[:4]:
                    tops = await lastfm_service.get_artist_top_tracks(
                        lastfm_api_key, sa["name"], limit=4
                    )
                    for t in tops:
                        seeds.append({"title": t["title"], "artist": t["artist"]})
            except Exception as e:
                logger.warning("lastfm similar-artists expansion failed: %s", e)

    # De-dupe (case-insensitive) so the radio doesn't loop the same song.
    seen = set()
    deduped = []
    for s in seeds:
        key = f"{(s['title'] or '').strip().lower()}|{(s['artist'] or '').strip().lower()}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)

    payload = _now_payload({"seeds": deduped})
    metadata_cache.set(cache_key, payload)
    return deduped
