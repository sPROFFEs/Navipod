from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text
import database, auth, manager
import psutil
import shutil
import subprocess
import os
import operations_service
import track_identity

router = APIRouter(prefix="/admin")
from shared_templates import templates

def get_db():
    db = database.SessionLocal()
    try: yield db
    finally: db.close()

# SECURITY DEPENDENCY
def get_current_admin(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token: raise HTTPException(status_code=401)
    
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
    except Exception: pass
    return total

# DASHBOARD VIEW
@router.get("/")
async def admin_panel(request: Request, db: Session = Depends(get_db)):
    try:
        admin = get_current_admin(request, db)
    except:
        return RedirectResponse("/portal")

    # Calculate total disk space for HTML 'max' attribute
    disk = shutil.disk_usage("/saas-data")
    stats = {
        "disk_total": int(disk.total / (1024**3))
    }

    users = db.query(database.User).all()
    
    # Pool Status
    u_gb, l_gb, pct = manager.get_pool_status(db)
    
    return templates.TemplateResponse("admin.html", {
        "request": request, 
        "username": admin.username,
        "is_admin": True,
        "users": users,
        "stats": stats,
        "pool": {"used": u_gb, "limit": l_gb, "percent": pct}
    })

# API ACTIONS
@router.post("/users/create")
async def create_user(
    username: str = Form(...), 
    password: str = Form(...), 
    is_admin: bool = Form(False),
    db: Session = Depends(get_db),
    admin: database.User = Depends(get_current_admin)
):
    # 1. IF HERE (Validate complexity before creating)
    if not auth.is_password_strong(password):
        return {"error": "Weak password: use 8 chars, uppercase, numbers and symbols"}

    existing = auth.get_user_by_username(db, username)
    if existing: return {"error": f"User {username} already exists"}
    
    new_user = auth.create_user_in_db(db, username, password)
    if is_admin:
        new_user.is_admin = True
        db.commit()
    
    try: manager.provision_user_env(username)
    except: pass

    return {"msg": f"User {username} created successfully"}

@router.post("/users/delete")
async def delete_user(
    user_id: int = Form(...),
    db: Session = Depends(get_db),
    admin: database.User = Depends(get_current_admin)
):
    user_del = db.query(database.User).filter(database.User.id == user_id).first()
    
    # L0 Security: Do not delete current admin
    if not user_del or user_del.id == admin.id:
        return {"error": "Action not allowed: you cannot delete yourself"}

    username = user_del.username
    user_data_path = f"/saas-data/users/{username}"
    bytes_to_free = 0

    try:
        # 1. DOCKER PURGE
        subprocess.run(["docker", "rm", "-f", f"navidrome-{username}"], check=False, capture_output=True)

        # 2. CALCULATE SPACE (Before deleting)
        if os.path.exists(user_data_path):
            bytes_to_free = get_dir_size(user_data_path)
            # 3. DISK PURGE (Using rm -rf for robustness against permissions)
            subprocess.run(["rm", "-rf", user_data_path], check=False)

        # 4. DB PURGE
        db.delete(user_del)
        db.commit()

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
    admin: database.User = Depends(get_current_admin)
):
    # 2. IF HERE (Validate complexity before resetting)
    if not auth.is_password_strong(new_password):
        return {"error": "New password is too weak (use 8 chars, uppercase, numbers and symbols)"}

    user_to_edit = db.query(database.User).filter(database.User.id == user_id).first()
    if not user_to_edit:
        return {"error": "User not found"}
    
    # Hash and update
    user_to_edit.hashed_password = auth.get_password_hash(new_password)
    db.commit()
    
    return {"msg": f"Password for {user_to_edit.username} updated successfully"}

