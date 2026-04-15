import re
import unicodedata

from sqlalchemy import or_

import database


YOUTUBE_PATTERNS = [
    re.compile(r"(?:v=|youtu\.be/|embed/|shorts/)([a-zA-Z0-9_-]{11})"),
]
SPOTIFY_TRACK_PATTERNS = [
    re.compile(r"spotify\.com/track/([a-zA-Z0-9]+)"),
    re.compile(r"spotify:track:([a-zA-Z0-9]+)"),
]

VERSION_PATTERNS = [
    (r"[\(\[\-]\s*(.*?remix.*?)[\)\]]?$", "remix"),
    (r"\s+remix$", "remix"),
    (r"[\(\[\-]\s*(live|en vivo|directo).*?[\)\]]?$", "live"),
    (r"[\(\[\-]\s*(acoustic|acustico|acústico).*?[\)\]]?$", "acoustic"),
    (r"[\(\[\-]\s*(remaster|remasterizado|reissue).*?[\)\]]?$", "remaster"),
    (r"\s*-\s*\d{4}\s*(remaster|remasterizado).*$", "remaster"),
    (r"[\(\[\-]\s*(radio edit|single edit|edit)[\)\]]?$", "edit"),
    (r"[\(\[\-]\s*(extended|extendido).*?[\)\]]?$", "extended"),
    (r"[\(\[\-]\s*(instrumental)[\)\]]?$", "instrumental"),
    (r"[\(\[\-]\s*(cover|tribute|version de|versión de).*?[\)\]]?$", "cover"),
    (r"[\(\[\-]\s*(version|versión).*?[\)\]]?$", "version"),
]
FEAT_PATTERN = re.compile(r"[\(\[]\s*(feat\.?|ft\.?|featuring).*?[\)\]]", re.IGNORECASE)


def extract_source_id_from_url(url: str) -> str | None:
    if not url:
        return None

    cleaned = url.strip().rstrip("/")
    for pattern in YOUTUBE_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            return f"youtube:{match.group(1)}"

    for pattern in SPOTIFY_TRACK_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            return f"spotify:track:{match.group(1)}"

    return None


def normalize_text(text: str) -> str:
    if not text:
        return ""

    text = text.lower().replace("&", " and ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = " ".join(text.split())
    return text


def extract_version_tag(title: str) -> tuple[str, str]:
    if not title:
        return ("", "original")

    cleaned_title = FEAT_PATTERN.sub("", title).strip()
    lowered = cleaned_title.lower()

    for pattern, tag in VERSION_PATTERNS:
        if re.search(pattern, lowered, re.IGNORECASE):
            stripped = re.sub(pattern, "", cleaned_title, flags=re.IGNORECASE).strip()
            stripped = re.sub(r"\s*[-–—]\s*$", "", stripped).strip()
            return (stripped, tag)

    return (cleaned_title, "original")


def compute_track_identity(artist: str, title: str) -> dict[str, str]:
    clean_title, version_tag = extract_version_tag(title or "")
    artist_norm = normalize_text(artist or "")
    title_norm = normalize_text(clean_title)
    return {
        "artist_norm": artist_norm,
        "title_norm": title_norm,
        "version_tag": version_tag,
        "fingerprint": f"{artist_norm}::{title_norm}::{version_tag}",
    }


def apply_identity_fields(track, artist: str | None = None, title: str | None = None):
    identity = compute_track_identity(artist if artist is not None else track.artist, title if title is not None else track.title)
    track.artist_norm = identity["artist_norm"]
    track.title_norm = identity["title_norm"]
    track.version_tag = identity["version_tag"]
    track.fingerprint = identity["fingerprint"]
    return identity


def find_existing_track(db, *, source_id: str | None = None, file_hash: str | None = None, artist: str | None = None, title: str | None = None, fingerprint: str | None = None):
    if source_id:
        track = db.query(database.Track).filter(database.Track.source_id == source_id).first()
        if track:
            return track

    if file_hash:
        track = db.query(database.Track).filter(database.Track.file_hash == file_hash).first()
        if track:
            return track

    effective_fingerprint = fingerprint
    if not effective_fingerprint and (artist or title):
        effective_fingerprint = compute_track_identity(artist or "", title or "")["fingerprint"]

    if effective_fingerprint and effective_fingerprint != "::::original":
        return db.query(database.Track).filter(database.Track.fingerprint == effective_fingerprint).first()

    return None


def backfill_missing_track_identities(db, batch_size: int = 500) -> int:
    updated = 0

    while True:
        tracks = db.query(database.Track).filter(
            or_(
                database.Track.artist_norm.is_(None),
                database.Track.title_norm.is_(None),
                database.Track.version_tag.is_(None),
                database.Track.fingerprint.is_(None),
            )
        ).limit(batch_size).all()

        if not tracks:
            break

        for track in tracks:
            apply_identity_fields(track)
            updated += 1

        db.commit()

    return updated
