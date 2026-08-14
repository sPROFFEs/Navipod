"""Persistent party-room state and in-process live presence.

SQLite is the source of truth for rooms, queues, and the playback clock.  The
hub only tracks currently connected browsers; losing the process therefore
cannot lose a room or its queue, and startup deliberately pauses stale rooms.
"""

import asyncio
import json
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone

import database
from sqlalchemy import or_
from sqlalchemy.orm import Session

MAX_ROOM_USERS = 15
MIN_ROOM_USERS = 2
MAX_ROOM_NAME = 80
MAX_QUEUE_ITEMS = 500
_write_lock = threading.RLock()
logger = logging.getLogger(__name__)


class PartyError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _utcnow() -> datetime:
    # SQLAlchemy's SQLite DateTime adapter stores naive values. Keep the DB
    # representation naive while sourcing it from an explicit UTC clock.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _track_dict(track: database.Track | None) -> dict | None:
    if not track:
        return None
    return {
        "id": track.id,
        "db_id": track.id,
        "title": track.title or "Unknown title",
        "artist": track.artist or "Unknown artist",
        "album": track.album or "",
        "duration": int(track.duration or 0),
        "thumbnail": f"/api/cover/{track.id}",
        "is_local": True,
    }


def _ordered_items(room: database.PartyRoom) -> list[database.PartyRoomQueueItem]:
    return sorted(room.queue_items, key=lambda item: (item.position, item.id))


def _effective_position_ms(room: database.PartyRoom, now: datetime | None = None) -> int:
    position = max(0, int(room.playback_position_ms or 0))
    if room.playback_status != "playing" or not room.playback_started_at:
        return position
    now = now or _utcnow()
    return position + max(0, int((now - room.playback_started_at).total_seconds() * 1000))


def normalize_playback(room: database.PartyRoom, now: datetime | None = None) -> bool:
    """Materialize elapsed time and advance over tracks that have ended."""
    if room.playback_status != "playing":
        return False
    items = _ordered_items(room)
    if not items or room.current_index < 0 or room.current_index >= len(items):
        room.playback_status = "paused"
        room.current_index = -1 if not items else min(max(room.current_index, 0), len(items) - 1)
        room.playback_position_ms = 0
        room.playback_started_at = None
        room.revision += 1
        return True

    now = now or _utcnow()
    position_ms = _effective_position_ms(room, now)
    changed = False
    while room.current_index < len(items):
        duration_ms = max(0, int(items[room.current_index].track.duration or 0) * 1000)
        if not duration_ms or position_ms < duration_ms:
            break
        position_ms -= duration_ms
        room.current_index += 1
        changed = True

    if room.current_index >= len(items):
        room.current_index = len(items) - 1
        room.playback_status = "paused"
        room.playback_position_ms = max(0, int(items[-1].track.duration or 0) * 1000)
        room.playback_started_at = None
    else:
        room.playback_position_ms = position_ms
        room.playback_started_at = now
    if changed:
        room.revision += 1
    return changed


def serialize_room(room: database.PartyRoom, presence: dict | None = None, include_queue: bool = True) -> dict:
    now = _utcnow()
    items = _ordered_items(room)
    current = items[room.current_index] if 0 <= room.current_index < len(items) else None
    participants = (presence or {}).get("participants", [])
    payload = {
        "id": room.id,
        "name": room.name,
        "owner_id": room.owner_id,
        "owner_username": room.owner.username if room.owner else "Unknown",
        "max_users": room.max_users,
        "allow_guests_queue": bool(room.allow_guests_queue),
        "playback_status": room.playback_status,
        "playback_position_ms": _effective_position_ms(room, now),
        "server_time_ms": int(now.replace(tzinfo=timezone.utc).timestamp() * 1000),
        "current_index": room.current_index,
        "current_track": _track_dict(current.track) if current else None,
        "revision": room.revision,
        "active_users": len(participants),
        "participants": participants,
        "queue_count": len(items),
        "created_at": room.created_at.isoformat() if room.created_at else None,
    }
    if include_queue:
        payload["queue"] = [
            {
                "item_id": item.id,
                "position": index,
                "added_by": item.added_by.username if item.added_by else None,
                "track": _track_dict(item.track),
            }
            for index, item in enumerate(items)
        ]
    return payload


