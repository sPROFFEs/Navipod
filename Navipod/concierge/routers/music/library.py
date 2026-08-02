"""Browsable library facets and tracks."""

import library_service
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .core import get_current_user_safe, get_db

router = APIRouter()


@router.get("/api/library/facets")
async def library_facets(
    request: Request,
    kind: str = "artists",
    q: str = "",
    limit: int = 100,
    db: Session = Depends(get_db),
):
    if not get_current_user_safe(db, request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        return JSONResponse(library_service.list_facets(db, kind, q, limit))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.get("/api/library/tracks")
async def library_tracks(
    request: Request,
    artist: str = "",
    album: str = "",
    genre: str = "",
    year: int | None = None,
    q: str = "",
    limit: int = 200,
    db: Session = Depends(get_db),
):
    if not get_current_user_safe(db, request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return JSONResponse(
        library_service.list_tracks(db, artist=artist, album=album, genre=genre, year=year, query=q, limit=limit)
    )
