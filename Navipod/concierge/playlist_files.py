"""Playlist display-name validation and safe M3U filename mapping."""

import re
import unicodedata

MAX_PLAYLIST_NAME_LENGTH = 120
_UNSAFE_FILENAME_CHARS = '<>:"/\\|?*'
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_MAX_M3U_STEM_BYTES = 180


def normalize_playlist_name(name: str) -> str:
    """Return a validated display name without restricting punctuation."""
    normalized = unicodedata.normalize("NFC", name or "").strip()
    if not normalized:
        raise ValueError("Playlist name cannot be empty.")
    if len(normalized) > MAX_PLAYLIST_NAME_LENGTH:
        raise ValueError(f"Playlist name must be {MAX_PLAYLIST_NAME_LENGTH} characters or fewer.")
    if _CONTROL_CHARS.search(normalized):
        raise ValueError("Playlist name cannot contain control characters.")
    return normalized


def playlist_m3u_filename(name: str, playlist_id: int) -> str:
    """Map a display name to a portable, collision-free M3U filename."""
    normalized = unicodedata.normalize("NFKC", name or "").strip()
    stem = "".join("-" if char in _UNSAFE_FILENAME_CHARS else char for char in normalized)
    stem = _CONTROL_CHARS.sub("-", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .-")
    stem = stem[:MAX_PLAYLIST_NAME_LENGTH].rstrip(" .-")
    while len(stem.encode("utf-8")) > _MAX_M3U_STEM_BYTES:
        stem = stem[:-1]
    stem = stem.rstrip(" .-") or "Playlist"
    return f"{stem}--{int(playlist_id)}.m3u"
