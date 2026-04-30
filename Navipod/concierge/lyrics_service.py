"""
lyrics_service - Synced lyrics fetcher using lrclib.net.

Why lrclib.net?
  - Free, no API key, no auth, no rate-limits as long as you set a
    reasonable User-Agent.
  - Crowd-sourced LRC corpus that beats Genius/Musixmatch for most
    non-English catalog and is comparable for mainstream pop.
  - Returns both `syncedLyrics` (LRC format) and `plainLyrics` so the
    frontend can fall back gracefully if synced are missing.

Caching strategy
  - Lyrics are immutable once published — we cache for 30 days in the
    shared metadata_cache.db with key `lyrics:{artist}|{title}` so
    every user benefits from a single fetch per song.
  - 404s are also cached (briefly, 24h) so we don't hammer lrclib for
    obscure tracks every time the user replays them. Stored as
    `{"miss": true, ...}`.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx
import metadata_cache

logger = logging.getLogger(__name__)

LRCLIB_BASE = "https://lrclib.net/api"
USER_AGENT = "Navipod/0.1 (https://github.com/navipod) lyrics-fetcher"

LYRICS_HIT_TTL = 30 * 24 * 3600   # 30 days
LYRICS_MISS_TTL = 24 * 3600       # 1 day — give lrclib a chance to add it


def _is_fresh(payload: dict, ttl: int) -> bool:
    if not payload:
        return False
    cached_at = payload.get("cached_at") or 0
    return (time.time() - cached_at) < ttl


async def get_lyrics(
    *,
    title: str,
    artist: str,
    album: Optional[str] = None,
    duration: Optional[float] = None,
) -> dict:
    """Fetch synced + plain lyrics for a track. Returns:

        {
          "synced": "[00:12.00] La la la\n...",  # may be ""
          "plain":  "La la la\n...",              # may be ""
          "instrumental": False,
          "source": "lrclib" | "cache" | "miss"
        }

    The 'miss' response is intentional — we want the frontend to render
    a friendly "no lyrics found" state rather than retry."""

    if not (title and artist):
        return {"synced": "", "plain": "", "instrumental": False, "source": "miss"}

    cache_key = metadata_cache.make_key("lyrics", artist=artist, title=title)
    cached = metadata_cache.get(cache_key)

    # Honour the cache — both hits and misses, but with different TTLs
    # so a recently-uploaded song can still win on the next refresh.
    if cached:
        ttl = LYRICS_MISS_TTL if cached.get("miss") else LYRICS_HIT_TTL
        if _is_fresh(cached, ttl):
            if cached.get("miss"):
                return {
                    "synced": "", "plain": "", "instrumental": False, "source": "cache_miss",
                }
            return {
                "synced": cached.get("synced", ""),
                "plain": cached.get("plain", ""),
                "instrumental": cached.get("instrumental", False),
                "source": "cache",
            }

    params = {
        "artist_name": artist,
        "track_name": title,
    }
    if album:
        params["album_name"] = album
    if duration and duration > 0:
        params["duration"] = int(duration)

    url = f"{LRCLIB_BASE}/get"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    synced = ""
    plain = ""
    instrumental = False
    found = False

    async with httpx.AsyncClient(timeout=8.0) as client:
        try:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                synced = data.get("syncedLyrics") or ""
                plain = data.get("plainLyrics") or ""
                instrumental = bool(data.get("instrumental"))
                found = bool(synced or plain or instrumental)
            elif resp.status_code != 404:
                logger.warning("lrclib /get unexpected status %s for %s — %s", resp.status_code, artist, title)
        except Exception as e:
            logger.warning("lrclib /get failed for %s — %s: %s", artist, title, e)

        # If the exact-match endpoint missed, try the search endpoint —
        # lrclib's /get demands tight metadata; /search is fuzzy. We only
        # accept the first result if its title+artist match (case-insensitive)
        # to avoid serving the wrong track's lyrics.
        if not found:
            try:
                s_resp = await client.get(
                    f"{LRCLIB_BASE}/search",
                    params={"track_name": title, "artist_name": artist},
                    headers=headers,
                )
                if s_resp.status_code == 200:
                    results = s_resp.json() or []
                    for cand in results[:5]:
                        cand_title = (cand.get("trackName") or "").strip().lower()
                        cand_artist = (cand.get("artistName") or "").strip().lower()
                        if cand_title == title.strip().lower() and cand_artist == artist.strip().lower():
                            synced = cand.get("syncedLyrics") or ""
                            plain = cand.get("plainLyrics") or ""
                            instrumental = bool(cand.get("instrumental"))
                            found = bool(synced or plain or instrumental)
                            if found:
                                break
            except Exception as e:
                logger.warning("lrclib /search failed for %s — %s: %s", artist, title, e)

    if found:
        payload = {
            "cached_at": time.time(),
            "synced": synced,
            "plain": plain,
            "instrumental": instrumental,
        }
        metadata_cache.set(cache_key, payload)
        return {"synced": synced, "plain": plain, "instrumental": instrumental, "source": "lrclib"}

    # Persist the miss with a short TTL.
    metadata_cache.set(cache_key, {"cached_at": time.time(), "miss": True})
    return {"synced": "", "plain": "", "instrumental": False, "source": "miss"}
