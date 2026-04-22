import logging
import os
import shutil
import stat
import subprocess
from datetime import datetime
from pathlib import Path

import auth
import database
import manager
import operations_service
import path_security
import psutil
import track_identity
import wrapped_service
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from navipod_config import settings
from sqlalchemy import text
from sqlalchemy.orm import Session

router = APIRouter(prefix="/admin")
from shared_templates import templates

logger = logging.getLogger(__name__)

MAX_DUPLICATE_VALUE_SCAN = 500
TRACK_DELETE_ROOTS = ("/saas-data/pool", "/saas-data/users")
DOCKER_PURGE_IMAGE = "alpine:3.20"


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


# SECURITY DEPENDENCY
def get_current_admin(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401)

    username = auth.get_username_from_token(token)
    user = auth.get_user_by_username(db, username)

    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Access denied.")
    return user


def get_dir_size(path):
    """Calculates total directory size in bytes."""
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                if entry.is_file():
                    total += entry.stat().st_size
                elif entry.is_dir():
                    total += get_dir_size(entry.path)
    except OSError as e:
        logger.debug("Skipping unreadable path while measuring directory size for %s: %s", path, e)
    return total


def _rmtree_permission_retry(func, path, exc_info):
    exc = exc_info[1]
    if not isinstance(exc, PermissionError):
        raise exc

    os.chmod(path, stat.S_IRWXU)
    func(path)


def _validate_username_path_segment(username: str) -> None:
    if not username or username in {".", ".."} or "/" in username or "\\" in username:
        raise ValueError("Unsafe username path segment")


def _get_host_data_root() -> Path:
    """Resolve the host path mounted as /saas-data in this running container."""
    container_name = os.getenv("SELF_CONTAINER_NAME", "concierge")
    try:
        container = manager.client.containers.get(container_name)
        container.reload()
        for mount in container.attrs.get("Mounts", []):
            if mount.get("Destination") == "/saas-data" and mount.get("Source"):
                return Path(mount["Source"]).resolve()
    except Exception as e:
        logger.warning("Could not inspect host data root from container %s: %s", container_name, e)

    return Path(settings.HOST_DATA_ROOT).resolve()


def _remove_user_container(username: str) -> None:
    _validate_username_path_segment(username)
    container_name = f"navidrome-{username}"
    remove_result = subprocess.run(
        ["docker", "rm", "-f", container_name],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    remove_output = f"{remove_result.stdout or ''}\n{remove_result.stderr or ''}".strip()
    if remove_result.returncode != 0 and "No such container" not in remove_output:
        raise RuntimeError(f"Could not remove {container_name}: {remove_output or remove_result.returncode}")

    inspect_result = subprocess.run(
        ["docker", "container", "inspect", container_name],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if inspect_result.returncode == 0:
        raise RuntimeError(f"Container still exists after removal: {container_name}")

    manager.ip_cache.pop(username, None)


def _purge_user_data_with_docker(username: str) -> None:
    _validate_username_path_segment(username)
    host_data_root = _get_host_data_root()
    host_users_root = path_security.safe_child_path(host_data_root, "users")
    host_user_path = path_security.safe_child_path(host_users_root, username)

    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "sh",
            "-e",
            f"TARGET_NAME={username}",
            "-v",
            f"{host_users_root}:/users:rw",
            DOCKER_PURGE_IMAGE,
            "-c",
            'chmod -R u+rwX "/users/$TARGET_NAME" 2>/dev/null || true; rm -rf -- "/users/$TARGET_NAME"',
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Docker data purge failed for {host_user_path}: {stderr or result.returncode}")


def _purge_user_data(username: str, user_data_path) -> None:
    if not user_data_path.exists():
        return

    try:
        shutil.rmtree(user_data_path, onerror=_rmtree_permission_retry)
    except PermissionError as e:
        logger.warning("Local user data purge hit permission error for %s, retrying via Docker: %s", username, e)
        _purge_user_data_with_docker(username)
    except OSError as e:
        logger.warning("Local user data purge failed for %s, retrying via Docker: %s", username, e)
        _purge_user_data_with_docker(username)

    if user_data_path.exists():
        raise RuntimeError(f"User data path still exists after purge: {user_data_path}")


# DASHBOARD VIEW
@router.get("/")
async def admin_panel(request: Request, db: Session = Depends(get_db)):
    try:
        admin = get_current_admin(request, db)
    except HTTPException:
        return RedirectResponse("/portal")

    # Calculate total disk space for HTML 'max' attribute
    disk = shutil.disk_usage("/saas-data")
    stats = {"disk_total": int(disk.total / (1024**3))}

    users = db.query(database.User).all()

    # Pool Status
    u_gb, l_gb, pct = manager.get_pool_status(db)

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "username": admin.username,
            "is_admin": True,
            "users": users,
            "stats": stats,
            "pool": {"used": u_gb, "limit": l_gb, "percent": pct},
        },
    )


