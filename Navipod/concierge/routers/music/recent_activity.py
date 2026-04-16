"""
Recent playlists and saved radios tracking per user.
"""

from __future__ import annotations

import personalization_service
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .core import get_current_user_safe, get_db

router = APIRouter()


class RecentPlaylistRequest(BaseModel):
    playlist_id: int


class RecentRadioRequest(BaseModel):
    radio_id: str
    name: str = ""
    stream_url: str = ""


@router.get("/api/recent-activity")
async def get_recent_activity(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    return JSONResponse(personalization_service.get_recent_activity_payload(db, user))


@router.post("/api/recent-activity/playlist")
async def track_recent_playlist(req: RecentPlaylistRequest, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    personalization_service.record_recent_playlist(user.username, req.playlist_id)
    return JSONResponse({"status": "ok"})


@router.post("/api/recent-activity/radio")
async def track_recent_radio(req: RecentRadioRequest, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    personalization_service.record_recent_radio(user.username, req.radio_id, req.name.strip(), req.stream_url.strip())
    return JSONResponse({"status": "ok"})


@router.delete("/api/recent-activity/playlist/{playlist_id}")
async def remove_recent_playlist(playlist_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    personalization_service.remove_recent_playlist(user.username, playlist_id)
    return JSONResponse({"status": "ok"})


@router.delete("/api/recent-activity/radio/{radio_id}")
async def remove_recent_radio(radio_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    personalization_service.remove_recent_radio(user.username, radio_id)
    return JSONResponse({"status": "ok"})
