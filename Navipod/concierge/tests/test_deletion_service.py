from datetime import timedelta

import database
import deletion_service
import party_service


def _room_with_tracks(db_session, track_count=2):
    owner = database.User(username=f"delete-owner-{track_count}", hashed_password="unused", is_active=True)
    tracks = [
        database.Track(
            title=f"Track {index}",
            artist="Artist",
            duration=120,
            filepath=f"/tmp/delete-track-{track_count}-{index}.mp3",
        )
        for index in range(track_count)
    ]
    db_session.add_all([owner, *tracks])
    db_session.commit()
    room = party_service.create_room(db_session, owner, "Deletion room", 5, True)
    for track in tracks:
        party_service.add_track(db_session, room, owner, track.id)
    return owner, tracks, room


def _delete_track(db_session, track):
    deletion_service.detach_track_references(db_session, track.id)
    db_session.delete(track)
    db_session.commit()


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


def test_deleting_track_before_current_preserves_current_playback(db_session):
    _owner, tracks, room = _room_with_tracks(db_session)
    room.current_index = 1
    room.playback_status = "playing"
    room.playback_position_ms = 42000
    room.playback_started_at = party_service._utcnow() - timedelta(seconds=2)
    started_at = room.playback_started_at
    previous_revision = room.revision
    db_session.commit()

    _delete_track(db_session, tracks[0])

    items = party_service._ordered_items(room)
    assert [item.position for item in items] == [0]
    assert items[room.current_index].track_id == tracks[1].id
    assert room.current_index == 0
    assert room.playback_status == "playing"
    assert room.playback_position_ms == 42000
    assert room.playback_started_at == started_at
    assert room.revision == previous_revision + 1


def test_deleting_current_track_loads_its_successor(db_session):
    _owner, tracks, room = _room_with_tracks(db_session)
    room.current_index = 0
    room.playback_status = "playing"
    room.playback_position_ms = 42000
    room.playback_started_at = party_service._utcnow() - timedelta(seconds=2)
    previous_revision = room.revision
    db_session.commit()

    _delete_track(db_session, tracks[0])

    items = party_service._ordered_items(room)
    assert items[room.current_index].track_id == tracks[1].id
    assert room.current_index == 0
    assert room.playback_status == "loading"
    assert room.playback_position_ms == 0
    assert room.playback_started_at is None
    assert room.revision == previous_revision + 1


def test_deleting_only_current_track_leaves_empty_room_paused(db_session):
    _owner, tracks, room = _room_with_tracks(db_session, track_count=1)
    room.current_index = 0
    room.playback_status = "playing"
    room.playback_position_ms = 42000
    room.playback_started_at = party_service._utcnow() - timedelta(seconds=2)
    previous_revision = room.revision
    db_session.commit()

    _delete_track(db_session, tracks[0])

    assert party_service._ordered_items(room) == []
    assert room.current_index == -1
    assert room.playback_status == "paused"
    assert room.playback_position_ms == 0
    assert room.playback_started_at is None
    assert room.revision == previous_revision + 1


def test_deleting_current_tail_stops_without_replaying_previous_track(db_session):
    _owner, tracks, room = _room_with_tracks(db_session)
    room.current_index = 1
    room.playback_status = "playing"
    room.playback_position_ms = 42000
    room.playback_started_at = party_service._utcnow() - timedelta(seconds=2)
    previous_revision = room.revision
    db_session.commit()

    _delete_track(db_session, tracks[1])

    items = party_service._ordered_items(room)
    assert [item.track_id for item in items] == [tracks[0].id]
    assert room.current_index == -1
    assert room.playback_status == "paused"
    assert room.playback_position_ms == 0
    assert room.playback_started_at is None
    assert room.revision == previous_revision + 1


def test_deleting_track_removes_duplicate_queue_entries_in_one_transition(db_session):
    owner, tracks, room = _room_with_tracks(db_session)
    party_service.add_track(db_session, room, owner, tracks[0].id)
    items = party_service._ordered_items(room)
    room.current_index = 1
    room.playback_status = "playing"
    room.playback_position_ms = 12000
    previous_revision = room.revision
    db_session.commit()

    _delete_track(db_session, tracks[0])

    items = party_service._ordered_items(room)
    assert [item.track_id for item in items] == [tracks[1].id]
    assert [item.position for item in items] == [0]
    assert room.current_index == 0
    assert room.playback_status == "playing"
    assert room.playback_position_ms == 12000
    assert room.revision == previous_revision + 1


def test_track_delete_detaches_non_party_references(db_session):
    owner = database.User(username="reference-owner")
    reviewer = database.User(username="reference-reviewer")
    track = database.Track(title="Referenced", filepath="/tmp/referenced-track.mp3")
    db_session.add_all([owner, reviewer, track])
    db_session.flush()
    playlist = database.Playlist(name="References", owner_id=owner.id, cover_track_id=track.id)
    db_session.add(playlist)
    db_session.flush()
    job = database.DownloadJob(
        user_id=owner.id,
        input_url="https://example.test/referenced",
        resolved_track_id=track.id,
        status="completed",
    )
    request = database.TrackDeleteRequest(
        user_id=owner.id,
        track_id=track.id,
        reason="Duplicate",
        reviewed_by_user_id=reviewer.id,
    )
    db_session.add_all(
        [
            database.PlaylistItem(playlist_id=playlist.id, track_id=track.id),
            database.UserFavorite(user_id=owner.id, track_id=track.id),
            job,
            request,
        ]
    )
    db_session.commit()

    track_id = track.id
    _delete_track(db_session, track)

    assert db_session.query(database.UserFavorite).filter_by(track_id=track_id).count() == 0
    assert db_session.query(database.PlaylistItem).filter_by(track_id=track_id).count() == 0
    assert db_session.get(database.Playlist, playlist.id).cover_track_id is None
    assert db_session.get(database.DownloadJob, job.id).resolved_track_id is None
    assert db_session.get(database.TrackDeleteRequest, request.id).track_id is None


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
