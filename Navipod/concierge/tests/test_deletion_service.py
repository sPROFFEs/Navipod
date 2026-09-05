import database
import deletion_service


def test_playlist_delete_detaches_history_and_removes_items(db_session):
    user = database.User(username="playlist-owner")
    track = database.Track(title="Track", filepath="/tmp/playlist-track.mp3")
    db_session.add_all([user, track])
    db_session.flush()

    playlist = database.Playlist(name="Imported", owner_id=user.id)
    db_session.add(playlist)
    db_session.flush()
    db_session.add(database.PlaylistItem(playlist_id=playlist.id, track_id=track.id))
    job = database.DownloadJob(
        user_id=user.id,
        input_url="https://example.test/track",
        target_modern_playlist_id=playlist.id,
        status="completed",
    )
    db_session.add(job)
    db_session.commit()

    playlist_id = playlist.id
    job_id = job.id
    deletion_service.detach_playlist_references(db_session, playlist_id)
    db_session.delete(playlist)
    db_session.commit()

    assert db_session.get(database.Playlist, playlist_id) is None
    assert db_session.query(database.PlaylistItem).filter_by(playlist_id=playlist_id).count() == 0
    assert db_session.get(database.DownloadJob, job_id).target_modern_playlist_id is None


def test_track_delete_applies_reference_lifecycle_policy(db_session):
    owner = database.User(username="track-owner")
    reviewer = database.User(username="reviewer")
    track = database.Track(title="Liked track", filepath="/tmp/liked-track.mp3")
    db_session.add_all([owner, reviewer, track])
    db_session.flush()

    playlist = database.Playlist(name="Favorites", owner_id=owner.id, cover_track_id=track.id)
    room = database.PartyRoom(owner_id=owner.id, name="Room")
    db_session.add_all([playlist, room])
    db_session.flush()
    db_session.add_all(
        [
            database.PlaylistItem(playlist_id=playlist.id, track_id=track.id),
            database.UserFavorite(user_id=owner.id, track_id=track.id),
            database.DownloadJob(
                user_id=owner.id,
                input_url="https://example.test/liked-track",
                resolved_track_id=track.id,
                status="completed",
            ),
            database.TrackDeleteRequest(
                user_id=owner.id,
                track_id=track.id,
                reason="Duplicate",
                reviewed_by_user_id=reviewer.id,
            ),
            database.PartyRoomQueueItem(room_id=room.id, track_id=track.id, added_by_user_id=owner.id),
        ]
    )
    db_session.commit()

    track_id = track.id
    playlist_id = playlist.id
    job_id = db_session.query(database.DownloadJob.id).filter_by(resolved_track_id=track_id).scalar()
    request_id = db_session.query(database.TrackDeleteRequest.id).filter_by(track_id=track_id).scalar()
    deletion_service.detach_track_references(db_session, track_id)
    db_session.delete(track)
    db_session.commit()

    assert db_session.get(database.Track, track_id) is None
    assert db_session.query(database.UserFavorite).filter_by(track_id=track_id).count() == 0
    assert db_session.query(database.PlaylistItem).filter_by(track_id=track_id).count() == 0
    assert db_session.query(database.PartyRoomQueueItem).filter_by(track_id=track_id).count() == 0
    assert db_session.get(database.Playlist, playlist_id).cover_track_id is None
    assert db_session.get(database.DownloadJob, job_id).resolved_track_id is None
    assert db_session.get(database.TrackDeleteRequest, request_id).track_id is None


def test_orm_foreign_keys_document_delete_semantics():
    expected = {
        ("playlist_items", "playlist_id"): "CASCADE",
        ("playlist_items", "track_id"): "CASCADE",
        ("playlists", "cover_track_id"): "SET NULL",
        ("download_jobs", "target_modern_playlist_id"): "SET NULL",
        ("download_jobs", "resolved_track_id"): "SET NULL",
        ("user_favorites", "track_id"): "CASCADE",
        ("track_delete_requests", "track_id"): "SET NULL",
        ("party_room_queue_items", "track_id"): "CASCADE",
    }

    for (table_name, column_name), action in expected.items():
        table = database.Base.metadata.tables[table_name]
        foreign_key = next(iter(table.c[column_name].foreign_keys))
        assert foreign_key.ondelete == action
