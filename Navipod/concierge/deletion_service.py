"""Keep historical references consistent while deleting playlists and tracks."""

import database
from sqlalchemy.orm import Session


def detach_playlist_references(db: Session, playlist_id: int) -> None:
    """Remove owned items and retain download history without a dead target."""
    db.query(database.DownloadJob).filter(database.DownloadJob.target_modern_playlist_id == playlist_id).update(
        {database.DownloadJob.target_modern_playlist_id: None}, synchronize_session=False
    )
    db.query(database.PlaylistItem).filter(database.PlaylistItem.playlist_id == playlist_id).delete(
        synchronize_session=False
    )


def detach_track_references(db: Session, track_id: int) -> None:
    """Apply the lifecycle policy for every known reference to a track."""
    db.query(database.UserFavorite).filter(database.UserFavorite.track_id == track_id).delete(synchronize_session=False)
    db.query(database.PlaylistItem).filter(database.PlaylistItem.track_id == track_id).delete(synchronize_session=False)
    db.query(database.PartyRoomQueueItem).filter(database.PartyRoomQueueItem.track_id == track_id).delete(
        synchronize_session=False
    )
    db.query(database.Playlist).filter(database.Playlist.cover_track_id == track_id).update(
        {database.Playlist.cover_track_id: None}, synchronize_session=False
    )
    db.query(database.DownloadJob).filter(database.DownloadJob.resolved_track_id == track_id).update(
        {database.DownloadJob.resolved_track_id: None}, synchronize_session=False
    )
    db.query(database.TrackDeleteRequest).filter(database.TrackDeleteRequest.track_id == track_id).update(
        {database.TrackDeleteRequest.track_id: None}, synchronize_session=False
    )
