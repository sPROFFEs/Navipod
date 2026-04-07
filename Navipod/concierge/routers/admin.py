from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import database, auth, manager
import psutil
import shutil
import subprocess
import os
import operations_service

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
    return templates.TemplateResponse("update_progress.html", {
        "request": request,
        "job": job,
        "username": admin.username,
        "is_admin": True,
    })


@router.get("/api/system/jobs/{job_id}")
async def get_admin_job_status(job_id: int, db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    job = operations_service.get_admin_job(db, job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return JSONResponse(job)


# --- ADMIN LIBRARY MANAGEMENT ---

@router.get("/api/library/search")
async def admin_search_library(q: str = "", db: Session = Depends(get_db), admin: database.User = Depends(get_current_admin)):
    """Search all tracks in the Pool (Admin only)"""
    from fastapi.responses import JSONResponse
    from database import Track
    
    if not q:
        # Return first 50 tracks if no query
        tracks = db.query(Track).limit(50).all()
    else:
        query = q.lower()
        tracks = db.query(Track).filter(
            (Track.title.ilike(f"%{query}%")) | (Track.artist.ilike(f"%{query}%"))
        ).limit(100).all()
    
    return JSONResponse([{
        "id": t.id,
        "title": t.title or "Unknown",
        "artist": t.artist or "Unknown",
        "filepath": t.filepath,
        "source_provider": t.source_provider or "unknown"
    } for t in tracks])


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
    """Find duplicate tracks by source_id, file_hash, or title+artist (Admin only)"""
    from fastapi.responses import JSONResponse
    from database import Track
    from sqlalchemy import func
    
    duplicates = []
    existing_track_ids = set()
    
    # 1. Find duplicates by source_id
    source_id_dupes = db.query(Track.source_id).filter(Track.source_id.isnot(None)).group_by(Track.source_id).having(func.count() > 1).all()
    for (source_id,) in source_id_dupes:
        tracks = db.query(Track).filter(Track.source_id == source_id).all()
        duplicates.append({
            "key": f"source_id:{source_id}",
            "tracks": [{
                "id": t.id,
                "title": t.title or "Unknown",
                "artist": t.artist or "Unknown",
                "filepath": t.filepath
            } for t in tracks]
        })
        existing_track_ids.update(t.id for t in tracks)
    
    # 2. Find duplicates by file_hash
    hash_dupes = db.query(Track.file_hash).filter(Track.file_hash.isnot(None)).group_by(Track.file_hash).having(func.count() > 1).all()
    for (file_hash,) in hash_dupes:
        tracks = db.query(Track).filter(Track.file_hash == file_hash).all()
        if not any(t.id in existing_track_ids for t in tracks):
            duplicates.append({
                "key": f"hash:{file_hash[:16]}...",
                "tracks": [{
                    "id": t.id,
                    "title": t.title or "Unknown",
                    "artist": t.artist or "Unknown",
                    "filepath": t.filepath
                } for t in tracks]
            })
            existing_track_ids.update(t.id for t in tracks)
    
    # 3. Find duplicates by title + artist (same song name from different sources)
    title_artist_dupes = db.query(
        func.lower(Track.title), func.lower(Track.artist)
    ).filter(
        Track.title.isnot(None), Track.artist.isnot(None)
    ).group_by(
        func.lower(Track.title), func.lower(Track.artist)
    ).having(func.count() > 1).all()
    
    for (title, artist) in title_artist_dupes:
        tracks = db.query(Track).filter(
            func.lower(Track.title) == title,
            func.lower(Track.artist) == artist
        ).all()
        # Skip if all tracks already flagged
        if not any(t.id in existing_track_ids for t in tracks):
            duplicates.append({
                "key": f"name:{title} - {artist}",
                "tracks": [{
                    "id": t.id,
                    "title": t.title or "Unknown",
                    "artist": t.artist or "Unknown",
                    "filepath": t.filepath
                } for t in tracks]
            })
            existing_track_ids.update(t.id for t in tracks)
    
    return JSONResponse({"count": len(duplicates), "groups": duplicates})