# API ACTIONS
@router.post("/users/create")
async def create_user(
    username: str = Form(...),
    password: str = Form(...),
    is_admin: bool = Form(False),
    db: Session = Depends(get_db),
    admin: database.User = Depends(get_current_admin),
):
    # 1. IF HERE (Validate complexity before creating)
    password = (password or "").strip()
    if not auth.is_password_strong(password):
        return {"error": "Weak password: use 8 chars, uppercase, lowercase, numbers and symbols"}

    existing = auth.get_user_by_username(db, username)
    if existing:
        return {"error": f"User {username} already exists"}

    new_user = auth.create_user_in_db(db, username, password)
    if is_admin:
        new_user.is_admin = True
        db.commit()

    try:
        manager.provision_user_env(username)
    except Exception as e:
        logger.warning("Failed to provision user environment for %s: %s", username, e)

    return {"msg": f"User {username} created successfully"}


@router.post("/users/delete")
async def delete_user(
    user_id: int = Form(...), db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)
):
    user_del = db.query(database.User).filter(database.User.id == user_id).first()

    # L0 Security: Do not delete current admin
    if not user_del or user_del.id == admin.id:
        return {"error": "Action not allowed: you cannot delete yourself"}

    username = user_del.username
    users_root = "/saas-data/users"
    bytes_to_free = 0

    try:
        user_data_path = path_security.safe_child_path(users_root, username)

        # 1. DOCKER PURGE. This is fatal: do not delete DB state if the user container remains alive.
        _remove_user_container(username)

        # 2. CALCULATE SPACE (Before deleting)
        if user_data_path.exists():
            bytes_to_free = get_dir_size(user_data_path)
            # 3. DISK PURGE with an explicit path guard under /saas-data/users.
            _purge_user_data(username, user_data_path)

        # 4. DB PURGE
        db.delete(user_del)
        db.commit()
        manager.invalidate_pool_status_cache()

        if bytes_to_free > 1024**3:
            freed_str = f"{round(bytes_to_free / 1024**3, 2)} GB"
        else:
            freed_str = f"{round(bytes_to_free / 1024**2, 2)} MB"

        return {"msg": f"User {username} deleted. Freed {freed_str}"}

    except Exception as e:
        db.rollback()
        return {"error": f"Purge error: {str(e)}"}


@router.post("/users/reset-password")
async def reset_user_password(
    user_id: int = Form(...),
    new_password: str = Form(...),
    db: Session = Depends(get_db),
    admin: database.User = Depends(get_current_admin),
):
    # 2. IF HERE (Validate complexity before resetting)
    new_password = (new_password or "").strip()
    if not auth.is_password_strong(new_password):
        return {"error": "New password is too weak (use 8 chars, uppercase, lowercase, numbers and symbols)"}

    user_to_edit = db.query(database.User).filter(database.User.id == user_id).first()
    if not user_to_edit:
        return {"error": "User not found"}

    # Hash and update
    user_to_edit.hashed_password = auth.get_password_hash(new_password)
    db.commit()

    return {"msg": f"Password for {user_to_edit.username} updated successfully"}


