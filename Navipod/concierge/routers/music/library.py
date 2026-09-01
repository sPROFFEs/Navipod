"""Browsable library facets and tracks."""

import library_service
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .core import get_current_user_safe, get_db

router = APIRouter()


@router.get("/api/library/facets")
def library_facets(
    request: Request,
    kind: str = "artists",
    q: str = "",
    limit: int = 100,
    offset: int = 0,
    sort: str = "name",
    paged: bool = False,
    db: Session = Depends(get_db),
):
    if not get_current_user_safe(db, request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        items = library_service.list_facets(db, kind, q, limit, offset, sort)
        if not paged:
            return JSONResponse(items)
        total = library_service.count_facets(db, kind, q)
        return JSONResponse(
            {
                "items": items,
                "total": total,
                "offset": max(0, offset),
                "limit": limit,
                "has_more": offset + len(items) < total,
            }
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.get("/api/library/tracks")
def library_tracks(
    request: Request,
    artist: str = "",
    album: str = "",
    genre: str = "",
    year: int | None = None,
    q: str = "",
    limit: int = 200,
    offset: int = 0,
    sort: str = "artist",
    paged: bool = False,
    db: Session = Depends(get_db),
):
    if not get_current_user_safe(db, request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    filters = {"artist": artist, "album": album, "genre": genre, "year": year, "query": q}
    items = library_service.list_tracks(db, **filters, limit=limit, offset=offset, sort=sort)
    if not paged:
        return JSONResponse(items)
    total = library_service.count_tracks(db, **filters)
    return JSONResponse(
        {
            "items": items,
            "total": total,
            "offset": max(0, offset),
            "limit": limit,
            "has_more": offset + len(items) < total,
        }
    )
