from __future__ import annotations

import json
from datetime import datetime, timezone

import database

ADMIN_JOB_RETENTION_LIMIT = 200
GLOBAL_OPERATION_LOCK = "admin-global-operation"


def utcnow():
    return datetime.now(timezone.utc)


def create_admin_job(job_type: str, triggered_by: str | None, message: str, details=None):
    db = database.SessionLocal()
    try:
        job = database.AdminJob(
            job_type=job_type,
            status="queued",
            triggered_by=triggered_by,
            message=message,
            details_json=json.dumps(details or {}, ensure_ascii=False),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        _prune_finished_admin_jobs(db, keep=ADMIN_JOB_RETENTION_LIMIT)
        return job.id
    finally:
        db.close()


def _prune_finished_admin_jobs(db, keep: int = ADMIN_JOB_RETENTION_LIMIT):
    keep = max(1, int(keep))
    total_jobs = db.query(database.AdminJob.id).count()
    overflow = total_jobs - keep
    if overflow <= 0:
        return

    old_finished_jobs = (
        db.query(database.AdminJob)
        .filter(database.AdminJob.status.in_(["completed", "failed"]))
        .order_by(database.AdminJob.id.asc())
        .limit(overflow)
        .all()
    )
    if not old_finished_jobs:
        return

    for job in old_finished_jobs:
        db.delete(job)
    db.commit()


def update_admin_job(job_id: int, *, status=None, message=None, details=None, finished=False):
    db = database.SessionLocal()
    try:
        job = db.query(database.AdminJob).filter(database.AdminJob.id == job_id).first()
        if not job:
            return
        if status:
            job.status = status
        if message is not None:
            job.message = message
        if details is not None:
            job.details_json = json.dumps(details, ensure_ascii=False)
        if finished:
            job.finished_at = utcnow()
        db.commit()
    finally:
        db.close()


def update_admin_job_progress(job_id: int, *, message=None, status=None, phase=None, progress=None, extra=None, finished=False):
    db = database.SessionLocal()
    try:
        job = db.query(database.AdminJob).filter(database.AdminJob.id == job_id).first()
        if not job:
            return
        details = {}
        if job.details_json:
            try:
                details = json.loads(job.details_json)
            except Exception:
                details = {}
        if phase is not None:
            details["phase"] = phase
        if progress is not None:
            details["progress"] = progress
        if extra:
            details.update(extra)
        if message:
            details.setdefault("logs", []).append({
                "at": utcnow().isoformat(),
                "message": message,
            })
            job.message = message
        if status:
            job.status = status
        if finished:
            job.finished_at = utcnow()
        job.details_json = json.dumps(details, ensure_ascii=False)
        db.commit()
    finally:
        db.close()


def get_admin_job(db, job_id: int):
    job = db.query(database.AdminJob).filter(database.AdminJob.id == job_id).first()
    if not job:
        return None
    details = {}
    if job.details_json:
        try:
            details = json.loads(job.details_json)
        except Exception:
            details = {"raw": job.details_json}
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "triggered_by": job.triggered_by,
        "message": job.message,
        "details": details,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def get_recent_admin_jobs(db, limit=10):
    jobs = db.query(database.AdminJob).order_by(database.AdminJob.id.desc()).limit(limit).all()
    response = []
    for job in jobs:
        details = {}
        if job.details_json:
            try:
                details = json.loads(job.details_json)
            except Exception:
                details = {"raw": job.details_json}
        response.append({
            "id": job.id,
            "job_type": job.job_type,
            "status": job.status,
            "triggered_by": job.triggered_by,
            "message": job.message,
            "details": details,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        })
    return response


def get_active_operation_lock(db):
    lock = db.query(database.AdminOperationLock).filter(database.AdminOperationLock.name == GLOBAL_OPERATION_LOCK).first()
    if not lock:
        return None
    return {
        "name": lock.name,
        "job_id": lock.job_id,
        "acquired_at": lock.acquired_at.isoformat() if lock.acquired_at else None,
        "expires_at": lock.expires_at.isoformat() if lock.expires_at else None,
    }


def acquire_lock(db, job_id: int | None):
    existing = db.query(database.AdminOperationLock).filter(database.AdminOperationLock.name == GLOBAL_OPERATION_LOCK).first()
    if existing:
        return False
    lock = database.AdminOperationLock(name=GLOBAL_OPERATION_LOCK, job_id=job_id)
    db.add(lock)
    db.commit()
    return True


def release_lock(db):
    db.query(database.AdminOperationLock).filter(database.AdminOperationLock.name == GLOBAL_OPERATION_LOCK).delete(synchronize_session=False)
    db.commit()