@router.get("/system")
async def system_monitor(request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request, db)

    # Disk Statistics on data path
    disk = shutil.disk_usage("/saas-data")

    stats = {
        "cpu_usage": psutil.cpu_percent(interval=1),
        "ram": psutil.virtual_memory(),
        "disk_total": round(disk.total / (1024**3), 2),
        "disk_used": round(disk.used / (1024**3), 2),
        "disk_free": round(disk.free / (1024**3), 2),
        "disk_percent": round((disk.used / disk.total) * 100, 1),
    }

    # Get Global Pool Status
    pool_used, pool_limit, pool_pct = manager.get_pool_status(db)
    build_info = operations_service.get_build_info()
    schema_status = operations_service.get_schema_status(db)
    backup_state = operations_service.get_backup_state(db)
    update_state = operations_service.get_update_state(db)
    recent_jobs = operations_service.get_recent_admin_jobs(db, limit=8)
    active_lock = operations_service.get_active_operation_lock(db)
    timezone_options = operations_service.get_timezone_options()
    wrapped_state = wrapped_service.get_wrapped_settings(db)

    return templates.TemplateResponse(
        "system_monitor.html",
        {
            "request": request,
            "stats": stats,
            "pool": {"used": pool_used, "limit": pool_limit, "percent": pool_pct},
            "build": build_info,
            "schema": schema_status,
            "backups": backup_state,
            "updates": update_state,
            "timezone_options": timezone_options,
            "wrapped": wrapped_state,
            "admin_jobs": recent_jobs,
            "active_lock": active_lock,
            "username": admin.username,
            "is_admin": True,
        },
    )


@router.get("/api/system-stats")
async def api_system_stats(request: Request, db: Session = Depends(get_db)):
    """JSON endpoint for live stats polling"""
    # Quick auth check
    token = request.cookies.get("access_token")
    if not token:
        return {"error": "Unauthorized"}

    try:
        username = auth.get_username_from_token(token)
        user = auth.get_user_by_username(db, username)
        if not user or not user.is_admin:
            return {"error": "Forbidden"}
    except Exception as e:
        logger.debug("System stats auth check failed: %s", e)
        return {"error": "Unauthorized"}

    # Collect stats (faster CPU interval for polling)
    pool_used, pool_limit, pool_pct = manager.get_pool_status(db)

    return {
        "cpu_usage": psutil.cpu_percent(interval=0.1),
        "ram": {
            "percent": psutil.virtual_memory().percent,
            "used_gb": round(psutil.virtual_memory().used / (1024**3), 2),
            "total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        },
        "pool": {"used": pool_used, "limit": pool_limit, "percent": pool_pct},
    }


@router.post("/system/purge-storage")
async def purge_storage(db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    """Delete download residue and temporary files."""
    import shutil

    # Directorios a limpiar
    paths_to_clean = ["/tmp", "/app/temp"]  # Ajusta según tu Dockerfile

    # Extensiones de archivos basura (descargas incompletas)
    trash_extensions = [".part", ".ytdl", ".tmp", ".cache"]

    bytes_freed = 0

    try:
        # 1. Limpiar carpetas temporales globales
        for path in paths_to_clean:
            if os.path.exists(path):
                for root, dirs, files in os.walk(path):
                    for f in files:
                        file_path = os.path.join(root, f)
                        bytes_freed += os.path.getsize(file_path)
                        os.remove(file_path)

        # 2. Buscar basura en las carpetas de los usuarios
        users_root = "/saas-data/users"
        for root, dirs, files in os.walk(users_root):
            # Eliminar archivos temporales de motores
            for f in files:
                if any(f.endswith(ext) for ext in trash_extensions):
                    file_path = os.path.join(root, f)
                    bytes_freed += os.path.getsize(file_path)
                    os.remove(file_path)

            # Eliminar carpetas .spotdl-cache que suelen pesar bastante
            for d in dirs:
                if d == ".spotdl-cache":
                    dir_path = os.path.join(root, d)
                    bytes_freed += sum(
                        os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(dir_path) for f in fs
                    )
                    shutil.rmtree(dir_path)

        freed_gb = round(bytes_freed / (1024**3), 3)
        return RedirectResponse(f"/admin/system?msg=Storage purge completed. Freed {freed_gb} GB", status_code=303)

    except Exception as e:
        return RedirectResponse(f"/admin/system?error=Storage purge failed: {str(e)}", status_code=303)


@router.post("/system/pool-limit")
async def update_pool_limit(
    limit_gb: int = Form(...), db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)
):
    """Actualiza el límite total del Pool"""
    try:
        from database import SystemSettings

        settings = db.query(SystemSettings).first()
        if not settings:
            settings = SystemSettings(pool_limit_gb=limit_gb)
            db.add(settings)
        else:
            settings.pool_limit_gb = limit_gb
        db.commit()
        return RedirectResponse(f"/admin/system?msg=Pool limit updated to {limit_gb} GB", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/system?error=Failed to update pool limit: {str(e)}", status_code=303)


@router.post("/system/backups/create")
async def create_backup(
    request: Request, db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)
):
    job_id = operations_service.queue_backup(admin.username, mode="manual")
    return RedirectResponse(f"/admin/system?msg=Backup job #{job_id} queued", status_code=303)


@router.post("/system/backups/restore")
async def restore_backup(
    slot: str = Form(...), db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)
):
    if slot not in {"current", "previous"}:
        return RedirectResponse("/admin/system?error=Invalid backup slot", status_code=303)
    job_id = operations_service.queue_restore(slot, admin.username)
    return RedirectResponse(f"/admin/system?msg=Restore job #{job_id} queued for {slot}", status_code=303)


