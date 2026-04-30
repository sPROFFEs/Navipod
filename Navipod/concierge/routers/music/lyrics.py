"""Lyrics endpoint — wraps lyrics_service so the frontend never talks
to lrclib directly (CORS, plus we want the shared cache)."""

import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from lyrics_service import get_lyrics
from sqlalchemy.orm import Session

from .core import get_current_user_safe, get_db

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/api/lyrics")
async def lyrics(
    request: Request,
    db: Session = Depends(get_db),
    title: str = Query(...),
    artist: str = Query(...),
    album: str = Query(""),
    duration: float = Query(0.0),
):
    """Returns synced + plain lyrics for a track. Cached per-song
    globally so a popular song gets fetched once for the whole instance.
    Frontend handles the empty-state when both are blank."""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    data = await get_lyrics(
        title=title,
        artist=artist,
        album=album or None,
        duration=duration if duration > 0 else None,
    )
    return JSONResponse(data)
