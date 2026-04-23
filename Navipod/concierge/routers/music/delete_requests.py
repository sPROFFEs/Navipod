"""
User-facing track deletion request API.
"""

import database
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .core import get_current_user_safe, get_db

router = APIRouter()


class TrackDeleteRequestPayload(BaseModel):
    reason: str


@router.post("/api/tracks/{track_id}/delete-request")
async def create_track_delete_request(
    track_id: int,
    payload: TrackDeleteRequestPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    reason = (payload.reason or "").strip()
    if len(reason) < 8:
        return JSONResponse({"error": "Please provide a clearer reason (min 8 chars)."}, status_code=400)
    if len(reason) > 1000:
        return JSONResponse({"error": "Reason is too long (max 1000 chars)."}, status_code=400)

    track = db.query(database.Track).filter(database.Track.id == track_id).first()
    if not track:
        return JSONResponse({"error": "Track not found."}, status_code=404)

    existing_pending = (
        db.query(database.TrackDeleteRequest)
        .filter(
            database.TrackDeleteRequest.user_id == user.id,
            database.TrackDeleteRequest.track_id == track.id,
            database.TrackDeleteRequest.status == "pending",
        )
        .first()
    )
    if existing_pending:
        return JSONResponse({"error": "You already have a pending request for this track."}, status_code=409)

    new_request = database.TrackDeleteRequest(
        user_id=user.id,
        track_id=track.id,
        track_title=track.title,
        track_artist=track.artist,
        reason=reason,
        status="pending",
    )
    db.add(new_request)
    db.commit()

    return JSONResponse({"ok": True, "message": "Deletion request sent to admins."})