@router.post("/system/flush-ram")
async def flush_ram(db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    """Clears RAM cache (Requires root privileges on host)"""
    try:
        # Sincroniza y limpia caches
        subprocess.run(["sync"], check=True)
        # Nota: En Docker, esto solo funciona si el contenedor tiene privilegios o mapeo de /proc
        # Si falla, es normal por restricciones de seguridad de Docker.
        os.system("echo 3 > /proc/sys/vm/drop_caches")
        return RedirectResponse("/admin/system?msg=RAM Cache cleared successfully", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/admin/system?error=Could not clear RAM: {str(e)}", status_code=303)


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
        "disk_percent": round((disk.used / disk.total) * 100, 1)
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
    
    return templates.TemplateResponse("system_monitor.html", {
        "request": request, 
        "stats": stats,
        "pool": {"used": pool_used, "limit": pool_limit, "percent": pool_pct},
        "build": build_info,
        "schema": schema_status,
        "backups": backup_state,
        "updates": update_state,
        "timezone_options": timezone_options,
        "admin_jobs": recent_jobs,
        "active_lock": active_lock,
        "username": admin.username,
        "is_admin": True
    })

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
    except:
        return {"error": "Unauthorized"}
    
    # Collect stats (faster CPU interval for polling)
    disk = shutil.disk_usage("/saas-data")
    pool_used, pool_limit, pool_pct = manager.get_pool_status(db)
    
    return {
        "cpu_usage": psutil.cpu_percent(interval=0.1),
        "ram": {
            "percent": psutil.virtual_memory().percent,
            "used_gb": round(psutil.virtual_memory().used / (1024**3), 2),
            "total_gb": round(psutil.virtual_memory().total / (1024**3), 2)
        },
        "pool": {
            "used": pool_used,
            "limit": pool_limit,
            "percent": pool_pct
        }
    }

@router.post("/system/purge-storage")
async def purge_storage(db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    """Delete download residue and temporary files."""
    import shutil
    
    # Directorios a limpiar
    paths_to_clean = ["/tmp", "/app/temp"] # Ajusta según tu Dockerfile
    
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
                    bytes_freed += sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(dir_path) for f in fs)
                    shutil.rmtree(dir_path)

        freed_gb = round(bytes_freed / (1024**3), 3)
        return RedirectResponse(f"/admin/system?msg=Storage purge completed. Freed {freed_gb} GB", status_code=303)
    
    except Exception as e:
        return RedirectResponse(f"/admin/system?error=Storage purge failed: {str(e)}", status_code=303)

@router.post("/system/pool-limit")
async def update_pool_limit(limit_gb: int = Form(...), db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
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
async def create_backup(request: Request, db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    job_id = operations_service.queue_backup(admin.username, mode="manual")
    return RedirectResponse(f"/admin/system?msg=Backup job #{job_id} queued", status_code=303)


@router.post("/system/backups/restore")
async def restore_backup(
    slot: str = Form(...),
    db: Session = Depends(get_db),
    admin: database.User = Depends(get_current_admin)
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
    admin: database.User = Depends(get_current_admin)
):
    enabled = str(autobackup_enabled).lower() in {"1", "true", "on", "yes"}
    operations_service.update_autobackup_settings(enabled, autobackup_hour, autobackup_minute, autobackup_timezone)
    state = "enabled" if enabled else "disabled"
    return RedirectResponse(f"/admin/system?msg=Autobackup {state}", status_code=303)


@router.post("/system/updates/check")
async def check_updates(request: Request, db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    job_id = operations_service.queue_check_update(admin.username)
    return RedirectResponse(f"/admin/system/updates/jobs/{job_id}", status_code=303)


@router.post("/system/updates/apply")
async def apply_updates(request: Request, db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    job_id = operations_service.queue_apply_update(admin.username)
    return RedirectResponse(f"/admin/system/updates/jobs/{job_id}", status_code=303)


@router.get("/system/updates/jobs/{job_id}")
async def update_job_progress_page(job_id: int, request: Request, db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    job = operations_service.get_admin_job(db, job_id)
    if not job:
        return RedirectResponse("/admin/system?error=Update job not found", status_code=303)
    if operations_service.is_updater_monitor_available(job_id):
        return RedirectResponse(operations_service.get_update_monitor_path(job_id), status_code=303)
    pool_used, pool_limit, pool_pct = manager.get_pool_status(db)
    return templates.TemplateResponse("update_progress.html", {
        "request": request,
        "job": job,
        "pool": {"used": pool_used, "limit": pool_limit, "percent": pool_pct},
        "username": admin.username,
        "is_admin": True,
    })


@router.get("/api/system/jobs/{job_id}")
async def get_admin_job_status(job_id: int, db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
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
        return [{
            "id": row.id,
            "title": row.title or "Unknown",
            "artist": row.artist or "Unknown",
            "filepath": row.filepath,
            "source_provider": row.source_provider or "unknown",
        } for row in rows]
    except Exception:
        return []

@router.get("/api/library/search")
async def admin_search_library(q: str = "", db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    """Search all tracks in the Pool (Admin only)"""
    from fastapi.responses import JSONResponse
    from database import Track
    
    if not q:
        tracks = db.query(Track).order_by(Track.created_at.desc(), Track.id.desc()).limit(50).all()
        payload = [_serialize_track(t) for t in tracks]
    else:
        payload = _query_tracks_with_fts(db, q, 100)
        if not payload:
            query = q.lower()
            tracks = db.query(Track).filter(
                (Track.title.ilike(f"%{query}%")) | (Track.artist.ilike(f"%{query}%")) | (Track.album.ilike(f"%{query}%"))
            ).order_by(Track.created_at.desc(), Track.id.desc()).limit(100).all()
            payload = [_serialize_track(t) for t in tracks]
    
    return JSONResponse(payload)


@router.delete("/api/library/track/{track_id}")
async def admin_delete_track(track_id: int, db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    """Delete a track from DB and disk (Admin only)"""
    from fastapi.responses import JSONResponse
    from database import Track
    
    track = db.query(Track).filter(Track.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")
    
    filepath = track.filepath
    
    # Delete from database (cascades to playlist items)
    db.delete(track)
    db.commit()
    
    # Delete file from disk
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception as e:
            return JSONResponse({"success": True, "warning": f"DB deleted but file removal failed: {str(e)}"})
    
    return JSONResponse({"success": True, "message": "Track deleted successfully"})


@router.get("/api/library/duplicates")
async def admin_find_duplicates(db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    """Find duplicate tracks by source_id, file_hash, or semantic fingerprint (Admin only)"""
    from fastapi.responses import JSONResponse
    from database import Track
    from sqlalchemy import func
    
    groups_by_members = {}

    source_id_dupes = [row[0] for row in db.query(Track.source_id).filter(Track.source_id.isnot(None)).group_by(Track.source_id).having(func.count() > 1).all()]
    if source_id_dupes:
        tracks_by_source_id = _group_tracks_by_value(
            db.query(Track).filter(Track.source_id.in_(source_id_dupes)).order_by(Track.source_id.asc(), Track.id.asc()).all(),
            "source_id",
        )
        for source_id in source_id_dupes:
            tracks = tracks_by_source_id.get(source_id, [])
            _append_duplicate_group(groups_by_members, "source_id", f"source_id:{source_id}", tracks)

    hash_dupes = [row[0] for row in db.query(Track.file_hash).filter(Track.file_hash.isnot(None)).group_by(Track.file_hash).having(func.count() > 1).all()]
    if hash_dupes:
        tracks_by_hash = _group_tracks_by_value(
            db.query(Track).filter(Track.file_hash.in_(hash_dupes)).order_by(Track.file_hash.asc(), Track.id.asc()).all(),
            "file_hash",
        )
        for file_hash in hash_dupes:
            tracks = tracks_by_hash.get(file_hash, [])
            _append_duplicate_group(groups_by_members, "file_hash", f"hash:{file_hash[:16]}...", tracks)

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
        .all()
    ]
    if fingerprint_dupes:
        tracks_by_fingerprint = _group_tracks_by_value(
            db.query(Track).filter(Track.fingerprint.in_(fingerprint_dupes)).order_by(Track.fingerprint.asc(), Track.id.asc()).all(),
            "fingerprint",
        )
        for fingerprint in fingerprint_dupes:
            tracks = tracks_by_fingerprint.get(fingerprint, [])
            if not tracks:
                continue
            first_track = tracks[0]
            if not track_identity.is_semantic_identity_valid(first_track.artist_norm or "", first_track.title_norm or ""):
                continue
            _append_duplicate_group(groups_by_members, "semantic", f"semantic:{fingerprint}", tracks)

    groups = []
    for group in groups_by_members.values():
        display_key = " | ".join(group["keys"])
        groups.append({
            "key": display_key,
            "reasons": group["reasons"],
            "tracks": group["tracks"],
        })

    groups.sort(key=lambda item: (-len(item["tracks"]), item["key"]))
    return JSONResponse({"count": len(groups), "groups": groups})
