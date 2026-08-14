"""Authenticated party-room API and server-sent event stream."""

import asyncio

import database
import party_service
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .core import get_current_user_safe, get_db

router = APIRouter(prefix="/api/party", tags=["party"])


class CreateRoomRequest(BaseModel):
    name: str | None = Field(default=None, max_length=party_service.MAX_ROOM_NAME)
    max_users: int = Field(default=5, ge=party_service.MIN_ROOM_USERS, le=party_service.MAX_ROOM_USERS)
    allow_guests_queue: bool = True
    playlist_id: int | None = None


class AddTrackRequest(BaseModel):
    track_id: int


class ControlRequest(BaseModel):
    action: str
    position_ms: int = Field(default=0, ge=0)
    expected_item_id: int | None = None


def _user_or_401(db: Session, request: Request):
    user = get_current_user_safe(db, request)
    if not user or user.is_service_account:
        return None, JSONResponse({"error": "Unauthorized"}, status_code=401)
    return user, None


def _error(exc: party_service.PartyError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=exc.status_code)


def _room_payload(room: database.PartyRoom, user: database.User, include_queue: bool = True) -> dict:
    payload = party_service.serialize_room(room, party_service.hub.presence(room.id), include_queue=include_queue)
    payload["is_owner"] = room.owner_id == user.id
    payload["can_add_songs"] = room.owner_id == user.id or bool(room.allow_guests_queue)
    return payload


@router.get("/rooms")
async def list_rooms(request: Request, db: Session = Depends(get_db)):
    user, response = _user_or_401(db, request)
    if response:
        return response
    rooms = db.query(database.PartyRoom).order_by(database.PartyRoom.created_at.desc()).all()
    return JSONResponse({"rooms": [_room_payload(room, user, include_queue=False) for room in rooms]})


@router.post("/rooms")
async def create_room(payload: CreateRoomRequest, request: Request, db: Session = Depends(get_db)):
    user, response = _user_or_401(db, request)
    if response:
        return response
    try:
        room = party_service.create_room(
            db,
            user,
            payload.name,
            payload.max_users,
            payload.allow_guests_queue,
            payload.playlist_id,
        )
        await party_service.hub.open_room(room.id)
        return JSONResponse({"room": _room_payload(room, user)}, status_code=201)
    except party_service.PartyError as exc:
        return _error(exc)


@router.get("/rooms/{room_id}")
async def room_detail(room_id: int, request: Request, db: Session = Depends(get_db)):
    user, response = _user_or_401(db, request)
    if response:
        return response
    try:
        room = party_service.get_room(db, room_id)
        party_service.normalize_playback(room)
        db.commit()
        return JSONResponse({"room": _room_payload(room, user)})
    except party_service.PartyError as exc:
        return _error(exc)


@router.post("/rooms/{room_id}/join")
async def check_join(room_id: int, request: Request, db: Session = Depends(get_db)):
    user, response = _user_or_401(db, request)
    if response:
        return response
    try:
        room = party_service.get_room(db, room_id)
        await party_service.hub.reserve(room, user)
        return JSONResponse({"room": _room_payload(room, user)})
    except party_service.PartyError as exc:
        return _error(exc)


@router.delete("/rooms/{room_id}")
async def delete_room(room_id: int, request: Request, db: Session = Depends(get_db)):
    user, response = _user_or_401(db, request)
    if response:
        return response
    try:
        room = party_service.get_room(db, room_id)
        if room.owner_id != user.id:
            raise party_service.PartyError("Only the room owner can delete it", 403)
        db.delete(room)
        db.commit()
        await party_service.hub.close_room(room_id)
        return JSONResponse({"status": "deleted"})
    except party_service.PartyError as exc:
        return _error(exc)


@router.get("/rooms/{room_id}/tracks")
async def room_track_search(room_id: int, request: Request, q: str = "", db: Session = Depends(get_db)):
    user, response = _user_or_401(db, request)
    if response:
        return response
    try:
        room = party_service.get_room(db, room_id)
        party_service.require_membership(room, user)
        if user.id != room.owner_id and not room.allow_guests_queue:
            raise party_service.PartyError("Only the room owner can add songs", 403)
        return JSONResponse({"tracks": party_service.search_tracks(db, q)})
    except party_service.PartyError as exc:
        return _error(exc)


@router.post("/rooms/{room_id}/queue")
async def add_queue_track(room_id: int, payload: AddTrackRequest, request: Request, db: Session = Depends(get_db)):
    user, response = _user_or_401(db, request)
    if response:
        return response
    try:
        room = party_service.get_room(db, room_id)
        party_service.require_membership(room, user)
        party_service.add_track(db, room, user, payload.track_id)
        await party_service.hub.broadcast(room_id)
        return JSONResponse({"status": "added"}, status_code=201)
    except party_service.PartyError as exc:
        return _error(exc)


@router.delete("/rooms/{room_id}/queue/{item_id}")
async def remove_queue_track(room_id: int, item_id: int, request: Request, db: Session = Depends(get_db)):
    user, response = _user_or_401(db, request)
    if response:
        return response
    try:
        room = party_service.get_room(db, room_id)
        party_service.require_membership(room, user)
        party_service.remove_queue_item(db, room, user, item_id)
        await party_service.hub.broadcast(room_id)
        return JSONResponse({"status": "removed"})
    except party_service.PartyError as exc:
        return _error(exc)


@router.post("/rooms/{room_id}/control")
async def control_playback(room_id: int, payload: ControlRequest, request: Request, db: Session = Depends(get_db)):
    user, response = _user_or_401(db, request)
    if response:
        return response
    try:
        room = party_service.get_room(db, room_id)
        party_service.require_membership(room, user)
        party_service.control_room(
            db,
            room,
            user,
            payload.action,
            payload.position_ms,
            payload.expected_item_id,
            allow_guest_ended=not party_service.hub.is_member(room.id, room.owner_id),
        )
        await party_service.hub.broadcast(room_id)
        return JSONResponse({"status": "ok"})
    except party_service.PartyError as exc:
        return _error(exc)


@router.get("/rooms/{room_id}/events")
async def room_events(room_id: int, request: Request, db: Session = Depends(get_db)):
    user, response = _user_or_401(db, request)
    if response:
        return response
    try:
        room = party_service.get_room(db, room_id)
        queue = await party_service.hub.subscribe(room, user)
        initial = _room_payload(room, user)
    except party_service.PartyError as exc:
        return _error(exc)

    async def events():
        try:
            yield f'event: state\ndata: {{"type":"state","room":{_json(initial)}}}\n\n'
            await party_service.hub.broadcast(room_id)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: state\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            await party_service.hub.unsubscribe(room_id, user.id, queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _json(value: dict) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))