def get_room(db: Session, room_id: int) -> database.PartyRoom:
    room = db.query(database.PartyRoom).filter(database.PartyRoom.id == room_id).first()
    if not room:
        raise PartyError("Party room not found", 404)
    return room


def create_room(
    db: Session,
    owner: database.User,
    name: str | None,
    max_users: int,
    allow_guests_queue: bool,
    playlist_id: int | None = None,
) -> database.PartyRoom:
    if not MIN_ROOM_USERS <= max_users <= MAX_ROOM_USERS:
        raise PartyError(f"User limit must be between {MIN_ROOM_USERS} and {MAX_ROOM_USERS}")
    room_name = (name or f"{owner.username}'s Party").strip()
    if not room_name or len(room_name) > MAX_ROOM_NAME:
        raise PartyError(f"Room name must be between 1 and {MAX_ROOM_NAME} characters")

    with _write_lock:
        if db.query(database.PartyRoom.id).filter(database.PartyRoom.owner_id == owner.id).first():
            raise PartyError("Delete your existing party room before creating another", 409)
        playlist = None
        if playlist_id is not None:
            playlist = (
                db.query(database.Playlist)
                .filter(database.Playlist.id == playlist_id, database.Playlist.owner_id == owner.id)
                .first()
            )
            if not playlist:
                raise PartyError("Playlist not found or not owned by you", 404)
            if len(playlist.items) > MAX_QUEUE_ITEMS:
                raise PartyError(f"Party queues are limited to {MAX_QUEUE_ITEMS} songs")

        room = database.PartyRoom(
            owner_id=owner.id,
            name=room_name,
            max_users=max_users,
            allow_guests_queue=allow_guests_queue,
        )
        db.add(room)
        db.flush()
        if playlist:
            for position, playlist_item in enumerate(sorted(playlist.items, key=lambda item: item.position)):
                db.add(
                    database.PartyRoomQueueItem(
                        room_id=room.id,
                        track_id=playlist_item.track_id,
                        added_by_user_id=owner.id,
                        position=position,
                    )
                )
            if playlist.items:
                room.current_index = 0
        db.commit()
        db.refresh(room)
        return room


def add_track(db: Session, room: database.PartyRoom, user: database.User, track_id: int) -> None:
    if user.id != room.owner_id and not room.allow_guests_queue:
        raise PartyError("Only the room owner can add songs", 403)
    track = db.query(database.Track).filter(database.Track.id == track_id).first()
    if not track:
        raise PartyError("Track not found", 404)
    with _write_lock:
        position = db.query(database.PartyRoomQueueItem).filter_by(room_id=room.id).count()
        if position >= MAX_QUEUE_ITEMS:
            raise PartyError(f"Party queues are limited to {MAX_QUEUE_ITEMS} songs", 409)
        db.add(
            database.PartyRoomQueueItem(
                room_id=room.id,
                track_id=track.id,
                added_by_user_id=user.id,
                position=position,
            )
        )
        if room.current_index < 0:
            room.current_index = 0
            room.playback_position_ms = 0
        room.revision += 1
        db.commit()


def remove_queue_item(db: Session, room: database.PartyRoom, user: database.User, item_id: int) -> None:
    if user.id != room.owner_id:
        raise PartyError("Only the room owner can remove songs", 403)
    with _write_lock:
        items = _ordered_items(room)
        remove_index = next((i for i, item in enumerate(items) if item.id == item_id), -1)
        if remove_index < 0:
            raise PartyError("Queue item not found", 404)
        db.delete(items[remove_index])
        for position, item in enumerate(item for i, item in enumerate(items) if i != remove_index):
            item.position = position
        if len(items) == 1:
            room.current_index = -1
            room.playback_status = "paused"
            room.playback_position_ms = 0
            room.playback_started_at = None
        elif remove_index < room.current_index:
            room.current_index -= 1
        elif remove_index == room.current_index:
            remaining_count = len(items) - 1
            if remove_index >= remaining_count:
                # The current item was the tail. There is no successor to
                # continue with, so end playback instead of replaying an
                # already-consumed previous song.
                room.current_index = -1
                room.playback_status = "paused"
                room.playback_position_ms = 0
                room.playback_started_at = None
            else:
                room.current_index = remove_index
                room.playback_position_ms = 0
                room.playback_started_at = _utcnow() if room.playback_status == "playing" else None
        room.revision += 1
        db.commit()


