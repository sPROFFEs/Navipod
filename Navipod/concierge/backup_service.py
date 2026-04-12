from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import database
import ops_core as ops
from personalization_service import MIX_CACHE_NAME, USER_ACTIVITY_DB_NAME
from build_info_service import format_bytes, format_datetime_for_display, get_build_info
from job_service import acquire_lock, create_admin_job, release_lock, update_admin_job, update_admin_job_progress


def _upsert_backup_artifact(db, slot, *, filename=None, file_path=None, size_bytes=0, created_at=None, manifest=None):
    artifact = db.query(database.BackupArtifact).filter(database.BackupArtifact.slot == slot).first()
    if not artifact:
        artifact = database.BackupArtifact(slot=slot)
        db.add(artifact)
    artifact.filename = filename
    artifact.file_path = file_path
    artifact.size_bytes = size_bytes or 0
    artifact.created_at = created_at
    build_info = get_build_info()
    artifact.source_commit = build_info["commit"]
    artifact.source_branch = build_info["channel"]
    artifact.manifest_json = json.dumps(manifest or {}, ensure_ascii=False)
    db.commit()
    db.refresh(artifact)
    return artifact


def get_backup_state(db):
    current = db.query(database.BackupArtifact).filter(database.BackupArtifact.slot == "current").first()
    previous = db.query(database.BackupArtifact).filter(database.BackupArtifact.slot == "previous").first()
    system_settings = ops.ensure_system_settings_record(db)
    scheduler_timezone = ops.get_scheduler_timezone(system_settings)

    def serialize(artifact):
        if not artifact:
            return None
        exists = bool(artifact.file_path and os.path.exists(artifact.file_path))
        return {
            "slot": artifact.slot,
            "filename": artifact.filename,
            "file_path": artifact.file_path,
            "size_bytes": artifact.size_bytes or 0,
            "size_label": format_bytes(artifact.size_bytes or 0),
            "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
            "created_at_display": format_datetime_for_display(artifact.created_at, scheduler_timezone),
            "exists": exists,
            "source_commit": artifact.source_commit,
            "source_branch": artifact.source_branch,
        }

    latest_success = current.created_at if current and current.created_at else None
    if previous and previous.created_at and (latest_success is None or previous.created_at > latest_success):
        latest_success = previous.created_at

    next_run = None
    if system_settings.autobackup_enabled:
        now = datetime.now(scheduler_timezone)
        next_candidate = now.replace(hour=system_settings.autobackup_hour, minute=system_settings.autobackup_minute, second=0, microsecond=0)
        if next_candidate <= now:
            next_candidate = next_candidate.replace(day=now.day) + ops.timedelta(days=1)
        next_run = next_candidate.isoformat()

    return {
        "current": serialize(current),
        "previous": serialize(previous),
        "autobackup_enabled": bool(system_settings.autobackup_enabled),
        "autobackup_hour": system_settings.autobackup_hour,
        "autobackup_minute": system_settings.autobackup_minute,
        "autobackup_timezone": ops.get_scheduler_timezone_name(system_settings),
        "latest_success_at": latest_success.isoformat() if latest_success else None,
        "next_run": next_run,
    }


def _build_backup_manifest(triggered_by: str | None, mode: str):
    build_info = get_build_info()
    personalization_files = []
    users_root = Path("/saas-data/users")
    if users_root.exists():
        for cache_file in users_root.glob(f"*/cache/{USER_ACTIVITY_DB_NAME}"):
            personalization_files.append(str(cache_file))
        for mix_cache in users_root.glob(f"*/cache/{MIX_CACHE_NAME}"):
            personalization_files.append(str(mix_cache))
    return {
        "created_at": ops.utcnow().isoformat(),
        "triggered_by": triggered_by,
        "mode": mode,
        "build": build_info,
        "db_file": ops.DB_FILE_PATH,
        "env_file": ops.ENV_FILE_PATH if os.path.exists(ops.ENV_FILE_PATH) else None,
        "personalization_files": personalization_files,
    }


