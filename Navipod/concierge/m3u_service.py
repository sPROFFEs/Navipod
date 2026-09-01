"""
M3U service backed by the current Playlist / PlaylistItem models.
"""

import logging
import os
from pathlib import Path
from typing import List, Optional

import database
from navipod_config import settings
from playlist_files import normalize_playlist_name, playlist_m3u_filename
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class M3UService:
    """Manage user playlists and their exported M3U files."""

    def __init__(self, db: Session, user: database.User):
        self.db = db
        self.user = user
        self.playlists_root = Path(settings.MUSIC_ROOT) / user.username / "music" / "playlists"
        self.playlists_root.mkdir(parents=True, exist_ok=True)

    def create_playlist(self, name: str, source_url: str = None) -> database.Playlist:
        # Retained for callers using the historical API; source-backed
        # playlists are represented by Playlist.source_playlist_id now.
        del source_url
        display_name = normalize_playlist_name(name)
        existing = (
            self.db.query(database.Playlist)
            .filter(
                database.Playlist.owner_id == self.user.id,
                database.Playlist.name == display_name,
                database.Playlist.source_playlist_id.is_(None),
            )
            .first()
        )
        if existing:
            return existing

        playlist = database.Playlist(
            owner_id=self.user.id,
            name=display_name,
            is_public=False,
        )
        self.db.add(playlist)
        self.db.commit()
        self.db.refresh(playlist)
        self._write_m3u(playlist)
        logger.info("Playlist created: %s for %s", display_name, self.user.username)
        return playlist

    def delete_playlist(self, playlist_id: int) -> bool:
        playlist = self._get_user_playlist(playlist_id)
        if not playlist:
            return False

        self._delete_m3u_file(playlist)
        self.db.delete(playlist)
        self.db.commit()
        logger.info("Playlist deleted: %s", playlist.name)
        return True

    def get_user_playlists(self) -> List[database.Playlist]:
        return (
            self.db.query(database.Playlist)
            .filter(database.Playlist.owner_id == self.user.id)
            .order_by(database.Playlist.name)
            .all()
        )

    def add_track_to_playlist(self, playlist_id: int, track_id: int, position: int = None) -> bool:
        playlist = self._get_user_playlist(playlist_id)
        if not playlist:
            return False

        track = self.db.query(database.Track).filter(database.Track.id == track_id).first()
        if not track:
            return False

        existing = (
            self.db.query(database.PlaylistItem)
            .filter(
                database.PlaylistItem.playlist_id == playlist_id,
                database.PlaylistItem.track_id == track_id,
            )
            .first()
        )
        if existing:
            return True

        if position is None:
            position = len(playlist.items)

        item = database.PlaylistItem(
            playlist_id=playlist_id,
            track_id=track_id,
            position=position,
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(playlist)
        self._write_m3u(playlist)
        logger.info("Track %s added to %s", track.title, playlist.name)
        return True

    def remove_track_from_playlist(self, playlist_id: int, track_id: int) -> bool:
        playlist = self._get_user_playlist(playlist_id)
        if not playlist:
            return False

        item = (
            self.db.query(database.PlaylistItem)
            .filter(
                database.PlaylistItem.playlist_id == playlist_id,
                database.PlaylistItem.track_id == track_id,
            )
            .first()
        )
        if not item:
            return False

        self.db.delete(item)
        self.db.commit()
        self.db.refresh(playlist)
        self._write_m3u(playlist)
        return True

    def get_playlist_tracks(self, playlist_id: int) -> List[database.Track]:
        playlist = self._get_user_playlist(playlist_id)
        if not playlist:
            return []

        items = (
            self.db.query(database.PlaylistItem)
            .filter(database.PlaylistItem.playlist_id == playlist_id)
            .order_by(database.PlaylistItem.position)
            .all()
        )
        return [item.track for item in items if item.track]

    def regenerate_all_m3u(self):
        for playlist in self.get_user_playlists():
            self._write_m3u(playlist)

    def _write_m3u(self, playlist: database.Playlist):
        previous_m3u_path = playlist.m3u_path
        m3u_path = self._build_m3u_path(playlist.name, playlist.id)
        m3u_path.parent.mkdir(parents=True, exist_ok=True)

        items = (
            self.db.query(database.PlaylistItem)
            .filter(database.PlaylistItem.playlist_id == playlist.id)
            .order_by(database.PlaylistItem.position)
            .all()
        )

        try:
            with open(m3u_path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                f.write(f"#PLAYLIST:{playlist.name}\n")
                for item in items:
                    track = item.track
                    if not track or not track.filepath:
                        continue
                    duration = track.duration if track.duration is not None else -1
                    f.write(f"#EXTINF:{duration},{track.artist or 'Unknown'} - {track.title or 'Unknown'}\n")
                    f.write(f"{self._render_track_path(track.filepath)}\n")

            playlist.m3u_path = str(m3u_path)
            self.db.commit()
            previous_path_is_shared = (
                previous_m3u_path
                and self.db.query(database.Playlist.id)
                .filter(
                    database.Playlist.id != playlist.id,
                    database.Playlist.m3u_path == previous_m3u_path,
                )
                .first()
                is not None
            )
            if (
                previous_m3u_path
                and previous_m3u_path != str(m3u_path)
                and not previous_path_is_shared
                and os.path.exists(previous_m3u_path)
            ):
                try:
                    os.remove(previous_m3u_path)
                except OSError:
                    logger.warning("Could not remove superseded playlist M3U %s", previous_m3u_path)
        except Exception:
            logger.exception("Error writing M3U for playlist %s", playlist.id)

    def _get_user_playlist(self, playlist_id: int) -> Optional[database.Playlist]:
        return (
            self.db.query(database.Playlist)
            .filter(
                database.Playlist.id == playlist_id,
                database.Playlist.owner_id == self.user.id,
            )
            .first()
        )

    def _build_m3u_path(self, name: str, playlist_id: int) -> Path:
        return self.playlists_root / playlist_m3u_filename(name, playlist_id)

    def _render_track_path(self, filepath: str) -> str:
        normalized = filepath.replace("\\", "/")
        if "/pool/" in normalized:
            return "../Library/" + normalized.split("/pool/", 1)[1]
        return normalized

    def _delete_m3u_file(self, playlist: database.Playlist):
        path = playlist.m3u_path or str(self._build_m3u_path(playlist.name, playlist.id))
        path_is_shared = (
            self.db.query(database.Playlist.id)
            .filter(database.Playlist.id != playlist.id, database.Playlist.m3u_path == path)
            .first()
            is not None
        )
        try:
            if path and not path_is_shared and os.path.exists(path):
                os.remove(path)
        except Exception:
            logger.exception("Failed to delete M3U file for playlist %s", playlist.id)


def get_m3u_service(db: Session, user: database.User) -> M3UService:
    return M3UService(db, user)