@router.post("/system/backups/settings")
async def update_backup_settings(
    autobackup_enabled: str = Form("off"),
    autobackup_hour: int = Form(0),
    autobackup_minute: int = Form(0),
    autobackup_timezone: str = Form("UTC"),
    db: Session = Depends(get_db),
    admin: database.User = Depends(get_current_admin),
):
    enabled = str(autobackup_enabled).lower() in {"1", "true", "on", "yes"}
    operations_service.update_autobackup_settings(enabled, autobackup_hour, autobackup_minute, autobackup_timezone)
    state = "enabled" if enabled else "disabled"
    return RedirectResponse(f"/admin/system?msg=Autobackup {state}", status_code=303)


@router.post("/system/wrapped/settings")
async def update_wrapped_settings(
    wrapped_enabled: str = Form("off"),
    wrapped_visible_from_date: str = Form(""),
    wrapped_visible_from_time: str = Form(""),
    wrapped_visible_until_date: str = Form(""),
    wrapped_visible_until_time: str = Form(""),
    wrapped_artist_clip_message: str = Form(""),
    db: Session = Depends(get_db),
    admin: database.User = Depends(get_current_admin),
):
    enabled = str(wrapped_enabled).lower() in {"1", "true", "on", "yes"}

    def combine_datetime(date_value: str, time_value: str) -> str:
        date_value = (date_value or "").strip()
        time_value = (time_value or "").strip()
        if not date_value:
            return ""
        return f"{date_value}T{time_value or '00:00'}"

    wrapped_service.update_wrapped_settings(
        db,
        enabled=enabled,
        visible_from=combine_datetime(wrapped_visible_from_date, wrapped_visible_from_time),
        visible_until=combine_datetime(wrapped_visible_until_date, wrapped_visible_until_time),
        artist_clip_message=wrapped_artist_clip_message,
    )
    state = "enabled" if enabled else "disabled"
    return RedirectResponse(f"/admin/system?msg=Wrapped {state}", status_code=303)


@router.post("/system/wrapped/regenerate")
async def regenerate_wrapped_now(
    background_tasks: BackgroundTasks,
    year: int | None = Form(None),
    db: Session = Depends(get_db),
    admin: database.User = Depends(get_current_admin),
):
    target_year = year or datetime.utcnow().year
    job_id = wrapped_service.queue_wrapped_regeneration(admin.username, target_year)
    background_tasks.add_task(wrapped_service.run_wrapped_regeneration_job, job_id, target_year)
    return RedirectResponse(f"/admin/system/updates/jobs/{job_id}", status_code=303)


@router.post("/system/updates/check")
async def check_updates(
    request: Request, db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)
):
    job_id = operations_service.queue_check_update(admin.username)
    return RedirectResponse(f"/admin/system/updates/jobs/{job_id}", status_code=303)