def control_room(
    db: Session,
    room: database.PartyRoom,
    user: database.User,
    action: str,
    position_ms: int = 0,
    expected_item_id: int | None = None,
    allow_guest_ended: bool = False,
) -> None:
    if user.id != room.owner_id and not (action == "ended" and allow_guest_ended):
        raise PartyError("Only the room owner can control playback", 403)
    with _write_lock:
        # Previous/next are explicit index changes. Normalizing first at the
        # exact end boundary could auto-advance and then apply "next" again,
        # skipping two songs.
        advanced = False
        if action not in {"next", "previous", "ended"}:
            advanced = normalize_playback(room)
        items = _ordered_items(room)
        if action == "play":
            if not items:
                raise PartyError("Add a song before starting playback")
            if room.playback_status == "playing":
                # Repeated browser/media-session play events are idempotent.
                # Resetting playback_started_at here would discard elapsed
                # server time and rewind every listener.
                if advanced:
                    db.commit()
                return
            if room.current_index < 0 or room.current_index >= len(items):
                room.current_index = 0
                room.playback_position_ms = 0
            else:
                duration_ms = max(0, int(items[room.current_index].track.duration or 0) * 1000)
                if duration_ms and room.playback_position_ms >= duration_ms:
                    # A room paused at the natural end should restart the
                    # queue, not briefly play an already-finished resource.
                    room.current_index = 0
                    room.playback_position_ms = 0
            room.playback_status = "playing"
            room.playback_started_at = _utcnow()
        elif action == "pause":
            room.playback_position_ms = _effective_position_ms(room)
            room.playback_status = "paused"
            room.playback_started_at = None
        elif action == "seek":
            if not items or room.current_index < 0:
                raise PartyError("Nothing is playing")
            duration_ms = max(0, int(items[room.current_index].track.duration or 0) * 1000)
            room.playback_position_ms = min(max(0, position_ms), duration_ms or max(0, position_ms))
            room.playback_started_at = _utcnow() if room.playback_status == "playing" else None
        elif action in {"next", "previous", "ended"}:
            if not items:
                raise PartyError("The queue is empty")
            if action == "ended":
                current_item = items[room.current_index] if 0 <= room.current_index < len(items) else None
                if not current_item or expected_item_id != current_item.id:
                    # The server clock or another end event already moved the
                    # room. Treat retries as success without advancing twice.
                    return
                if user.id != room.owner_id and int(current_item.track.duration or 0) > 0:
                    # Guests may only resolve tracks whose duration is unknown
                    # to the server. Known-duration tracks advance by the
                    # authoritative room clock and remain host-controlled.
                    raise PartyError("Only the room owner can control playback", 403)
                if room.current_index + 1 >= len(items):
                    room.playback_status = "paused"
                    room.playback_position_ms = max(0, int(current_item.track.duration or 0) * 1000)
                    room.playback_started_at = None
                else:
                    room.current_index += 1
                    room.playback_position_ms = 0
                    room.playback_started_at = _utcnow() if room.playback_status == "playing" else None
            elif action == "next":
                if room.current_index + 1 >= len(items):
                    room.playback_status = "paused"
                    room.current_index = len(items) - 1
                else:
                    room.current_index += 1
            else:
                room.current_index = max(0, room.current_index - 1)
            if action != "ended":
                room.playback_position_ms = 0
                room.playback_started_at = _utcnow() if room.playback_status == "playing" else None
        else:
            raise PartyError("Unsupported playback action")
        room.revision += 1
        db.commit()


