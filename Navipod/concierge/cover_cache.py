"""
Cover Art Caching Service.
Caches extracted ID3 cover art to disk to avoid repeated extraction.
"""
import os
from pathlib import Path
from typing import Optional

# Cache directory
COVER_CACHE_DIR = Path("/saas-data/cover_cache")


def ensure_cache_dir():
    """Create cache directory if it doesn't exist."""
    COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def get_cached_cover(track_id: int) -> Optional[Path]:
    """
    Check if cover is cached for track.
    Returns path if found, None otherwise.
    """
    path = COVER_CACHE_DIR / f"{track_id}.jpg"
    return path if path.exists() else None


def cache_cover(track_id: int, data: bytes) -> Path:
    """
    Save cover art to cache.
    Returns the path where it was saved.
    """
    ensure_cache_dir()
    path = COVER_CACHE_DIR / f"{track_id}.jpg"
    path.write_bytes(data)
    return path


def delete_cached_cover(track_id: int):
    """Remove cached cover if exists."""
    path = COVER_CACHE_DIR / f"{track_id}.jpg"
    if path.exists():
        path.unlink()


def clear_cover_cache():
    """Clear all cached covers."""
    if COVER_CACHE_DIR.exists():
        for f in COVER_CACHE_DIR.glob("*.jpg"):
            f.unlink()