def _rotate_current_to_previous(db):
    current_path = ops.BACKUP_ROOT / ops.CURRENT_BACKUP_NAME
    previous_path = ops.BACKUP_ROOT / ops.PREVIOUS_BACKUP_NAME
    current_artifact = db.query(database.BackupArtifact).filter(database.BackupArtifact.slot == "current").first()
    if not current_artifact or not current_artifact.file_path or not os.path.exists(current_artifact.file_path):
        return

    if previous_path.exists():
        previous_path.unlink()
    shutil.copy2(current_path, previous_path)
    _upsert_backup_artifact(
        db,
        "previous",
        filename=ops.PREVIOUS_BACKUP_NAME,
        file_path=str(previous_path),
        size_bytes=previous_path.stat().st_size,
        created_at=current_artifact.created_at,
        manifest=json.loads(current_artifact.manifest_json or "{}"),
    )


def _write_backup_zip(target_path: Path, manifest: dict):
    ops.ensure_runtime_dirs()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db", dir=ops.BACKUP_ROOT) as tmp_db:
        temp_db_path = Path(tmp_db.name)

    with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        src = sqlite3.connect(ops.DB_FILE_PATH)
        dst = sqlite3.connect(str(temp_db_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        zf.write(temp_db_path, arcname="concierge.db")
        if os.path.exists(ops.ENV_FILE_PATH):
            zf.write(ops.ENV_FILE_PATH, arcname=".env")
        users_root = Path("/saas-data/users")
        if users_root.exists():
            for user_dir in users_root.iterdir():
                if not user_dir.is_dir():
                    continue
                cache_dir = user_dir / "cache"
                if not cache_dir.exists():
                    continue
                for filename in (USER_ACTIVITY_DB_NAME, MIX_CACHE_NAME):
                    candidate = cache_dir / filename
                    if candidate.exists():
                        zf.write(candidate, arcname=f"users/{user_dir.name}/cache/{filename}")
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    try:
        with zipfile.ZipFile(target_path, "r") as zf:
            if zf.testzip() is not None:
                raise RuntimeError("Backup archive integrity check failed")
    finally:
        temp_db_path.unlink(missing_ok=True)


def run_backup_job(job_id: int, triggered_by: str | None, mode: str = "manual"):
    db = database.SessionLocal()
    temp_path = None
    try:
        if not acquire_lock(db, job_id):
            update_admin_job(job_id, status="failed", message="Another admin operation is already running", finished=True)
            return

        update_admin_job(job_id, status="running", message="Creating backup archive")
        ops.ensure_runtime_dirs()
        manifest = _build_backup_manifest(triggered_by, mode)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir=ops.BACKUP_ROOT) as tmp:
            temp_path = Path(tmp.name)

        database.engine.dispose()
        _write_backup_zip(temp_path, manifest)

        _rotate_current_to_previous(db)
        current_path = ops.BACKUP_ROOT / ops.CURRENT_BACKUP_NAME
        shutil.move(str(temp_path), current_path)
        temp_path = None

        _upsert_backup_artifact(
            db,
            "current",
            filename=ops.CURRENT_BACKUP_NAME,
            file_path=str(current_path),
            size_bytes=current_path.stat().st_size,
            created_at=ops.utcnow(),
            manifest=manifest,
        )
        update_admin_job(job_id, status="completed", message=f"Backup created successfully ({format_bytes(current_path.stat().st_size)})", details={"slot": "current", "size_bytes": current_path.stat().st_size, "mode": mode}, finished=True)
    except Exception as e:
        update_admin_job(job_id, status="failed", message=f"Backup failed: {e}", finished=True)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        try:
            release_lock(db)
        finally:
            db.close()


def run_restore_job(job_id: int, slot: str, triggered_by: str | None):
    db = database.SessionLocal()
    extract_dir = None
    try:
        if not acquire_lock(db, job_id):
            update_admin_job(job_id, status="failed", message="Another admin operation is already running", finished=True)
            return

        artifact = db.query(database.BackupArtifact).filter(database.BackupArtifact.slot == slot).first()
        if not artifact or not artifact.file_path or not os.path.exists(artifact.file_path):
            update_admin_job(job_id, status="failed", message=f"{slot.title()} backup not available", finished=True)
            return

        update_admin_job(job_id, status="running", message=f"Restoring {slot} backup")
        extract_dir = Path(tempfile.mkdtemp(prefix="navipod-restore-", dir=ops.BACKUP_ROOT))
        with zipfile.ZipFile(artifact.file_path, "r") as zf:
            zf.extractall(extract_dir)

        restored_db = extract_dir / "concierge.db"
        restored_env = extract_dir / ".env"
        restored_users_root = extract_dir / "users"
        if not restored_db.exists():
            raise RuntimeError("Backup archive does not contain concierge.db")

        database.engine.dispose()
        shutil.copy2(restored_db, ops.DB_FILE_PATH)
        if restored_env.exists():
            shutil.copy2(restored_env, ops.ENV_FILE_PATH)
        if restored_users_root.exists():
            for cache_file in restored_users_root.glob("*/cache/*"):
                relative = cache_file.relative_to(restored_users_root)
                target = Path("/saas-data/users") / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cache_file, target)

        update_admin_job(job_id, status="completed", message=f"{slot.title()} backup restored. Restart recommended to reload configuration.", details={"slot": slot, "restart_required": True, "triggered_by": triggered_by}, finished=True)
    except Exception as e:
        update_admin_job(job_id, status="failed", message=f"Restore failed: {e}", finished=True)
    finally:
        if extract_dir and extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
        try:
            release_lock(db)
        finally:
            db.close()