@router.post("/system/updates/apply")
async def apply_updates(
    request: Request, db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)
):
    job_id = operations_service.queue_apply_update(admin.username)
    return RedirectResponse(f"/admin/system/updates/jobs/{job_id}", status_code=303)


@router.get("/system/updates/jobs/{job_id}")
async def update_job_progress_page(
    job_id: int, request: Request, db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)
):
    job = operations_service.get_admin_job(db, job_id)
    if not job:
        return RedirectResponse("/admin/system?error=Update job not found", status_code=303)
    pool_used, pool_limit, pool_pct = manager.get_pool_status(db)
    updater_monitor_url = operations_service.get_update_monitor_path(job_id)
    updater_job_endpoint = f"/updater/api/jobs/{job_id}?access={operations_service.get_update_monitor_token(job_id)}"
    return templates.TemplateResponse(
        "update_progress.html",
        {
            "request": request,
            "job": job,
            "pool": {"used": pool_used, "limit": pool_limit, "percent": pool_pct},
            "username": admin.username,
            "is_admin": True,
            "updater_monitor_url": updater_monitor_url,
            "updater_job_endpoint": updater_job_endpoint,
        },
    )


@router.get("/api/system/jobs/{job_id}")
async def get_admin_job_status(
    job_id: int, db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)
):
    job = operations_service.get_admin_job(db, job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return JSONResponse(job)


@router.get("/api/update-notification")
async def get_update_notification(db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    state = operations_service.get_update_state(db)
    remote = state.get("remote") or {}
    current = state.get("current") or {}
    payload = {
        "update_available": bool(state.get("update_available")),
        "local_version": current.get("version"),
        "local_commit": current.get("commit"),
        "remote_version": remote.get("version"),
        "remote_release_version": remote.get("release_version"),
        "remote_commit": remote.get("commit"),
        "remote_full_commit": remote.get("full_commit"),
        "behind_count": state.get("behind_count", 0),
        "ahead_count": state.get("ahead_count", 0),
        "checked_at": state.get("checked_at"),
    }
    return JSONResponse(payload)


# --- ADMIN LIBRARY MANAGEMENT ---


def _serialize_track(track):
    return {
        "id": track.id,
        "title": track.title or "Unknown",
        "artist": track.artist or "Unknown",
        "filepath": track.filepath,
        "source_provider": track.source_provider or "unknown",
    }


def _append_duplicate_group(groups_by_members, reason, reason_key, tracks):
    member_ids = tuple(sorted(track.id for track in tracks))
    if len(member_ids) < 2:
        return

    group = groups_by_members.get(member_ids)
    if not group:
        group = {
            "member_ids": member_ids,
            "reasons": [],
            "keys": [],
            "tracks": [_serialize_track(track) for track in tracks],
        }
        groups_by_members[member_ids] = group

    group["reasons"].append(reason)
    group["keys"].append(reason_key)


def _group_tracks_by_value(tracks, attr_name):
    grouped = {}
    for track in tracks:
        grouped.setdefault(getattr(track, attr_name), []).append(track)
    return grouped


def _build_duplicate_scan_result(db: Session, limit_groups: int, progress_callback=None):
    from database import Track
    from sqlalchemy import func

    def report(message, phase, progress):
        if progress_callback:
            progress_callback(message=message, phase=phase, progress=progress)

    groups_by_members = {}

    report("Scanning duplicate source IDs", "source_id", 15)
    source_id_dupes = [
        row[0]
        for row in db.query(Track.source_id)
        .filter(Track.source_id.isnot(None))
        .group_by(Track.source_id)
        .having(func.count() > 1)
        .order_by(func.count().desc())
        .limit(MAX_DUPLICATE_VALUE_SCAN)
        .all()
    ]
    if source_id_dupes:
        tracks_by_source_id = _group_tracks_by_value(
            db.query(Track)
            .filter(Track.source_id.in_(source_id_dupes))
            .order_by(Track.source_id.asc(), Track.id.asc())
            .all(),
            "source_id",
        )
        for source_id in source_id_dupes:
            tracks = tracks_by_source_id.get(source_id, [])
            _append_duplicate_group(groups_by_members, "source_id", f"source_id:{source_id}", tracks)

    report("Scanning duplicate file hashes", "file_hash", 45)
    hash_dupes = [
        row[0]
        for row in db.query(Track.file_hash)
        .filter(Track.file_hash.isnot(None))
        .group_by(Track.file_hash)
        .having(func.count() > 1)
        .order_by(func.count().desc())
        .limit(MAX_DUPLICATE_VALUE_SCAN)
        .all()
    ]
    if hash_dupes:
        tracks_by_hash = _group_tracks_by_value(
            db.query(Track)
            .filter(Track.file_hash.in_(hash_dupes))
            .order_by(Track.file_hash.asc(), Track.id.asc())
            .all(),
            "file_hash",
        )
        for file_hash in hash_dupes:
            tracks = tracks_by_hash.get(file_hash, [])
            _append_duplicate_group(groups_by_members, "file_hash", f"hash:{file_hash[:16]}...", tracks)

    report("Scanning semantic fingerprints", "semantic", 70)
    fingerprint_dupes = [
        row[0]
        for row in db.query(Track.fingerprint)
        .filter(
            Track.fingerprint.isnot(None),
            Track.artist_norm.isnot(None),
            Track.title_norm.isnot(None),
        )
        .group_by(Track.fingerprint)
        .having(func.count() > 1)
        .order_by(func.count().desc())
        .limit(MAX_DUPLICATE_VALUE_SCAN)
        .all()
    ]
    if fingerprint_dupes:
        tracks_by_fingerprint = _group_tracks_by_value(
            db.query(Track)
            .filter(Track.fingerprint.in_(fingerprint_dupes))
            .order_by(Track.fingerprint.asc(), Track.id.asc())
            .all(),
            "fingerprint",
        )
        for fingerprint in fingerprint_dupes:
            tracks = tracks_by_fingerprint.get(fingerprint, [])
            if not tracks:
                continue
            first_track = tracks[0]
            if not track_identity.is_semantic_identity_valid(
                first_track.artist_norm or "", first_track.title_norm or ""
            ):
                continue
            _append_duplicate_group(groups_by_members, "semantic", f"semantic:{fingerprint}", tracks)

    report("Preparing duplicate groups", "summarize", 90)
    groups = []
    for group in groups_by_members.values():
        display_key = " | ".join(group["keys"])
        groups.append(
            {
                "key": display_key,
                "reasons": group["reasons"],
                "tracks": group["tracks"],
            }
        )

    groups.sort(key=lambda item: (-len(item["tracks"]), item["key"]))
    total_count = len(groups)
    return {
        "count": total_count,
        "returned_count": min(total_count, limit_groups),
        "truncated": total_count > limit_groups,
        "groups": groups[:limit_groups],
        "scan_limits": {
            "max_duplicate_values_per_strategy": MAX_DUPLICATE_VALUE_SCAN,
            "limit_groups": limit_groups,
        },
    }


def _run_duplicate_scan_job(job_id: int, limit_groups: int):
    db = database.SessionLocal()
    try:
        operations_service.update_admin_job_progress(
            job_id,
            status="running",
            message="Starting duplicate scan",
            phase="starting",
            progress=5,
        )

        def progress_callback(message, phase, progress):
            operations_service.update_admin_job_progress(
                job_id,
                status="running",
                message=message,
                phase=phase,
                progress=progress,
            )

        result = _build_duplicate_scan_result(db, limit_groups, progress_callback=progress_callback)
        operations_service.update_admin_job_progress(
            job_id,
            status="completed",
            message=f"Duplicate scan completed: {result['count']} group(s)",
            phase="completed",
            progress=100,
            extra={"result": result},
            finished=True,
        )
    except Exception as e:
        operations_service.update_admin_job_progress(
            job_id,
            status="failed",
            message=f"Duplicate scan failed: {str(e)}",
            phase="failed",
            progress=100,
            extra={"error": str(e)},
            finished=True,
        )
    finally:
        db.close()


def _query_tracks_with_fts(db, raw_query: str, limit: int):
    normalized = " ".join(token for token in raw_query.strip().split() if token)
    if not normalized:
        return []

    try:
        stmt = text("""
            SELECT t.id, t.title, t.artist, t.filepath, t.source_provider
            FROM tracks_fts f
            JOIN tracks t ON t.id = f.rowid
            WHERE tracks_fts MATCH :query
            ORDER BY bm25(tracks_fts)
            LIMIT :limit
        """)
        rows = db.execute(stmt, {"query": normalized, "limit": limit}).fetchall()
        return [
            {
                "id": row.id,
                "title": row.title or "Unknown",
                "artist": row.artist or "Unknown",
                "filepath": row.filepath,
                "source_provider": row.source_provider or "unknown",
            }
            for row in rows
        ]
    except Exception:
        return []


@router.get("/api/library/search")
async def admin_search_library(
    q: str = "", db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)
):
    """Search all tracks in the Pool (Admin only)"""
    from database import Track
    from fastapi.responses import JSONResponse

    if not q:
        tracks = db.query(Track).order_by(Track.created_at.desc(), Track.id.desc()).limit(50).all()
        payload = [_serialize_track(t) for t in tracks]
    else:
        payload = _query_tracks_with_fts(db, q, 100)
        if not payload:
            query = q.lower()
            tracks = (
                db.query(Track)
                .filter(
                    (Track.title.ilike(f"%{query}%"))
                    | (Track.artist.ilike(f"%{query}%"))
                    | (Track.album.ilike(f"%{query}%"))
                )
                .order_by(Track.created_at.desc(), Track.id.desc())
                .limit(100)
                .all()
            )
            payload = [_serialize_track(t) for t in tracks]

    return JSONResponse(payload)


@router.delete("/api/library/track/{track_id}")
async def admin_delete_track(
    track_id: int, db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)
):
    """Delete a track from DB and disk (Admin only)"""
    from database import Track
    from fastapi.responses import JSONResponse

    track = db.query(Track).filter(Track.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    filepath = track.filepath

    # Delete from database (cascades to playlist items)
    db.delete(track)
    db.commit()

    # Delete file from disk only if the DB path is inside an allowed media root.
    safe_filepath = None
    if filepath:
        for root in TRACK_DELETE_ROOTS:
            try:
                candidate = path_security.resolve_under(filepath, root)
                if candidate.exists() and candidate.is_file():
                    safe_filepath = candidate
                    break
            except path_security.UnsafePathError:
                continue

    if filepath and not safe_filepath:
        manager.invalidate_pool_status_cache()
        return JSONResponse(
            {
                "success": True,
                "warning": "DB deleted but file removal was skipped because the path is outside allowed media roots",
            }
        )

    if safe_filepath:
        try:
            safe_filepath.unlink()
            manager.invalidate_pool_status_cache()
        except Exception as e:
            return JSONResponse({"success": True, "warning": f"DB deleted but file removal failed: {str(e)}"})

    return JSONResponse({"success": True, "message": "Track deleted successfully"})


@router.get("/api/library/duplicates")
async def admin_find_duplicates(
    limit_groups: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: database.User = Depends(get_current_admin),
):
    """Find duplicate tracks by source_id, file_hash, or semantic fingerprint (Admin only)"""
    return JSONResponse(_build_duplicate_scan_result(db, limit_groups))


@router.post("/api/library/duplicates/jobs")
async def admin_find_duplicates_job(
    background_tasks: BackgroundTasks,
    limit_groups: int = Query(50, ge=1, le=200),
    admin: database.User = Depends(get_current_admin),
):
    """Run the duplicate scan out of the request path so large libraries do not block the admin UI."""
    job_id = operations_service.create_admin_job(
        "duplicate_scan",
        admin.username,
        "Duplicate scan queued",
        {"phase": "queued", "progress": 0, "limit_groups": limit_groups},
    )
    background_tasks.add_task(_run_duplicate_scan_job, job_id, limit_groups)
    return JSONResponse({"job_id": job_id})
