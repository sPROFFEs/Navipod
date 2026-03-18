import json
import re
from typing import Any, Dict, List

import spotify_service
import youtube_service
import metadata_cache
from lastfm_service import lastfm_service
from musicbrainz_service import musicbrainz_service


DEFAULT_PREFS = ["spotify", "lastfm", "musicbrainz"]


def parse_preferences(preferences_raw: str | None) -> List[str]:
    if not preferences_raw:
        return DEFAULT_PREFS.copy()
    try:
        loaded = json.loads(preferences_raw)
        if isinstance(loaded, list):
            cleaned = [str(p).strip().lower() for p in loaded if str(p).strip()]
            return cleaned or DEFAULT_PREFS.copy()
    except Exception:
        pass
    return DEFAULT_PREFS.copy()


def build_query(title: str, artist: str) -> str:
    if title and artist:
        return f"{artist} {title}".strip()
    return (title or artist or "").strip()


def _normalize_text(value: str) -> str:
    value = (value or "").lower()
    value = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _tokenize(value: str) -> set[str]:
    stop_words = {"official", "video", "audio", "topic", "lyrics", "lyric", "hd", "mv"}
    return {token for token in _normalize_text(value).split() if len(token) > 1 and token not in stop_words}


def _score_candidate(
    target_title: str,
    target_artist: str,
    target_album: str,
    candidate_title: str,
    candidate_artist: str,
) -> int:
    title_tokens = _tokenize(target_title)
    artist_tokens = _tokenize(target_artist)
    album_tokens = _tokenize(target_album)
    candidate_title_tokens = _tokenize(candidate_title)
    candidate_artist_tokens = _tokenize(candidate_artist)
    haystack = f"{candidate_title} {candidate_artist}"
    haystack_tokens = _tokenize(haystack)

    score = 0
    score += len(title_tokens & haystack_tokens) * 5
    score += len(artist_tokens & haystack_tokens) * 7
    score += len(album_tokens & haystack_tokens) * 2

    normalized_target_title = _normalize_text(target_title)
    normalized_candidate_title = _normalize_text(candidate_title)
    normalized_target_artist = _normalize_text(target_artist)
    normalized_candidate_artist = _normalize_text(candidate_artist)

    if normalized_target_title and normalized_target_title in normalized_candidate_title:
        score += 10
    if normalized_target_artist and normalized_target_artist in normalized_candidate_artist:
        score += 10

    penalty_terms = {"live", "karaoke", "remix", "sped up", "slowed", "instrumental"}
    normalized_target = f"{normalized_target_title} {normalized_target_artist}"
    normalized_candidate = f"{normalized_candidate_title} {normalized_candidate_artist}"
    for term in penalty_terms:
        if term in normalized_candidate and term not in normalized_target:
            score -= 8

    bonus_terms = {"topic", "provided to youtube", "audio"}
    for term in bonus_terms:
        if term in normalized_candidate:
            score += 3

    return score


async def search_tracks_with_fallback(settings: Any, query: str, limit: int = 20) -> List[Dict]:
    if not query:
        return []

    prefs = parse_preferences(getattr(settings, "metadata_preferences", None)) if settings else DEFAULT_PREFS.copy()

    for provider in prefs:
        provider = provider.lower()

        if provider == "spotify" and settings and settings.spotify_client_id and settings.spotify_client_secret:
            items = await spotify_service.spotify_service.search_tracks(
                settings.spotify_client_id,
                settings.spotify_client_secret,
                query,
                type="track",
                limit=limit,
            )
            if items:
                return items

        if provider == "lastfm" and settings and getattr(settings, "lastfm_api_key", None):
            items = await lastfm_service.search_tracks(settings.lastfm_api_key, query, limit=limit)
            if items:
                return items

        if provider == "musicbrainz":
            items = await musicbrainz_service.search_recordings(query, limit=limit)
            if items:
                return items

    return []