def queue_backup(triggered_by: str | None, mode: str = "manual"):
    job_id = create_admin_job("backup", triggered_by, "Backup queued", {"mode": mode})
    asyncio.create_task(asyncio.to_thread(run_backup_job, job_id, triggered_by, mode))
    return job_id


def queue_restore(slot: str, triggered_by: str | None):
    job_id = create_admin_job("restore", triggered_by, f"Restore queued for {slot}", {"slot": slot})
    asyncio.create_task(asyncio.to_thread(run_restore_job, job_id, slot, triggered_by))
    return job_id


def should_run_autobackup(db):
    settings_row = ops.ensure_system_settings_record(db)
    if not settings_row.autobackup_enabled:
        return False, "autobackup disabled"

    scheduler_timezone = ops.get_scheduler_timezone(settings_row)
    now = datetime.now(scheduler_timezone)
    scheduled_today = now.replace(hour=settings_row.autobackup_hour, minute=settings_row.autobackup_minute, second=0, microsecond=0)
    if now < scheduled_today:
        return False, f"scheduled time not reached ({scheduled_today.isoformat()} {ops.get_scheduler_timezone_name(settings_row)})"

    current = db.query(database.BackupArtifact).filter(database.BackupArtifact.slot == "current").first()
    if current and current.created_at:
        file_exists = bool(current.file_path and os.path.exists(current.file_path))
        local_created = current.created_at
        if file_exists and hasattr(local_created, "date") and local_created.date() == now.date():
            return False, "backup already exists for today"
        if not file_exists:
            print("[BACKUP-SCHEDULER] Ignoring stale current backup metadata because the backup file is missing.")

    lock = db.query(database.AdminOperationLock).filter(database.AdminOperationLock.name == "admin-global-operation").first()
    if lock is not None:
        return False, f"admin lock active (job #{lock.job_id})"
    return True, "backup should run"


def update_autobackup_settings(enabled: bool, hour: int, minute: int, timezone_name: str | None = None):
    db = database.SessionLocal()
    try:
        settings_row = ops.ensure_system_settings_record(db)
        settings_row.autobackup_enabled = bool(enabled)
        settings_row.autobackup_hour = max(0, min(23, int(hour)))
        settings_row.autobackup_minute = max(0, min(59, int(minute)))
        tz_name = (timezone_name or "UTC").strip()
        try:
            ops.ZoneInfo(tz_name)
        except Exception:
            tz_name = "UTC"
        settings_row.autobackup_timezone = tz_name
        db.commit()
    finally:
        db.close()


async def autobackup_scheduler():
    print(
        f"[BACKUP-SCHEDULER] Started. Poll interval={ops.settings.BACKUP_SCHEDULER_POLL_SECONDS}s "
        f"backup_root={ops.BACKUP_ROOT}"
    )
    while True:
        try:
            db = database.SessionLocal()
            try:
                should_run, reason = should_run_autobackup(db)
                if should_run:
                    print("[BACKUP-SCHEDULER] Autobackup conditions met. Queueing backup job.")
                    queue_backup("system", mode="auto")
                else:
                    print(f"[BACKUP-SCHEDULER] Skipping autobackup: {reason}")
            finally:
                db.close()
            await asyncio.sleep(ops.settings.BACKUP_SCHEDULER_POLL_SECONDS)
        except Exception as e:
            print(f"[BACKUP-SCHEDULER] {e}")
            await asyncio.sleep(60)
