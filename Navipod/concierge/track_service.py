"""
Track service aligned with the current Track schema.
"""

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

import database
import track_identity
from navipod_config import settings

logger = logging.getLogger(__name__)


class TrackService:
    def __init__(self, db: Session):
        self.db = db
        self.pool_root = settings.MUSIC_POOL_ROOT
        os.makedirs(self.pool_root, exist_ok=True)

    def search_local(self, query: str, limit: int = 20) -> List[database.Track]:
        query_lower = f"%{query.lower()}%"
        return self.db.query(database.Track).filter(
            (database.Track.title.ilike(query_lower))
            | (database.Track.artist.ilike(query_lower))
            | (database.Track.album.ilike(query_lower))
        ).limit(limit).all()

    def search_hybrid(self, query: str, spotify_service=None, youtube_service=None, spotify_creds: Dict = None, limit: int = 20) -> Dict[str, Any]:
        return {
            "local": [self._track_to_dict(track) for track in self.search_local(query, limit)],
            "remote_spotify": [],
            "remote_youtube": [],
        }

    def find_by_source_id(self, source_id: str) -> Optional[database.Track]:
        return self.db.query(database.Track).filter(database.Track.source_id == source_id).first()

    def find_by_hash(self, file_path: str) -> Optional[database.Track]:
        file_hash = self._compute_file_hash(file_path)
        if not file_hash:
            return None
        return self.db.query(database.Track).filter(database.Track.file_hash == file_hash).first()

    def track_exists(self, source_id: str) -> bool:
        return self.find_by_source_id(source_id) is not None

    def compute_fingerprint(self, artist: str, title: str) -> str:
        return track_identity.compute_track_identity(artist, title)["fingerprint"]

    def find_similar(self, artist: str, title: str) -> List[database.Track]:
        fingerprint = self.compute_fingerprint(artist, title)
        base_fingerprint = fingerprint.rsplit("::", 1)[0]
        return self.db.query(database.Track).filter(
            database.Track.fingerprint.like(f"{base_fingerprint}::%")
        ).all()

    def is_exact_duplicate(self, artist: str, title: str) -> Optional[database.Track]:
        fingerprint = self.compute_fingerprint(artist, title)
        return self.db.query(database.Track).filter(database.Track.fingerprint == fingerprint).first()

    def get_pool_path(self, artist: str, album: str, filename: str) -> str:
        safe_artist = self._sanitize_path(artist) or "Unknown Artist"
        safe_album = self._sanitize_path(album) or "Unknown Album"
        safe_filename = self._sanitize_path(filename) or "track.mp3"
        return str(Path(self.pool_root) / safe_artist / safe_album / safe_filename)

    def register_track(
        self,
        source_id: str,
        file_path: str,
        title: str,
        artist: str,
        album: str = None,
        duration_ms: int = None,
        cover_path: str = None,
    ) -> database.Track:
        existing = track_identity.find_existing_track(
            self.db,
            source_id=source_id,
            file_hash=self._compute_file_hash(file_path) if os.path.exists(file_path) else None,
            artist=artist,
            title=title,
        )
        if existing:
            return existing

        identity = track_identity.compute_track_identity(artist, title)
        track = database.Track(
            source_id=source_id,
            file_hash=self._compute_file_hash(file_path) if os.path.exists(file_path) else None,
            artist_norm=identity["artist_norm"],
            title_norm=identity["title_norm"],
            version_tag=identity["version_tag"],
            fingerprint=identity["fingerprint"],
            title=title,
            artist=artist,
            album=album,
            duration=duration_ms,
            filepath=file_path,
            source_provider="pool",
        )
        self.db.add(track)
        self.db.commit()
        self.db.refresh(track)
        return track

    def _track_to_dict(self, track: database.Track) -> Dict[str, Any]:
        return {
            "id": track.id,
            "source_id": track.source_id,
            "title": track.title,
            "artist": track.artist,
            "album": track.album,
            "duration_ms": track.duration,
            "file_path": track.filepath,
            "cover_path": None,
            "in_pool": True,
        }

    def _sanitize_path(self, name: str) -> str:
        if not name:
            return ""
        unsafe = '<>:"/\\|?*'
        for char in unsafe:
            name = name.replace(char, "")
        return name.strip()[:100]

    def _compute_file_hash(self, file_path: str, chunk_size: int = 8192) -> Optional[str]:
        if not os.path.exists(file_path):
            return None
        try:
            sha256 = hashlib.sha256()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(chunk_size), b""):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception as exc:
            logger.error(f"Error calculating hash: {exc}")
            return None


def get_track_service(db: Session) -> TrackService:
    return TrackService(db)
