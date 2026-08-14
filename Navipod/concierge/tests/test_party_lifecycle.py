"""Regression matrix for party-room membership and playback transitions."""

import asyncio
import importlib.util
import json
import sys
import types
from datetime import timedelta
from pathlib import Path

import database
import party_service
import pytest
from sqlalchemy.orm import sessionmaker


def _room_fixture(db_session, *, max_users=5, track_count=3, prefix="life"):
    owner = database.User(username=f"owner-{prefix}", hashed_password="unused", is_active=True)
    guests = [
        database.User(username=f"guest-{prefix}-{index}", hashed_password="unused", is_active=True)
        for index in range(max(2, max_users))
    ]
    tracks = [
        database.Track(
            title=f"Track {index}",
            artist="Lifecycle",
            duration=60,
            filepath=f"/music/lifecycle-{prefix}-{index}.mp3",
        )
        for index in range(track_count)
    ]
    db_session.add_all([owner, *guests, *tracks])
    db_session.commit()
    room = party_service.create_room(db_session, owner, f"Lifecycle {prefix}", max_users, True)
    for track in tracks:
        party_service.add_track(db_session, room, owner, track.id)
    return owner, guests, tracks, room


def _hub_for(db_session, *, leave_grace=0.01, reservation_grace=0.01):
    factory = sessionmaker(bind=db_session.get_bind())
    return party_service.PartyHub(
        db_factory=factory,
        leave_grace_seconds=leave_grace,
        reservation_seconds=reservation_grace,
    )


def _fresh_room(db_session, room_id):
    db_session.expire_all()
    return db_session.query(database.PartyRoom).filter_by(id=room_id).one()


