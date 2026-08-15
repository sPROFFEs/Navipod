import asyncio
from datetime import timedelta

import database
import party_service
import pytest


def _library(db_session):
    owner = database.User(username="owner", hashed_password="unused", is_active=True)
    guest = database.User(username="guest", hashed_password="unused", is_active=True)
    tracks = [
        database.Track(title="First", artist="Artist", duration=120, filepath="/music/first.mp3"),
        database.Track(title="Second", artist="Artist", duration=180, filepath="/music/second.mp3"),
    ]
    db_session.add_all([owner, guest, *tracks])
    db_session.commit()
    playlist = database.Playlist(name="Seed", owner_id=owner.id)
    db_session.add(playlist)
    db_session.flush()
    db_session.add_all(
        [
            database.PlaylistItem(playlist_id=playlist.id, track_id=track.id, position=position)
            for position, track in enumerate(tracks)
        ]
    )
    db_session.commit()
    return owner, guest, tracks, playlist


def _start_room(db_session, room, owner):
    party_service.control_room(db_session, room, owner, "play")
    item = party_service._ordered_items(room)[room.current_index]
    party_service.control_room(db_session, room, owner, "ready", expected_item_id=item.id)


def test_room_is_unique_per_owner_and_can_seed_owned_playlist(db_session):
    owner, _, tracks, playlist = _library(db_session)

    room = party_service.create_room(db_session, owner, "Friday", 6, True, playlist.id)

    assert room.owner_id == owner.id
    assert room.current_index == 0
    assert [item.track_id for item in party_service._ordered_items(room)] == [track.id for track in tracks]
    with pytest.raises(party_service.PartyError) as duplicate:
        party_service.create_room(db_session, owner, "Another", 6, True)
    assert duplicate.value.status_code == 409


def test_room_limits_and_playlist_ownership_are_enforced(db_session):
    owner, guest, _, playlist = _library(db_session)

    with pytest.raises(party_service.PartyError):
        party_service.create_room(db_session, owner, "Too large", 16, True)
    with pytest.raises(party_service.PartyError) as not_owned:
        party_service.create_room(db_session, guest, "Borrowed", 5, True, playlist.id)
    assert not_owned.value.status_code == 404


def test_guest_queue_permission_and_owner_controls(db_session):
    owner, guest, tracks, _ = _library(db_session)
    room = party_service.create_room(db_session, owner, "Locked", 5, False)

    with pytest.raises(party_service.PartyError) as forbidden:
        party_service.add_track(db_session, room, guest, tracks[0].id)
    assert forbidden.value.status_code == 403

    party_service.add_track(db_session, room, owner, tracks[0].id)
    _start_room(db_session, room, owner)
    room.playback_started_at = party_service._utcnow() - timedelta(seconds=4)
    snapshot = party_service.serialize_room(room)
    assert snapshot["playback_status"] == "playing"
    assert 3900 <= snapshot["playback_position_ms"] <= 4500

    with pytest.raises(party_service.PartyError) as guest_control:
        party_service.control_room(db_session, room, guest, "pause")
    assert guest_control.value.status_code == 403


def test_queue_size_is_bounded(db_session, monkeypatch):
    owner, _, tracks, _ = _library(db_session)
    room = party_service.create_room(db_session, owner, "Bounded", 5, True)
    monkeypatch.setattr(party_service, "MAX_QUEUE_ITEMS", 1)
    party_service.add_track(db_session, room, owner, tracks[0].id)

    with pytest.raises(party_service.PartyError) as full:
        party_service.add_track(db_session, room, owner, tracks[1].id)
    assert full.value.status_code == 409


def test_normalize_advances_queue_and_pauses_at_end(db_session):
    owner, _, tracks, playlist = _library(db_session)
    room = party_service.create_room(db_session, owner, "Clock", 5, True, playlist.id)
    _start_room(db_session, room, owner)
    room.playback_started_at = party_service._utcnow() - timedelta(seconds=125)

    assert party_service.normalize_playback(room) is True
    assert room.current_index == 1
    assert room.playback_status == "loading"
    assert room.playback_position_ms == 0

    item = party_service._ordered_items(room)[room.current_index]
    party_service.control_room(db_session, room, owner, "ready", expected_item_id=item.id)
    room.playback_started_at = party_service._utcnow() - timedelta(seconds=180)
    assert party_service.normalize_playback(room) is True
    assert room.playback_status == "paused"
    assert room.current_index == 1
    assert room.playback_position_ms == tracks[1].duration * 1000


def test_empty_rooms_persist_but_startup_pauses_their_clock(db_session):
    owner, _, tracks, _ = _library(db_session)
    room = party_service.create_room(db_session, owner, "Persistent", 5, True)
    party_service.add_track(db_session, room, owner, tracks[0].id)
    _start_room(db_session, room, owner)

    assert party_service.pause_all_rooms(db_session) == 1
    assert db_session.query(database.PartyRoom).filter_by(id=room.id).one().playback_status == "paused"


def test_hub_counts_users_not_browser_tabs_and_enforces_capacity(db_session):
    owner, guest, _, _ = _library(db_session)
    third = database.User(username="third", hashed_password="unused", is_active=True)
    db_session.add(third)
    db_session.commit()
    room = party_service.create_room(db_session, owner, "Small", 2, True)
    hub = party_service.PartyHub()

    async def scenario():
        await hub.subscribe(room, owner)
        await hub.subscribe(room, owner)
        await hub.subscribe(room, guest)
        assert len(hub.presence(room.id)["participants"]) == 2
        with pytest.raises(party_service.PartyError) as full:
            await hub.subscribe(room, third)
        assert full.value.status_code == 409

    asyncio.run(scenario())


def test_join_reservations_close_the_post_to_event_stream_capacity_race(db_session):
    owner, guest, _, _ = _library(db_session)
    third = database.User(username="third", hashed_password="unused", is_active=True)
    db_session.add(third)
    db_session.commit()
    room = party_service.create_room(db_session, owner, "Reserved", 2, True)
    hub = party_service.PartyHub()

    async def scenario():
        await hub.reserve(room, owner)
        await hub.reserve(room, guest)
        with pytest.raises(party_service.PartyError) as full:
            await hub.reserve(room, third)
        assert full.value.status_code == 409
        await hub.subscribe(room, owner)
        await hub.subscribe(room, guest)

    asyncio.run(scenario())
