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


@router.get("/api/tracks/delete-requests/mine")
async def list_my_track_delete_requests(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    rows = (
        db.query(database.TrackDeleteRequest)
        .filter(database.TrackDeleteRequest.user_id == user.id)
        .order_by(database.TrackDeleteRequest.requested_at.desc(), database.TrackDeleteRequest.id.desc())
        .limit(200)
        .all()
    )

    payload = []
    for row in rows:
        payload.append(
            {
                "id": int(row.id),
                "track_id": int(row.track_id) if row.track_id else None,
                "track_title": row.track_title or "Unknown Track",
                "track_artist": row.track_artist or "Unknown Artist",
                "reason": row.reason or "",
                "status": row.status or "pending",
                "review_note": row.review_note or "",
                "requested_at": row.requested_at.isoformat() if row.requested_at else None,
                "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
            }
        )
    return JSONResponse({"items": payload})


@router.post("/api/tracks/delete-requests/ack")
async def acknowledge_my_track_delete_responses(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    rows = (
        db.query(database.TrackDeleteRequest)
        .filter(
            database.TrackDeleteRequest.user_id == user.id,
            database.TrackDeleteRequest.status.in_(["approved", "rejected"]),
            database.TrackDeleteRequest.user_seen_at.is_(None),
        )
        .all()
    )
    for row in rows:
        row.user_seen_at = now
    db.commit()
    return JSONResponse({"ok": True, "updated": len(rows)})


@router.get("/api/tracks/delete-requests/unseen-count")
async def my_track_delete_responses_unseen_count(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    unseen = (
        db.query(database.TrackDeleteRequest)
        .filter(
            database.TrackDeleteRequest.user_id == user.id,
            database.TrackDeleteRequest.status.in_(["approved", "rejected"]),
            database.TrackDeleteRequest.user_seen_at.is_(None),
        )
        .count()
    )
    return JSONResponse({"unseen_count": int(unseen)})