async def enrich_metadata(settings: Any, title: str, artist: str, album: str = "") -> Dict[str, Any]:
    cache_key = metadata_cache.make_key("enrich", title=title, artist=artist, album=album)
    cached = metadata_cache.get(cache_key)
    if cached:
        return cached

    query = build_query(title, artist)
    data: Dict[str, Any] = {
        "title": title or "",
        "artist": artist or "",
        "album": album or "",
        "genres": [],
        "cover_url": "",
        "release_year": "",
        "source": "unknown",
    }

    prefs = parse_preferences(getattr(settings, "metadata_preferences", None)) if settings else DEFAULT_PREFS.copy()

    for provider in prefs:
        provider = provider.lower()

        if provider == "spotify" and settings and settings.spotify_client_id and settings.spotify_client_secret:
            sp = await spotify_service.spotify_service.search_item(
                settings.spotify_client_id,
                settings.spotify_client_secret,
                query,
                type="track",
                limit=1,
            )
            if sp:
                data["title"] = data["title"] or sp.get("name", "")
                data["artist"] = data["artist"] or sp.get("artist", "")
                data["album"] = data["album"] or sp.get("album", "")
                data["cover_url"] = sp.get("image", "")
                data["release_year"] = (sp.get("release_date") or "").split("-")[0]
                data["spotify_url"] = sp.get("url", "")
                data["source"] = "spotify"

        elif provider == "lastfm" and settings and getattr(settings, "lastfm_api_key", None):
            tags = await lastfm_service.get_track_tags(
                settings.lastfm_api_key,
                data.get("artist") or artist,
                data.get("title") or title,
            )
            if tags and not data["genres"]:
                data["genres"] = tags[:5]
                if data["source"] == "unknown":
                    data["source"] = "lastfm"

        elif provider == "musicbrainz":
            mb = await musicbrainz_service.search_recordings(
                build_query(data.get("title"), data.get("artist")),
                limit=1,
            )
            if mb:
                top = mb[0]
                data["mbid"] = top.get("id", "")
                if not data.get("release_year"):
                    data["release_year"] = top.get("year", "")
                if not data.get("album"):
                    data["album"] = top.get("album", "")
                if data["source"] == "unknown":
                    data["source"] = "musicbrainz"

    metadata_cache.set(cache_key, data)
    return data


async def resolve_cover_url(settings: Any, title: str, artist: str, album: str = "") -> str:
    cache_key = metadata_cache.make_key("cover-url", title=title, artist=artist, album=album)
    cached = metadata_cache.get(cache_key)
    if cached and cached.get("cover_url"):
        return cached["cover_url"]

    enriched = await enrich_metadata(settings, title=title, artist=artist, album=album)
    cover_url = enriched.get("cover_url", "")
    if cover_url:
        metadata_cache.set(cache_key, {"cover_url": cover_url})
    return cover_url


async def resolve_download_target(
    settings: Any,
    raw_url: str,
    title: str,
    artist: str,
    album: str = "",
    source: str = "",
) -> Dict[str, str]:
    cache_key = metadata_cache.make_key(
        "download-target",
        raw_url=raw_url,
        title=title,
        artist=artist,
        album=album,
        source=source,
    )
    cached = metadata_cache.get(cache_key)
    if cached:
        return cached

    raw_url = (raw_url or "").strip()
    source = (source or "").strip().lower()
    title = (title or "").strip()
    artist = (artist or "").strip()
    album = (album or "").strip()

    result = {
        "url": raw_url,
        "resolution_mode": "original",
        "cover_url": "",
        "title": title,
        "artist": artist,
        "album": album,
    }

    if not raw_url:
        return result
    if "youtube.com/watch" in raw_url or "youtu.be/" in raw_url:
        return result

    enriched = await enrich_metadata(settings, title=title, artist=artist, album=album)
    canonical_title = enriched.get("title") or title
    canonical_artist = enriched.get("artist") or artist
    canonical_album = enriched.get("album") or album
    cover_url = enriched.get("cover_url") or ""
    spotify_url = enriched.get("spotify_url") or ""

    result.update({
        "cover_url": cover_url,
        "title": canonical_title,
        "artist": canonical_artist,
        "album": canonical_album,
    })

    if spotify_url and source in {"spotify", "lastfm", "musicbrainz"}:
        result["url"] = spotify_url
        result["resolution_mode"] = "spotify-resolved"
        return result

    queries = []
    base_artist = canonical_artist or artist
    base_title = canonical_title or title
    base_album = canonical_album or album
    compact_title = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", base_title).strip()

    if base_artist and base_title:
        queries.extend([
            f"{base_artist} {base_title} audio",
            f"{base_artist} {base_title} topic",
            f"{base_artist} {base_title} official audio",
        ])
    if base_artist and compact_title and compact_title != base_title:
        queries.extend([
            f"{base_artist} {compact_title} audio",
            f"{base_artist} {compact_title} topic",
        ])
    if base_artist and base_album and base_title:
        queries.append(f"{base_artist} {base_title} {base_album}")

    seen_ids = set()
    best_item = None
    best_score = -10**9
    cookie_path = getattr(settings, "youtube_cookies_path", None) if settings else None

    for query in queries:
        items = await youtube_service.youtube_service.search_videos(query, limit=8, cookie_path=cookie_path)
        for item in items:
            video_id = item.get("id")
            if not video_id or video_id in seen_ids:
                continue
            seen_ids.add(video_id)
            score = _score_candidate(
                target_title=base_title,
                target_artist=base_artist,
                target_album=base_album,
                candidate_title=item.get("title", ""),
                candidate_artist=item.get("artist", ""),
            )
            if score > best_score:
                best_score = score
                best_item = item

    if best_item and best_score >= 8:
        result["url"] = best_item.get("url") or f"https://www.youtube.com/watch?v={best_item.get('id')}"
        result["resolution_mode"] = "youtube-resolved"

    metadata_cache.set(cache_key, result)
    return result