def _load_party_router_module():
    """Load the router without importing the aggregate music package and its optional providers."""
    package_name = "party_router_testpkg"
    package = types.ModuleType(package_name)
    package.__path__ = []
    core = types.ModuleType(f"{package_name}.core")
    core.get_current_user_safe = lambda _db, _request: None
    core.get_db = lambda: None
    sys.modules[package_name] = package
    sys.modules[f"{package_name}.core"] = core
    path = Path(__file__).resolve().parents[1] / "routers" / "music" / "party.py"
    spec = importlib.util.spec_from_file_location(f"{package_name}.party", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_zero_listener_room_persists_and_starts_paused(db_session):
    _, _, _, room = _room_fixture(db_session)
    hub = _hub_for(db_session)

    assert hub.presence(room.id) == {"participants": []}
    assert room.playback_status == "paused"
    assert db_session.query(database.PartyRoom).filter_by(id=room.id).count() == 1


def test_last_listener_departure_pauses_but_does_not_delete_room(db_session):
    owner, _, _, room = _room_fixture(db_session)
    hub = _hub_for(db_session)

    async def scenario():
        queue = await hub.subscribe(room, owner)
        party_service.control_room(db_session, room, owner, "play")
        await hub.unsubscribe(room.id, owner.id, queue)
        await asyncio.sleep(0.03)

    asyncio.run(scenario())
    persisted = _fresh_room(db_session, room.id)
    assert hub.presence(room.id) == {"participants": []}
    assert persisted.playback_status == "paused"


def test_reconnect_inside_grace_period_does_not_pause_room(db_session):
    owner, _, _, room = _room_fixture(db_session)
    hub = _hub_for(db_session, leave_grace=0.04)

    async def scenario():
        first = await hub.subscribe(room, owner)
        party_service.control_room(db_session, room, owner, "play")
        await hub.unsubscribe(room.id, owner.id, first)
        await asyncio.sleep(0.01)
        await hub.subscribe(room, owner)
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert _fresh_room(db_session, room.id).playback_status == "playing"
    assert [user["username"] for user in hub.presence(room.id)["participants"]] == [owner.username]


def test_owner_can_leave_while_guests_keep_listening_then_last_guest_pauses(db_session):
    owner, guests, _, room = _room_fixture(db_session)
    hub = _hub_for(db_session)

    async def scenario():
        owner_queue = await hub.subscribe(room, owner)
        guest_one = await hub.subscribe(room, guests[0])
        guest_two = await hub.subscribe(room, guests[1])
        party_service.control_room(db_session, room, owner, "play")

        await hub.unsubscribe(room.id, owner.id, owner_queue)
        await asyncio.sleep(0.02)
        assert _fresh_room(db_session, room.id).playback_status == "playing"
        assert {p["username"] for p in hub.presence(room.id)["participants"]} == {
            guests[0].username,
            guests[1].username,
        }

        await hub.unsubscribe(room.id, guests[0].id, guest_one)
        await asyncio.sleep(0.02)
        assert _fresh_room(db_session, room.id).playback_status == "playing"

        await hub.unsubscribe(room.id, guests[1].id, guest_two)
        await asyncio.sleep(0.02)

    asyncio.run(scenario())
    assert _fresh_room(db_session, room.id).playback_status == "paused"


def test_multiple_tabs_count_once_and_only_last_tab_departure_pauses(db_session):
    owner, _, _, room = _room_fixture(db_session)
    hub = _hub_for(db_session)

    async def scenario():
        first = await hub.subscribe(room, owner)
        second = await hub.subscribe(room, owner)
        party_service.control_room(db_session, room, owner, "play")
        assert len(hub.presence(room.id)["participants"]) == 1

        await hub.unsubscribe(room.id, owner.id, first)
        await asyncio.sleep(0.02)
        assert _fresh_room(db_session, room.id).playback_status == "playing"

        await hub.unsubscribe(room.id, owner.id, second)
        await asyncio.sleep(0.02)

    asyncio.run(scenario())
    assert _fresh_room(db_session, room.id).playback_status == "paused"


def test_simultaneous_final_departures_pause_once(db_session):
    owner, guests, _, room = _room_fixture(db_session)
    hub = _hub_for(db_session)

    async def scenario():
        first = await hub.subscribe(room, owner)
        second = await hub.subscribe(room, guests[0])
        party_service.control_room(db_session, room, owner, "play")
        revision_before = room.revision
        await asyncio.gather(
            hub.unsubscribe(room.id, owner.id, first),
            hub.unsubscribe(room.id, guests[0].id, second),
        )
        await asyncio.sleep(0.03)
        return revision_before

    revision_before = asyncio.run(scenario())
    persisted = _fresh_room(db_session, room.id)
    assert persisted.playback_status == "paused"
    assert persisted.revision == revision_before + 1


def test_emptying_one_room_does_not_pause_another_active_room(db_session):
    owner_one, _, _, room_one = _room_fixture(db_session, prefix="one")
    owner_two, _, _, room_two = _room_fixture(db_session, prefix="two")
    hub = _hub_for(db_session)

    async def scenario():
        queue_one = await hub.subscribe(room_one, owner_one)
        await hub.subscribe(room_two, owner_two)
        party_service.control_room(db_session, room_one, owner_one, "play")
        party_service.control_room(db_session, room_two, owner_two, "play")
        await hub.unsubscribe(room_one.id, owner_one.id, queue_one)
        await asyncio.sleep(0.03)

    asyncio.run(scenario())
    assert _fresh_room(db_session, room_one.id).playback_status == "paused"
    assert _fresh_room(db_session, room_two.id).playback_status == "playing"


def test_join_reservation_expires_and_releases_capacity(db_session):
    owner, guests, _, room = _room_fixture(db_session, max_users=2)
    hub = _hub_for(db_session, reservation_grace=0.01)

    async def scenario():
        await hub.reserve(room, owner)
        await hub.reserve(room, guests[0])
        assert hub.is_member(room.id, owner.id)
        with pytest.raises(party_service.PartyError):
            await hub.reserve(room, guests[1])
        await asyncio.sleep(0.03)
        assert not hub.is_member(room.id, owner.id)
        await hub.reserve(room, guests[1])

    asyncio.run(scenario())


def test_failed_sse_after_host_starts_playback_pauses_when_reservation_expires(db_session):
    owner, _, _, room = _room_fixture(db_session)
    hub = _hub_for(db_session, reservation_grace=0.01)

    async def scenario():
        await hub.reserve(room, owner)
        party_service.control_room(db_session, room, owner, "play")
        await asyncio.sleep(0.03)

    asyncio.run(scenario())
    assert not hub.is_member(room.id, owner.id)
    assert _fresh_room(db_session, room.id).playback_status == "paused"


def test_inflight_join_prevents_last_disconnect_from_pausing_room(db_session):
    owner, guests, _, room = _room_fixture(db_session)
    hub = _hub_for(db_session, leave_grace=0.01, reservation_grace=0.08)

    async def scenario():
        owner_queue = await hub.subscribe(room, owner)
        party_service.control_room(db_session, room, owner, "play")
        await hub.reserve(room, guests[0])
        await hub.unsubscribe(room.id, owner.id, owner_queue)
        await asyncio.sleep(0.03)
        assert _fresh_room(db_session, room.id).playback_status == "playing"
        await hub.subscribe(room, guests[0])

    asyncio.run(scenario())
    assert _fresh_room(db_session, room.id).playback_status == "playing"


def test_concurrent_joins_never_exceed_fifteen_unique_users(db_session):
    owner, guests, _, room = _room_fixture(db_session, max_users=15)
    extra = database.User(username="guest-overflow", hashed_password="unused", is_active=True)
    db_session.add(extra)
    db_session.commit()
    users = [owner, *guests[:14], extra]
    hub = _hub_for(db_session, reservation_grace=1)

    async def scenario():
        results = await asyncio.gather(*(hub.reserve(room, user) for user in users), return_exceptions=True)
        failures = [result for result in results if isinstance(result, party_service.PartyError)]
        assert len(failures) == 1
        assert failures[0].status_code == 409
        assert sum(hub.is_member(room.id, user.id) for user in users) == 15

    asyncio.run(scenario())


def test_mutations_require_joined_or_reserved_membership(db_session, monkeypatch):
    owner, guests, _, room = _room_fixture(db_session)
    hub = _hub_for(db_session)
    monkeypatch.setattr(party_service, "hub", hub)

    with pytest.raises(party_service.PartyError) as outside:
        party_service.require_membership(room, guests[0])
    assert outside.value.status_code == 409

    async def scenario():
        await hub.reserve(room, guests[0])
        party_service.require_membership(room, guests[0])
        queue = await hub.subscribe(room, guests[0])
        party_service.require_membership(room, guests[0])
        return queue

    asyncio.run(scenario())


def test_protected_endpoints_reject_anonymous_service_and_non_member_users(db_session, monkeypatch):
    from starlette.requests import Request

    owner, guests, tracks, room = _room_fixture(db_session)
    service = database.User(
        username="party-service",
        hashed_password="unused",
        is_active=True,
        is_service_account=True,
    )
    db_session.add(service)
    db_session.commit()
    routes = _load_party_router_module()
    hub = _hub_for(db_session)
    monkeypatch.setattr(party_service, "hub", hub)
    request = Request({"type": "http", "method": "POST", "path": "/api/party", "headers": []})

    async def scenario():
        routes.get_current_user_safe = lambda _db, _request: None
        anonymous = await routes.list_rooms(request, db_session)
        assert anonymous.status_code == 401

        routes.get_current_user_safe = lambda _db, _request: service
        service_response = await routes.list_rooms(request, db_session)
        assert service_response.status_code == 401

        routes.get_current_user_safe = lambda _db, _request: guests[0]
        outside_add = await routes.add_queue_track(
            room.id,
            routes.AddTrackRequest(track_id=tracks[0].id),
            request,
            db_session,
        )
        assert outside_add.status_code == 409
        assert json.loads(outside_add.body)["error"] == "Join the party room before changing it"

        routes.get_current_user_safe = lambda _db, _request: owner
        empty_room_play = await routes.control_playback(
            room.id,
            routes.ControlRequest(action="play"),
            request,
            db_session,
        )
        assert empty_room_play.status_code == 409

    asyncio.run(scenario())


def test_reserved_member_can_mutate_but_guest_still_cannot_control(db_session, monkeypatch):
    from starlette.requests import Request

    owner, guests, tracks, room = _room_fixture(db_session)
    routes = _load_party_router_module()
    hub = _hub_for(db_session, reservation_grace=1)
    monkeypatch.setattr(party_service, "hub", hub)
    request = Request({"type": "http", "method": "POST", "path": "/api/party", "headers": []})

    async def scenario():
        await hub.reserve(room, guests[0])
        routes.get_current_user_safe = lambda _db, _request: guests[0]
        added = await routes.add_queue_track(
            room.id,
            routes.AddTrackRequest(track_id=tracks[0].id),
            request,
            db_session,
        )
        assert added.status_code == 201
        guest_control = await routes.control_playback(
            room.id,
            routes.ControlRequest(action="play"),
            request,
            db_session,
        )
        assert guest_control.status_code == 403

        await hub.reserve(room, owner)
        routes.get_current_user_safe = lambda _db, _request: owner
        owner_control = await routes.control_playback(
            room.id,
            routes.ControlRequest(action="play"),
            request,
            db_session,
        )
        assert owner_control.status_code == 200

    asyncio.run(scenario())


def test_guest_can_end_unknown_duration_only_after_owner_reconnect_grace(db_session, monkeypatch):
    from starlette.requests import Request

    owner, guests, tracks, room = _room_fixture(db_session, track_count=2)
    tracks[0].duration = 0
    db_session.commit()
    routes = _load_party_router_module()
    hub = _hub_for(db_session, leave_grace=0.01)
    monkeypatch.setattr(party_service, "hub", hub)
    routes.get_current_user_safe = lambda _db, _request: guests[0]
    request = Request({"type": "http", "method": "POST", "path": "/api/party", "headers": []})
    first_item = party_service._ordered_items(room)[0]

    async def scenario():
        owner_queue = await hub.subscribe(room, owner)
        await hub.subscribe(room, guests[0])
        while_host_connected = await routes.control_playback(
            room.id,
            routes.ControlRequest(action="ended", expected_item_id=first_item.id),
            request,
            db_session,
        )
        assert while_host_connected.status_code == 403

        await hub.unsubscribe(room.id, owner.id, owner_queue)
        assert not hub.is_connected(room.id, owner.id)
        during_reconnect_grace = await routes.control_playback(
            room.id,
            routes.ControlRequest(action="ended", expected_item_id=first_item.id),
            request,
            db_session,
        )
        assert during_reconnect_grace.status_code == 403

        await asyncio.sleep(0.03)
        after_reconnect_grace = await routes.control_playback(
            room.id,
            routes.ControlRequest(action="ended", expected_item_id=first_item.id),
            request,
            db_session,
        )
        assert after_reconnect_grace.status_code == 200

    asyncio.run(scenario())
    assert room.current_index == 1


def test_deleted_room_disconnects_clients_and_rejects_late_sse_races(db_session):
    owner, guests, _, room = _room_fixture(db_session)
    hub = _hub_for(db_session)

    async def scenario():
        owner_queue = await hub.subscribe(room, owner)
        guest_queue = await hub.subscribe(room, guests[0])
        await hub.reserve(room, guests[1])
        await hub.close_room(room.id)

        for queue in (owner_queue, guest_queue):
            assert json.loads(await queue.get()) == {"type": "deleted", "room_id": room.id}
        assert hub.presence(room.id) == {"participants": []}
        assert not hub.is_member(room.id, owner.id)
        with pytest.raises(party_service.PartyError) as late_subscribe:
            await hub.subscribe(room, owner)
        assert late_subscribe.value.status_code == 404
        await hub.unsubscribe(room.id, owner.id, owner_queue)
        assert not hub._leave_tasks

    asyncio.run(scenario())


def test_recreated_room_can_reuse_a_deleted_sqlite_id(db_session):
    owner, guests, _, room = _room_fixture(db_session)
    hub = _hub_for(db_session)
    deleted_id = room.id

    async def delete_room():
        await hub.close_room(deleted_id)

    asyncio.run(delete_room())
    db_session.delete(room)
    db_session.commit()
    replacement = party_service.create_room(db_session, guests[0], "Replacement", 5, True)
    assert replacement.id == deleted_id

    async def join_replacement():
        await hub.open_room(replacement.id)
        await hub.reserve(replacement, guests[0])
        assert hub.is_member(replacement.id, guests[0].id)

    asyncio.run(join_replacement())


def test_clock_tick_broadcasts_compact_state_to_every_listener(db_session):
    owner, guests, _, room = _room_fixture(db_session)
    hub = _hub_for(db_session)

    async def scenario():
        owner_queue = await hub.subscribe(room, owner)
        guest_queue = await hub.subscribe(room, guests[0])
        party_service.control_room(db_session, room, owner, "play")
        await hub.broadcast(room.id, include_queue=False)
        owner_payload = json.loads(await owner_queue.get())
        guest_payload = json.loads(await guest_queue.get())
        assert owner_payload == guest_payload
        assert owner_payload["type"] == "state"
        assert owner_payload["room"]["active_users"] == 2
        assert "queue" not in owner_payload["room"]

    asyncio.run(scenario())


def test_auto_advance_tick_includes_fresh_queue_snapshot(db_session):
    owner, _, tracks, room = _room_fixture(db_session, track_count=2)
    hub = _hub_for(db_session)
    party_service.control_room(db_session, room, owner, "play")
    room.playback_position_ms = tracks[0].duration * 1000
    room.playback_started_at = party_service._utcnow()
    db_session.commit()

    async def scenario():
        queue = await hub.subscribe(room, owner)
        await hub.broadcast(room.id, include_queue=False)
        payload = json.loads(await queue.get())
        assert payload["room"]["current_index"] == 1
        assert len(payload["room"]["queue"]) == 2

    asyncio.run(scenario())


def test_clock_failure_in_one_room_does_not_stop_other_rooms(monkeypatch):
    class FailingHub:
        _connections = {1: {}, 2: {}}

        def __init__(self):
            self.called = []

        async def broadcast(self, room_id, include_queue=True):
            self.called.append((room_id, include_queue))
            if room_id == 1:
                raise RuntimeError("temporary database failure")

    fake = FailingHub()
    monkeypatch.setattr(party_service, "hub", fake)

    asyncio.run(party_service.party_clock_tick())

    assert fake.called == [(1, False), (2, False)]


@pytest.mark.parametrize("action", ["play", "next", "previous", "seek"])
def test_empty_queue_rejects_actions_that_need_a_track(db_session, action):
    owner = database.User(username=f"empty-{action}", hashed_password="unused", is_active=True)
    db_session.add(owner)
    db_session.commit()
    room = party_service.create_room(db_session, owner, f"Empty {action}", 5, True)

    with pytest.raises(party_service.PartyError):
        party_service.control_room(db_session, room, owner, action, 1000)


def test_removing_current_song_continues_with_successor(db_session):
    owner, _, tracks, room = _room_fixture(db_session)
    party_service.control_room(db_session, room, owner, "play")
    party_service.control_room(db_session, room, owner, "next")
    current_item = party_service._ordered_items(room)[1]

    party_service.remove_queue_item(db_session, room, owner, current_item.id)

    items = party_service._ordered_items(room)
    assert room.playback_status == "playing"
    assert room.current_index == 1
    assert items[room.current_index].track_id == tracks[2].id
    assert room.playback_position_ms == 0


def test_removing_current_tail_stops_instead_of_replaying_previous_song(db_session):
    owner, _, _, room = _room_fixture(db_session, track_count=2)
    party_service.control_room(db_session, room, owner, "play")
    party_service.control_room(db_session, room, owner, "next")
    current_item = party_service._ordered_items(room)[1]

    party_service.remove_queue_item(db_session, room, owner, current_item.id)

    assert room.playback_status == "paused"
    assert room.current_index == -1
    assert len(party_service._ordered_items(room)) == 1


def test_play_after_natural_end_restarts_queue_from_first_song(db_session):
    owner, _, tracks, room = _room_fixture(db_session, track_count=2)
    room.current_index = 1
    room.playback_status = "paused"
    room.playback_position_ms = tracks[1].duration * 1000
    db_session.commit()

    party_service.control_room(db_session, room, owner, "play")

    assert room.playback_status == "playing"
    assert room.current_index == 0
    assert room.playback_position_ms == 0


def test_repeated_play_does_not_rewind_active_room_clock(db_session):
    owner, _, _, room = _room_fixture(db_session)
    party_service.control_room(db_session, room, owner, "play")
    room.playback_started_at -= timedelta(seconds=5)

    party_service.control_room(db_session, room, owner, "play")

    assert party_service.serialize_room(room)["playback_position_ms"] >= 4900


def test_guest_can_end_only_unknown_duration_current_track(db_session):
    owner, guests, tracks, room = _room_fixture(db_session, track_count=2)
    tracks[0].duration = 0
    db_session.commit()
    first_item = party_service._ordered_items(room)[0]

    party_service.control_room(
        db_session,
        room,
        guests[0],
        "ended",
        expected_item_id=first_item.id,
        allow_guest_ended=True,
    )
    assert room.current_index == 1

    second_item = party_service._ordered_items(room)[1]
    with pytest.raises(party_service.PartyError) as forbidden:
        party_service.control_room(
            db_session,
            room,
            guests[0],
            "ended",
            expected_item_id=second_item.id,
            allow_guest_ended=True,
        )
    assert forbidden.value.status_code == 403


def test_host_end_event_advances_zero_duration_track_once(db_session):
    owner, _, tracks, room = _room_fixture(db_session, track_count=2)
    tracks[0].duration = 0
    db_session.commit()
    party_service.control_room(db_session, room, owner, "play")
    first_item = party_service._ordered_items(room)[0]

    party_service.control_room(db_session, room, owner, "ended", expected_item_id=first_item.id)
    revision_after_first = room.revision
    party_service.control_room(db_session, room, owner, "ended", expected_item_id=first_item.id)

    assert room.playback_status == "playing"
    assert room.current_index == 1
    assert room.playback_position_ms == 0
    assert room.revision == revision_after_first


def test_host_end_event_pauses_on_final_song(db_session):
    owner, _, tracks, room = _room_fixture(db_session, track_count=1)
    party_service.control_room(db_session, room, owner, "play")
    item = party_service._ordered_items(room)[0]

    party_service.control_room(db_session, room, owner, "ended", expected_item_id=item.id)

    assert room.playback_status == "paused"
    assert room.current_index == 0
    assert room.playback_position_ms == tracks[0].duration * 1000
