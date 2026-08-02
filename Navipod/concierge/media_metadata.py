"""Shared audio-tag extraction for imports and downloads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import mutagen


@dataclass(frozen=True)
class AudioMetadata:
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    year: int | None = None
    duration: int | None = None


def _first(tags, key: str, default: str = "") -> str:
    values = tags.get(key, [default]) if tags else [default]
    value = values[0] if values else default
    return str(value or default).strip()


def _parse_year(value: str) -> int | None:
    value = (value or "").strip()
    if len(value) >= 4 and value[:4].isdigit():
        year = int(value[:4])
        if 1000 <= year <= 9999:
            return year
    return None


def read_audio_metadata(path: str | Path) -> AudioMetadata:
    """Read common tags and duration. Missing or malformed tags are harmless."""
    path = Path(path)
    title = path.stem
    artist = album = genre = date = ""
    duration = None

    try:
        easy = mutagen.File(path, easy=True)
        if easy:
            title = _first(easy, "title", title) or title
            artist = _first(easy, "artist")
            album = _first(easy, "album")
            genre = _first(easy, "genre")
            date = _first(easy, "date") or _first(easy, "year")
    except Exception:
        pass

    try:
        full = mutagen.File(path)
        length = getattr(getattr(full, "info", None), "length", None)
        if length:
            duration = int(length)
    except Exception:
        pass

    return AudioMetadata(
        title=title,
        artist=artist,
        album=album,
        genre=genre,
        year=_parse_year(date),
        duration=duration,
    )


def backfill_library_metadata() -> int:
    """Index tags for pre-upgrade tracks once, without blocking app startup."""
    import database

    db = database.SessionLocal()
    updated = 0
    try:
        while True:
            tracks = db.query(database.Track).filter(database.Track.metadata_scanned_at.is_(None)).limit(100).all()
            if not tracks:
                break
            for track in tracks:
                metadata = read_audio_metadata(track.filepath) if track.filepath else AudioMetadata()
                if not track.genre and metadata.genre:
                    track.genre = metadata.genre
                if track.year is None and metadata.year is not None:
                    track.year = metadata.year
                if not track.duration and metadata.duration:
                    track.duration = metadata.duration
                track.metadata_scanned_at = datetime.now(timezone.utc)
                updated += 1
            db.commit()
        return updated
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