def pause_room(db: Session, room_id: int) -> bool:
    room = db.query(database.PartyRoom).filter(database.PartyRoom.id == room_id).first()
    if not room or room.playback_status != "playing":
        return False
    room.playback_position_ms = _effective_position_ms(room)
    room.playback_status = "paused"
    room.playback_started_at = None
    room.revision += 1
    db.commit()
    return True


def pause_all_rooms(db: Session) -> int:
    rooms = db.query(database.PartyRoom).filter(database.PartyRoom.playback_status == "playing").all()
    for room in rooms:
        room.playback_position_ms = _effective_position_ms(room)
        room.playback_status = "paused"
        room.playback_started_at = None
        room.revision += 1
    if rooms:
        db.commit()
    return len(rooms)


def search_tracks(db: Session, query: str, limit: int = 20) -> list[dict]:
    term = query.strip()
    q = db.query(database.Track)
    if term:
        pattern = f"%{term}%"
        q = q.filter(or_(database.Track.title.ilike(pattern), database.Track.artist.ilike(pattern)))
    return [_track_dict(track) for track in q.order_by(database.Track.artist, database.Track.title).limit(limit).all()]


class PartyHub:
    def __init__(self, db_factory=None, leave_grace_seconds: float = 8, reservation_seconds: float = 12):
        self._db_factory = db_factory or database.SessionLocal
        self._leave_grace_seconds = leave_grace_seconds
        self._reservation_seconds = reservation_seconds
        self._lock = asyncio.Lock()
        self._connections: dict[int, dict[int, set[asyncio.Queue]]] = defaultdict(lambda: defaultdict(set))
        self._names: dict[int, dict[int, str]] = defaultdict(dict)
        self._leave_tasks: dict[tuple[int, int], asyncio.Task] = {}
        self._reservations: dict[int, dict[int, asyncio.Task]] = defaultdict(dict)
        self._closed_rooms: set[int] = set()

    def presence(self, room_id: int) -> dict:
        names = self._names.get(room_id, {})
        return {"participants": [{"id": user_id, "username": name} for user_id, name in names.items()]}

    def is_member(self, room_id: int, user_id: int) -> bool:
        """True while connected, reconnecting during grace, or holding a join reservation."""
        return user_id in self._connections.get(room_id, {}) or user_id in self._reservations.get(room_id, {})

    def is_connected(self, room_id: int, user_id: int) -> bool:
        """True when the user has at least one live event-stream connection."""
        return bool(self._connections.get(room_id, {}).get(user_id))

    async def reserve(self, room: database.PartyRoom, user: database.User) -> None:
        """Hold a capacity slot between the join POST and EventSource GET."""
        async with self._lock:
            if room.id in self._closed_rooms:
                raise PartyError("Party room not found", 404)
            users = self._connections[room.id]
            reservations = self._reservations[room.id]
            if (
                user.id not in users
                and user.id not in reservations
                and len(users) + len(reservations) >= room.max_users
            ):
                raise PartyError("Party room is full", 409)
            old = reservations.pop(user.id, None)
            if old:
                old.cancel()
            if user.id not in users:
                reservations[user.id] = asyncio.create_task(self._expire_reservation(room.id, user.id))

    async def _expire_reservation(self, room_id: int, user_id: int) -> None:
        try:
            await asyncio.sleep(self._reservation_seconds)
            should_pause = False
            async with self._lock:
                self._reservations.get(room_id, {}).pop(user_id, None)
                if not self._reservations.get(room_id):
                    self._reservations.pop(room_id, None)
                should_pause = not self._connections.get(room_id) and not self._reservations.get(room_id)
            if should_pause:
                db = self._db_factory()
                try:
                    pause_room(db, room_id)
                finally:
                    db.close()
        except asyncio.CancelledError:
            return

    async def subscribe(self, room: database.PartyRoom, user: database.User) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        async with self._lock:
            if room.id in self._closed_rooms:
                raise PartyError("Party room not found", 404)
            users = self._connections[room.id]
            reservations = self._reservations.get(room.id, {})
            reserved = reservations.pop(user.id, None)
            if reserved:
                reserved.cancel()
            if not reservations:
                self._reservations.pop(room.id, None)
            occupied = len(users) + len(reservations)
            if user.id not in users and occupied >= room.max_users:
                raise PartyError("Party room is full", 409)
            task = self._leave_tasks.pop((room.id, user.id), None)
            if task:
                task.cancel()
            users[user.id].add(queue)
            self._names[room.id][user.id] = user.username
        return queue

    async def unsubscribe(self, room_id: int, user_id: int, queue: asyncio.Queue) -> None:
        async with self._lock:
            room_connections = self._connections.get(room_id)
            if not room_connections or user_id not in room_connections:
                return
            connections = room_connections[user_id]
            connections.discard(queue)
            if connections:
                return
            old = self._leave_tasks.pop((room_id, user_id), None)
            if old:
                old.cancel()
            self._leave_tasks[(room_id, user_id)] = asyncio.create_task(self._finish_leave(room_id, user_id))

    async def _finish_leave(self, room_id: int, user_id: int) -> None:
        try:
            await asyncio.sleep(self._leave_grace_seconds)
            should_pause = False
            async with self._lock:
                if self._connections.get(room_id, {}).get(user_id):
                    return
                self._connections.get(room_id, {}).pop(user_id, None)
                self._names.get(room_id, {}).pop(user_id, None)
                self._leave_tasks.pop((room_id, user_id), None)
                should_pause = not self._connections.get(room_id) and not self._reservations.get(room_id)
                if should_pause:
                    self._connections.pop(room_id, None)
                    self._names.pop(room_id, None)
            if should_pause:
                db = self._db_factory()
                try:
                    pause_room(db, room_id)
                finally:
                    db.close()
        except asyncio.CancelledError:
            return

    async def broadcast(self, room_id: int, include_queue: bool = True) -> None:
        db = self._db_factory()
        try:
            room = db.query(database.PartyRoom).filter(database.PartyRoom.id == room_id).first()
            if not room:
                payload = {"type": "deleted", "room_id": room_id}
            else:
                advanced = normalize_playback(room)
                db.commit()
                payload = {
                    "type": "state",
                    "room": serialize_room(
                        room,
                        self.presence(room_id),
                        include_queue=include_queue or advanced,
                    ),
                }
        finally:
            db.close()
        data = json.dumps(payload, separators=(",", ":"))
        async with self._lock:
            queues = [q for connections in self._connections.get(room_id, {}).values() for q in connections]
            for queue in queues:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(data)

    async def close_room(self, room_id: int) -> None:
        """Disconnect a deleted room and reject late reservation/SSE races."""
        data = json.dumps({"type": "deleted", "room_id": room_id}, separators=(",", ":"))
        async with self._lock:
            self._closed_rooms.add(room_id)
            queues = [q for connections in self._connections.pop(room_id, {}).values() for q in connections]
            self._names.pop(room_id, None)
            for key, task in list(self._leave_tasks.items()):
                if key[0] == room_id:
                    task.cancel()
                    self._leave_tasks.pop(key, None)
            for task in self._reservations.pop(room_id, {}).values():
                task.cancel()
            for queue in queues:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(data)

    async def open_room(self, room_id: int) -> None:
        """Allow a newly created room to use an ID SQLite previously reused."""
        async with self._lock:
            self._closed_rooms.discard(room_id)


hub = PartyHub()


def require_membership(room: database.PartyRoom, user: database.User) -> None:
    if not hub.is_member(room.id, user.id):
        raise PartyError("Join the party room before changing it", 409)


async def party_clock_scheduler() -> None:
    while True:
        await asyncio.sleep(2)
        await party_clock_tick()


async def party_clock_tick() -> None:
    room_ids = list(hub._connections.keys())
    for room_id in room_ids:
        try:
            # Clock ticks do not resend the entire queue to every listener.
            # A full snapshot is sent only when a command or auto-advance
            # changes structural state.
            await hub.broadcast(room_id, include_queue=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            # One corrupt/deleted room or transient SQLite error must not
            # terminate synchronization for every other active room.
            logger.exception("Party clock tick failed for room %s", room_id)
