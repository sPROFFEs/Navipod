from __future__ import annotations

import database
import wrapped_service
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .core import get_current_user_safe, get_db

router = APIRouter()


@router.get("/api/wrapped/{year}")
async def get_user_wrapped(
    year: int,
    request: Request,
    force_refresh: bool = Query(False),
    db: Session = Depends(get_db),
):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        payload = wrapped_service.get_or_build_user_wrapped_summary(db, user, year, force_refresh=force_refresh)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(payload)


@router.get("/api/wrapped/{year}/party")
async def get_wrapped_party(
    year: int,
    request: Request,
    force_refresh: bool = Query(False),
    db: Session = Depends(get_db),
):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        payload = wrapped_service.get_or_build_party_summary(db, year, force_refresh=force_refresh)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(payload)


@router.post("/admin/api/wrapped/{year}/regenerate")
async def admin_regenerate_wrapped(
    year: int,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user_safe(db, request)
    if not user or not user.is_admin:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    try:
        job_id = wrapped_service.queue_wrapped_regeneration(user.username, year)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    background_tasks.add_task(wrapped_service.run_wrapped_regeneration_job, job_id, year)
    return JSONResponse({"job_id": job_id, "year": year})


@router.get("/admin/api/wrapped/{year}/status")
async def admin_wrapped_status(year: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user or not user.is_admin:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    try:
        year = wrapped_service.normalize_year(year)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    user_count = db.query(database.User).filter(database.User.is_active == True).count()
    party = wrapped_service.get_cached_party_summary(year)
    return JSONResponse(
        {
            "year": year,
            "summary_db": str(wrapped_service.get_wrapped_summary_db_path()),
            "summary_db_exists": wrapped_service.get_wrapped_summary_db_path().exists(),
            "active_user_count": user_count,
            "party_generated_at": party.get("generated_at") if party else None,
        }
    )
