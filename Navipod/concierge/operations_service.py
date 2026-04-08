import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, available_timezones

import httpx
from sqlalchemy import text

import database
from navipod_config import settings


DB_FILE_PATH = "/saas-data/concierge.db"
REPO_ROOT = Path(settings.APP_SOURCE_ROOT)
COMPOSE_PROJECT_ROOT = REPO_ROOT / "Navipod"
ENV_FILE_PATH = settings.RUNTIME_ENV_FILE
COMPOSE_ENV_FILE = settings.COMPOSE_ENV_FILE
BACKUP_ROOT = Path(settings.BACKUP_ROOT)
CURRENT_BACKUP_NAME = "navipod-backup-current.zip"
PREVIOUS_BACKUP_NAME = "navipod-backup-previous.zip"
GLOBAL_OPERATION_LOCK = "admin-global-operation"
UPDATE_TRACKING_REMOTE = "refs/remotes/navipod-update/tracked"
REBUILD_REQUIRED_PATHS = {
    "docker-compose.yaml",
    "concierge/Dockerfile",
    "concierge/entrypoint.sh",
    "concierge/requirements.txt",
    "nginx.conf",
}
UPDATER_INTERNAL_URL = "http://updater:8090/internal/update/apply"
ADMIN_JOB_RETENTION_LIMIT = 200
VERSION_FILE = REPO_ROOT / "VERSION"

_scheduled_backup_task = None


def utcnow():
    return datetime.now(timezone.utc)


def get_internal_updater_token():
    return hashlib.sha256(f"navipod-updater:{settings.SECRET_KEY}".encode("utf-8")).hexdigest()


def get_scheduler_timezone_name(system_settings=None):
    tz_name = getattr(system_settings, "autobackup_timezone", None) if system_settings else None
    return tz_name or "UTC"


def get_scheduler_timezone(system_settings=None):
    tz_name = get_scheduler_timezone_name(system_settings)
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def ensure_runtime_dirs():
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)


def _run_git(args, *, check=True, fallback=None, include_details=False):
    try:
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={REPO_ROOT}", *args],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        result = {
            "ok": completed.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": completed.returncode,
            "command": "git " + " ".join(args),
        }
        if check and not result["ok"]:
            if include_details:
                return result
            return fallback
        if include_details:
            return result
        return stdout
    except Exception as e:
        if include_details:
            return {
                "ok": False,
                "stdout": "",
                "stderr": str(e),
                "returncode": None,
                "command": "git " + " ".join(args),
            }
        return fallback


def _get_container_mount_source(destination_path: Path):
    container_name = os.getenv("SELF_CONTAINER_NAME")
    if not container_name:
        return None
    try:
        completed = subprocess.run(
            ["docker", "inspect", "--format", "{{json .Mounts}}", container_name],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            return None
        mounts = json.loads((completed.stdout or "").strip() or "[]")
        for mount in mounts:
            if mount.get("Destination") == str(destination_path):
                source = mount.get("Source")
                if source:
                    return Path(source)
    except Exception:
        return None
    return None


def _get_host_visible_compose_roots():
    host_repo_root = _get_container_mount_source(REPO_ROOT)
    if not host_repo_root:
        return None, None
    return host_repo_root, host_repo_root / "Navipod"


def _build_host_bind_compose_file():
    host_repo_root, host_app_root = _get_host_visible_compose_roots()
    compose_file = COMPOSE_PROJECT_ROOT / "docker-compose.yaml"
    if not host_repo_root or not host_app_root or not compose_file.exists():
        return None

    compose_text = compose_file.read_text(encoding="utf-8")
    replacements = {
        "- ..:/workspace": f"- {host_repo_root.as_posix()}:/workspace",
        "- ./concierge/templates:/app/templates": f"- {host_app_root.as_posix()}/concierge/templates:/app/templates",
        "- ./concierge:/app": f"- {host_app_root.as_posix()}/concierge:/app",
        "- ./assets:/app/assets": f"- {host_app_root.as_posix()}/assets:/app/assets",
        "- ./assets:/app/assets:ro": f"- {host_app_root.as_posix()}/assets:/app/assets:ro",
        "- ./nginx.conf:/etc/nginx/nginx.conf:ro": f"- {host_app_root.as_posix()}/nginx.conf:/etc/nginx/nginx.conf:ro",
    }
    for old, new in replacements.items():
        compose_text = compose_text.replace(old, new)

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".host-bind.yml",
        prefix="navipod-compose-",
        dir=str(COMPOSE_PROJECT_ROOT),
        delete=False,
    ) as tmp:
        tmp.write(compose_text)
        return Path(tmp.name)


def _run_compose_command(args, *, check=True):
    temp_compose_file = _build_host_bind_compose_file()
    compose_file_arg = str(temp_compose_file) if temp_compose_file else "docker-compose.yaml"
    commands_to_try = [
        ["docker", "compose", "-f", compose_file_arg, "--env-file", COMPOSE_ENV_FILE, *args],
        ["docker-compose", "-f", compose_file_arg, "--env-file", COMPOSE_ENV_FILE, *args],
    ]
    last_error = None
    try:
        for cmd in commands_to_try:
            try:
                completed = subprocess.run(
                    cmd,
                    check=check,
                    capture_output=True,
                    text=True,
                    cwd=str(COMPOSE_PROJECT_ROOT),
                )
                return completed
            except FileNotFoundError as e:
                last_error = e
            except subprocess.CalledProcessError as e:
                if check:
                    raise
                return e
        if last_error:
            raise last_error
        raise RuntimeError("No compose command available")
    finally:
        if temp_compose_file and temp_compose_file.exists():
            temp_compose_file.unlink(missing_ok=True)


def _normalize_version_label(raw_version: str | None):
    version = (raw_version or "").strip()
    if not version:
        return "v0.0.0"
    return version if version.startswith("v") else f"v{version}"


def _read_release_version():
    env_version = os.getenv("APP_VERSION")
    if env_version:
        return _normalize_version_label(env_version)
    if VERSION_FILE.exists():
        try:
            return _normalize_version_label(VERSION_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    return "v0.0.0"


def get_build_info():
    commit = os.getenv("APP_COMMIT") or _run_git(["rev-parse", "--short", "HEAD"], fallback="unknown")
    branch = os.getenv("APP_CHANNEL") or _run_git(["branch", "--show-current"], fallback=settings.UPDATE_SOURCE_BRANCH)
    build_date = os.getenv("APP_BUILD_DATE") or _run_git(["log", "-1", "--format=%cI"], fallback="unknown")
    revision = os.getenv("APP_REVISION") or _run_git(["rev-list", "--count", "HEAD"], fallback="unknown")
    release_version = _read_release_version()
    version = f"{release_version}+r{revision}" if revision != "unknown" else release_version
    return {
        "channel": branch,
        "commit": commit,
        "revision": revision,
        "release_version": release_version,
        "version": version,
        "build_date": build_date,
        "repo_url": settings.UPDATE_SOURCE_REPO_URL,
        "display_version": f"{version} ({commit})" if commit != "unknown" else version,
    }


def get_timezone_options():
    grouped = {}
    for tz_name in sorted(available_timezones()):
        if tz_name.startswith("Etc/"):
            continue
        if "/" in tz_name:
            group, remainder = tz_name.split("/", 1)
        else:
            group, remainder = "Other", tz_name
        label = remainder.replace("_", " / ")
        grouped.setdefault(group, []).append({"value": tz_name, "label": label})

    if "UTC" not in grouped:
        grouped["Other"] = [{"value": "UTC", "label": "UTC"}] + grouped.get("Other", [])

    ordered_groups = []
    preferred_order = [
        "UTC",
        "Europe",
        "America",
        "Asia",
        "Africa",
        "Australia",
        "Pacific",
        "Indian",
        "Atlantic",
        "Other",
    ]
    for group in preferred_order:
        if group == "UTC":
            ordered_groups.append({"group": "UTC", "zones": [{"value": "UTC", "label": "UTC"}]})
            continue
        items = grouped.pop(group, None)
        if items:
            ordered_groups.append({"group": group, "zones": items})
    for group in sorted(grouped):
        ordered_groups.append({"group": group, "zones": grouped[group]})
    return ordered_groups


def _ensure_schema_migrations_table():
    with database.engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))


def _applied_migration_names(conn):
    return {
        row[0]
        for row in conn.execute(text("SELECT name FROM schema_migrations")).fetchall()
    }


def _register_migration(conn, name: str):
    conn.execute(
        text("INSERT INTO schema_migrations(name) VALUES (:name)"),
        {"name": name},
    )


def _migration_001_tracks_library_columns(conn):
    columns = {
        row[1]
        for row in conn.execute(text("PRAGMA table_info(tracks)")).fetchall()
    }
    required_columns = {
        "duration": "INTEGER",
        "filepath": "TEXT",
        "source_id": "TEXT",
        "file_hash": "TEXT",
        "source_provider": "TEXT",
    }
    for col_name, col_type in required_columns.items():
        if col_name not in columns:
            conn.execute(text(f"ALTER TABLE tracks ADD COLUMN {col_name} {col_type}"))


def _migration_002_user_favorites(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS user_favorites (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            track_id INTEGER NOT NULL,
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (track_id) REFERENCES tracks(id),
            UNIQUE(user_id, track_id)
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_favorites_user_id ON user_favorites(user_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_favorites_track_id ON user_favorites(track_id)"))


def _migration_003_download_settings_metadata(conn):
    columns = {
        row[1]
        for row in conn.execute(text("PRAGMA table_info(download_settings)")).fetchall()
    }
    required_columns = {
        "lastfm_api_key": "TEXT",
        "lastfm_shared_secret": "TEXT",
        "youtube_cookies": "TEXT",
        "metadata_preferences": "TEXT DEFAULT '[\"spotify\", \"lastfm\", \"musicbrainz\"]'",
    }
    for col_name, col_type in required_columns.items():
        if col_name not in columns:
            conn.execute(text(f"ALTER TABLE download_settings ADD COLUMN {col_name} {col_type}"))


def _migration_004_playlists_and_sync_copy(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS playlists (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            owner_id INTEGER NOT NULL,
            is_public INTEGER NOT NULL DEFAULT 0,
            source_playlist_id INTEGER,
            m3u_path TEXT,
            FOREIGN KEY (owner_id) REFERENCES users(id)
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS playlist_items (
            id INTEGER PRIMARY KEY,
            playlist_id INTEGER NOT NULL,
            track_id INTEGER NOT NULL,
            position INTEGER DEFAULT 0,
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (playlist_id) REFERENCES playlists(id),
            FOREIGN KEY (track_id) REFERENCES tracks(id)
        )
    """))
    columns = {
        row[1]
        for row in conn.execute(text("PRAGMA table_info(playlists)")).fetchall()
    }
    if "is_public" not in columns:
        conn.execute(text("ALTER TABLE playlists ADD COLUMN is_public INTEGER NOT NULL DEFAULT 0"))
    if "source_playlist_id" not in columns:
        conn.execute(text("ALTER TABLE playlists ADD COLUMN source_playlist_id INTEGER"))


def _migration_005_system_settings_autobackup(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS system_settings (
            id INTEGER PRIMARY KEY,
            pool_limit_gb INTEGER DEFAULT 100,
            autobackup_enabled INTEGER DEFAULT 1,
            autobackup_hour INTEGER DEFAULT 0,
            autobackup_minute INTEGER DEFAULT 0,
            autobackup_timezone TEXT DEFAULT 'UTC'
        )
    """))
    columns = {
        row[1]
        for row in conn.execute(text("PRAGMA table_info(system_settings)")).fetchall()
    }
    if "autobackup_enabled" not in columns:
        conn.execute(text("ALTER TABLE system_settings ADD COLUMN autobackup_enabled INTEGER DEFAULT 1"))
    if "autobackup_hour" not in columns:
        conn.execute(text("ALTER TABLE system_settings ADD COLUMN autobackup_hour INTEGER DEFAULT 0"))
    if "autobackup_minute" not in columns:
        conn.execute(text("ALTER TABLE system_settings ADD COLUMN autobackup_minute INTEGER DEFAULT 0"))
    if "autobackup_timezone" not in columns:
        conn.execute(text("ALTER TABLE system_settings ADD COLUMN autobackup_timezone TEXT DEFAULT 'UTC'"))


def _migration_006_admin_ops_tables(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS admin_jobs (
            id INTEGER PRIMARY KEY,
            job_type TEXT,
            status TEXT DEFAULT 'pending',
            triggered_by TEXT,
            message TEXT,
            details_json TEXT,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_jobs_job_type ON admin_jobs(job_type)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_admin_jobs_status ON admin_jobs(status)"))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS admin_operation_locks (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            job_id INTEGER,
            acquired_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME,
            FOREIGN KEY (job_id) REFERENCES admin_jobs(id)
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS backup_artifacts (
            id INTEGER PRIMARY KEY,
            slot TEXT NOT NULL UNIQUE,
            filename TEXT,
            file_path TEXT,
            size_bytes INTEGER DEFAULT 0,
            created_at DATETIME,
            source_commit TEXT,
            source_branch TEXT,
            manifest_json TEXT
        )
    """))


def _migration_007_system_settings_timezone(conn):
    columns = {
        row[1]
        for row in conn.execute(text("PRAGMA table_info(system_settings)")).fetchall()
    }
    if "autobackup_timezone" not in columns:
        conn.execute(text("ALTER TABLE system_settings ADD COLUMN autobackup_timezone TEXT DEFAULT 'UTC'"))


def _migration_008_system_settings_update_state(conn):
    columns = {
        row[1]
        for row in conn.execute(text("PRAGMA table_info(system_settings)")).fetchall()
    }
    if "update_state_json" not in columns:
        conn.execute(text("ALTER TABLE system_settings ADD COLUMN update_state_json TEXT"))


MIGRATIONS = [
    ("001_tracks_library_columns", _migration_001_tracks_library_columns),
    ("002_user_favorites", _migration_002_user_favorites),
    ("003_download_settings_metadata", _migration_003_download_settings_metadata),
    ("004_playlists_and_sync_copy", _migration_004_playlists_and_sync_copy),
    ("005_system_settings_autobackup", _migration_005_system_settings_autobackup),
    ("006_admin_ops_tables", _migration_006_admin_ops_tables),
    ("007_system_settings_timezone", _migration_007_system_settings_timezone),
    ("008_system_settings_update_state", _migration_008_system_settings_update_state),
]


def apply_schema_migrations():
    _ensure_schema_migrations_table()
    applied_now = []
    with database.engine.begin() as conn:
        applied = _applied_migration_names(conn)
        for name, migration in MIGRATIONS:
            if name in applied:
                continue
            migration(conn)
            _register_migration(conn, name)
            applied_now.append(name)
    return applied_now


def get_schema_status(db):
    latest = db.query(database.SchemaMigration).order_by(database.SchemaMigration.id.desc()).first()
    return {
        "count": db.query(database.SchemaMigration).count(),
        "latest": latest.name if latest else "none",
        "latest_applied_at": latest.applied_at.isoformat() if latest and latest.applied_at else None,
    }


def ensure_system_settings_record(db):
    settings_row = db.query(database.SystemSettings).first()
    if not settings_row:
        settings_row = database.SystemSettings(
            pool_limit_gb=100,
            autobackup_enabled=True,
            autobackup_hour=0,
            autobackup_minute=0,
            autobackup_timezone="UTC",
            update_state_json=None,
        )
        db.add(settings_row)
        db.commit()
        db.refresh(settings_row)
    return settings_row


def format_bytes(size_bytes):
    if not size_bytes:
        return "0 B"
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size_bytes} B"


def format_datetime_for_display(dt_value, tzinfo=None):
    if not dt_value:
        return None
    target_tz = tzinfo or ZoneInfo("UTC")
    dt_local = dt_value
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=timezone.utc)
    try:
        dt_local = dt_local.astimezone(target_tz)
    except Exception:
        dt_local = dt_local.astimezone(ZoneInfo("UTC"))
        target_tz = ZoneInfo("UTC")
    tz_name = getattr(target_tz, "key", None) or str(target_tz)
    return f"{dt_local.strftime('%Y-%m-%d %H:%M:%S')} {tz_name}"


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
    system_settings = ensure_system_settings_record(db)
    scheduler_timezone = get_scheduler_timezone(system_settings)

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
        next_candidate = now.replace(
            hour=system_settings.autobackup_hour,
            minute=system_settings.autobackup_minute,
            second=0,
            microsecond=0,
        )
        if next_candidate <= now:
            next_candidate = next_candidate.replace(day=now.day) + timedelta(days=1)
        next_run = next_candidate.isoformat()

    return {
        "current": serialize(current),
        "previous": serialize(previous),
        "autobackup_enabled": bool(system_settings.autobackup_enabled),
        "autobackup_hour": system_settings.autobackup_hour,
        "autobackup_minute": system_settings.autobackup_minute,
        "autobackup_timezone": get_scheduler_timezone_name(system_settings),
        "latest_success_at": latest_success.isoformat() if latest_success else None,
        "next_run": next_run,
    }


def _serialize_update_state_payload(payload):
    payload = payload or {}
    return json.dumps(payload, ensure_ascii=False)


def save_update_state(db, payload):
    settings_row = ensure_system_settings_record(db)
    settings_row.update_state_json = _serialize_update_state_payload(payload)
    db.commit()


def get_update_state(db):
    settings_row = ensure_system_settings_record(db)
    state = {}
    if settings_row.update_state_json:
        try:
            state = json.loads(settings_row.update_state_json)
        except Exception:
            state = {"status": "error", "message": "Stored update state is invalid JSON"}
    state.setdefault("source_repo_url", settings.UPDATE_SOURCE_REPO_URL)
    state.setdefault("source_branch", settings.UPDATE_SOURCE_BRANCH)
    state.setdefault("current", {**get_build_info(), "dirty": _get_worktree_dirty()})
    state.setdefault("remote", {"commit": "unknown", "full_commit": "unknown"})
    state["remote"].setdefault("release_version", None)
    state["remote"].setdefault("version", state["remote"].get("commit", "unknown"))
    state.setdefault("behind_count", 0)
    state.setdefault("ahead_count", 0)
    state.setdefault("update_available", False)
    state.setdefault("pending_commits", [])
    return state


def _parse_state_checked_at(payload: dict):
    checked_at = (payload or {}).get("checked_at")
    if not checked_at:
        return None
    try:
        parsed = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def is_update_state_stale(db, max_age_hours: int = 24):
    state = get_update_state(db)
    checked_at = _parse_state_checked_at(state)
    if not checked_at:
        return True
    return (utcnow() - checked_at).total_seconds() >= (max_age_hours * 3600)


def _fetch_update_tracking_ref():
    return _run_git(
        [
            "fetch",
            "--prune",
            "--no-tags",
            "--no-write-fetch-head",
            settings.UPDATE_SOURCE_REPO_URL,
            f"+refs/heads/{settings.UPDATE_SOURCE_BRANCH}:{UPDATE_TRACKING_REMOTE}",
        ],
        fallback="",
        include_details=True,
    )


def _get_remote_branch_sha_via_ls_remote():
    result = _run_git(
        [
            "ls-remote",
            settings.UPDATE_SOURCE_REPO_URL,
            f"refs/heads/{settings.UPDATE_SOURCE_BRANCH}",
        ],
        fallback="",
        include_details=True,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        return None, result

    stdout = result.get("stdout") or ""
    first_line = stdout.splitlines()[0].strip() if stdout else ""
    if not first_line:
        return None, result

    remote_sha = first_line.split()[0].strip()
    if not remote_sha:
        return None, result
    return remote_sha, result


def _parse_github_repo_slug(repo_url: str):
    parsed = urlparse(repo_url)
    if parsed.netloc.lower() != "github.com":
        return None
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}"


async def _get_remote_release_version():
    repo_slug = _parse_github_repo_slug(settings.UPDATE_SOURCE_REPO_URL)
    if not repo_slug:
        return None
    version_url = f"https://raw.githubusercontent.com/{repo_slug}/{settings.UPDATE_SOURCE_BRANCH}/VERSION"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(version_url)
        if response.status_code != 200:
            return None
        return _normalize_version_label(response.text.strip())
    except Exception:
        return None


def _build_remote_version_display(current: dict, remote_release_version: str | None, behind_count: int, remote_commit: str):
    if remote_release_version:
        try:
            current_revision = int(current.get("revision") or 0)
            return f"{remote_release_version}+r{current_revision + max(behind_count, 0)}"
        except Exception:
            return remote_release_version
    return remote_commit or "unknown"


async def _get_github_compare_payload(local_full_commit: str):
    repo_slug = _parse_github_repo_slug(settings.UPDATE_SOURCE_REPO_URL)
    if not repo_slug or not local_full_commit or local_full_commit == "unknown":
        return None

    compare_url = (
        f"https://api.github.com/repos/{repo_slug}/compare/"
        f"{local_full_commit}...{settings.UPDATE_SOURCE_BRANCH}"
    )

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(
            compare_url,
            headers={"Accept": "application/vnd.github+json"},
        )
        if response.status_code != 200:
            return {
                "ok": False,
                "status_code": response.status_code,
                "error": response.text.strip(),
                "compare_url": compare_url,
            }

        payload = response.json()
        commits = payload.get("commits") or []
        remote_head_sha = payload.get("merge_base_commit", {}).get("sha") or ""
        if payload.get("status") in {"ahead", "behind", "diverged", "identical"}:
            remote_head_sha = payload.get("html_url", "")
        head_commit = payload.get("commits", [])[-1].get("sha") if commits else None
        compare_status = payload.get("status")
        if compare_status == "ahead":
            # local...remote means remote HEAD is ahead of local BASE
            local_ahead = int(payload.get("behind_by", 0) or 0)
            local_behind = int(payload.get("ahead_by", 0) or 0)
        elif compare_status == "behind":
            local_ahead = int(payload.get("ahead_by", 0) or 0)
            local_behind = int(payload.get("behind_by", 0) or 0)
        else:
            local_ahead = int(payload.get("behind_by", 0) or 0)
            local_behind = int(payload.get("ahead_by", 0) or 0)
        return {
            "ok": True,
            "status_code": response.status_code,
            "compare_url": compare_url,
            "status": compare_status,
            "ahead_by": local_ahead,
            "behind_by": local_behind,
            "html_url": payload.get("html_url"),
            "remote_full_commit": head_commit or "unknown",
            "remote_commit": (head_commit or "")[:7] or "unknown",
            "pending_commits": [
                f"{(commit.get('sha') or '')[:7]} {commit.get('commit', {}).get('message', '').splitlines()[0]}".strip()
                for commit in commits[:10]
            ],
        }


def _get_update_details_via_fetch(local_full_commit: str, current: dict):
    remote_release_version = None
    fetch_result = _fetch_update_tracking_ref()
    if not _run_git(["rev-parse", "--verify", UPDATE_TRACKING_REMOTE], fallback=None):
        return None, fetch_result

    remote_commit = _run_git(["rev-parse", "--short", UPDATE_TRACKING_REMOTE], fallback="unknown")
    remote_full_commit = _run_git(["rev-parse", UPDATE_TRACKING_REMOTE], fallback="unknown")
    counts = _run_git(["rev-list", "--left-right", "--count", f"HEAD...{UPDATE_TRACKING_REMOTE}"], fallback="0\t0")
    ahead_count = 0
    behind_count = 0
    try:
        ahead_str, behind_str = counts.split()
        ahead_count = int(ahead_str)
        behind_count = int(behind_str)
    except Exception:
        pass
    pending_commits_raw = _run_git(
        ["log", "--oneline", f"HEAD..{UPDATE_TRACKING_REMOTE}", "-n", "10"],
        fallback="",
    )
    pending_commits = [line.strip() for line in pending_commits_raw.splitlines() if line.strip()]
    payload = {
        "checked_at": utcnow().isoformat(),
        "status": "ok",
        "message": "Update check completed",
        "source_repo_url": settings.UPDATE_SOURCE_REPO_URL,
        "source_branch": settings.UPDATE_SOURCE_BRANCH,
        "current": {
            **current,
            "full_commit": local_full_commit,
            "dirty": _get_worktree_dirty(),
        },
        "remote": {
            "commit": remote_commit,
            "full_commit": remote_full_commit,
            "release_version": remote_release_version,
            "version": _build_remote_version_display(current, remote_release_version, behind_count, remote_commit),
        },
        "ahead_count": ahead_count,
        "behind_count": behind_count,
        "update_available": behind_count > 0,
        "pending_commits": pending_commits,
        "fetch_result": fetch_result,
    }
    return payload, fetch_result


def _get_worktree_dirty():
    status = _run_git(
        [
            "status",
            "--porcelain",
            "--untracked-files=no",
            "--",
            ".",
            ":(exclude).env",
            ":(exclude)Navipod/.env",
        ],
        fallback="",
    )
    return bool(status and status.strip())


async def _get_update_check_payload():
    current = get_build_info()
    local_full_commit = _run_git(["rev-parse", "HEAD"], fallback="unknown")
    remote_release_version = await _get_remote_release_version()
    remote_sha, ls_remote_result = _get_remote_branch_sha_via_ls_remote()
    if remote_sha:
        remote_short = remote_sha[:7]
        update_available = remote_sha != local_full_commit
        behind_count = 1 if update_available else 0
        payload = {
            "checked_at": utcnow().isoformat(),
            "status": "ok",
            "message": "Update check completed",
            "source_repo_url": settings.UPDATE_SOURCE_REPO_URL,
            "source_branch": settings.UPDATE_SOURCE_BRANCH,
            "current": {
                **current,
                "full_commit": local_full_commit,
                "dirty": _get_worktree_dirty(),
            },
            "remote": {
                "commit": remote_short,
                "full_commit": remote_sha,
                "release_version": remote_release_version,
                "version": _build_remote_version_display(current, remote_release_version, behind_count, remote_short),
            },
            "ahead_count": 0,
            "behind_count": behind_count,
            "update_available": update_available,
            "pending_commits": [],
            "fetch_result": ls_remote_result,
        }

        fetch_payload, fetch_result = _get_update_details_via_fetch(local_full_commit, current)
        if fetch_payload:
            payload = fetch_payload

        if update_available:
            compare_result = await _get_github_compare_payload(local_full_commit)
            if compare_result and compare_result.get("ok"):
                ahead_count = int(compare_result.get("ahead_by") or 0)
                behind_count = int(compare_result.get("behind_by") or 0)
                payload.update({
                    "message": "Update check completed",
                    "remote": {
                        "commit": compare_result.get("remote_commit", remote_short),
                        "full_commit": compare_result.get("remote_full_commit", remote_sha),
                        "release_version": remote_release_version,
                        "version": _build_remote_version_display(current, remote_release_version, behind_count, compare_result.get("remote_commit", remote_short)),
                    },
                    "ahead_count": ahead_count,
                    "behind_count": behind_count,
                    "update_available": behind_count > 0,
                    "pending_commits": compare_result.get("pending_commits", payload.get("pending_commits", [])),
                    "fetch_result": compare_result,
                })
        return payload

    compare_result = await _get_github_compare_payload(local_full_commit)
    if compare_result and compare_result.get("ok"):
        ahead_count = int(compare_result.get("ahead_by") or 0)
        behind_count = int(compare_result.get("behind_by") or 0)
        return {
            "checked_at": utcnow().isoformat(),
            "status": "ok",
            "message": "Update check completed",
            "source_repo_url": settings.UPDATE_SOURCE_REPO_URL,
            "source_branch": settings.UPDATE_SOURCE_BRANCH,
            "current": {
                **current,
                "full_commit": local_full_commit,
                "dirty": _get_worktree_dirty(),
            },
            "remote": {
                "commit": compare_result.get("remote_commit", "unknown"),
                "full_commit": compare_result.get("remote_full_commit", "unknown"),
                "release_version": remote_release_version,
                "version": _build_remote_version_display(current, remote_release_version, behind_count, compare_result.get("remote_commit", "unknown")),
            },
            "ahead_count": ahead_count,
            "behind_count": behind_count,
            "update_available": behind_count > 0,
            "pending_commits": compare_result.get("pending_commits", []),
            "fetch_result": compare_result,
        }

    fetch_result = _fetch_update_tracking_ref()
    if not _run_git(["rev-parse", "--verify", UPDATE_TRACKING_REMOTE], fallback=None):
        fetch_error = ""
        fetch_stdout = ""
        fetch_returncode = None
        if compare_result and not compare_result.get("ok"):
            fetch_error = compare_result.get("error") or ""
            fetch_returncode = compare_result.get("status_code")
        elif isinstance(ls_remote_result, dict) and not ls_remote_result.get("ok"):
            fetch_error = ls_remote_result.get("stderr") or ls_remote_result.get("stdout") or ""
            fetch_returncode = ls_remote_result.get("returncode")
        elif isinstance(fetch_result, dict):
            fetch_error = fetch_result.get("stderr") or ""
            fetch_stdout = fetch_result.get("stdout") or ""
            fetch_returncode = fetch_result.get("returncode")
        error_message = "Failed to fetch update reference from GitHub main"
        if fetch_error:
            error_message = f"{error_message}: {fetch_error}"
        return {
            "checked_at": utcnow().isoformat(),
            "status": "error",
            "message": error_message,
            "source_repo_url": settings.UPDATE_SOURCE_REPO_URL,
            "source_branch": settings.UPDATE_SOURCE_BRANCH,
            "current": {
                **current,
                "full_commit": local_full_commit,
                "dirty": _get_worktree_dirty(),
            },
            "remote": {
                "commit": "unknown",
                "full_commit": "unknown",
            },
            "ahead_count": 0,
            "behind_count": 0,
            "update_available": False,
            "pending_commits": [],
            "fetch_result": fetch_result,
            "fetch_error": fetch_error,
            "fetch_stdout": fetch_stdout,
            "fetch_returncode": fetch_returncode,
        }


def _resolve_target_update_sha():
    remote_sha, ls_remote_result = _get_remote_branch_sha_via_ls_remote()
    if remote_sha:
        return remote_sha, ls_remote_result
    fetch_result = _fetch_update_tracking_ref()
    fetched_sha = _run_git(["rev-parse", UPDATE_TRACKING_REMOTE], fallback=None)
    return fetched_sha, fetch_result


def _fetch_target_update_ref(target_sha: str | None):
    if not target_sha:
        return None
    return _run_git(
        [
            "fetch",
            "--prune",
            "--no-tags",
            "--no-write-fetch-head",
            settings.UPDATE_SOURCE_REPO_URL,
            f"+refs/heads/{settings.UPDATE_SOURCE_BRANCH}:{UPDATE_TRACKING_REMOTE}",
        ],
        fallback=None,
        include_details=True,
    )


def _run_post_update_health_check():
    urls = [
        "http://concierge:8000/login",
        "http://nginx/login",
    ]
    timeout_seconds = 45
    deadline = utcnow().timestamp() + timeout_seconds
    last_error = "health check did not start"

    while utcnow().timestamp() < deadline:
        for url in urls:
            try:
                response = httpx.get(url, timeout=5.0, follow_redirects=True)
                if response.status_code < 500:
                    return {"ok": True, "url": url, "status_code": response.status_code}
                last_error = f"{url} returned {response.status_code}"
            except Exception as e:
                last_error = str(e)
        time.sleep(2)

    return {"ok": False, "error": last_error}


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


def _run_check_update_job(job_id: int, triggered_by: str | None):
    db = database.SessionLocal()
    try:
        if not _acquire_lock(db, job_id):
            update_admin_job_progress(job_id, message="Another admin operation is already running", status="failed", phase="error", progress=100, finished=True)
            return
        update_admin_job_progress(job_id, message="Checking GitHub main for updates", status="running", phase="check", progress=25)
        payload = asyncio.run(_get_update_check_payload())
        save_update_state(db, payload)
        if payload.get("status") != "ok":
            message = payload.get("message") or "Update check failed"
            status = "failed"
        elif payload.get("update_available"):
            message = f"Update available: {payload['behind_count']} commit(s) behind"
            status = "completed"
        else:
            message = "Already up to date"
            status = "completed"
        update_admin_job_progress(job_id, message=message, status=status, phase="done", progress=100, extra=payload, finished=True)
    except Exception as e:
        update_admin_job_progress(job_id, message=f"Update check failed: {e}", status="failed", phase="error", progress=100, finished=True)
    finally:
        try:
            _release_lock(db)
        finally:
            db.close()


def _run_apply_update_job(job_id: int, triggered_by: str | None):
    db = database.SessionLocal()
    temp_path = None
    try:
        if not _acquire_lock(db, job_id):
            update_admin_job(job_id, status="failed", message="Another admin operation is already running", finished=True)
            return

        if _get_worktree_dirty():
            update_admin_job(
                job_id,
                status="failed",
                message="Repository has local tracked changes. Refusing to apply update.",
                details={"dirty": True},
                finished=True,
            )
            return

        update_admin_job(job_id, status="running", message="Checking for updates before apply")
        payload = asyncio.run(_get_update_check_payload())
        save_update_state(db, payload)
        if payload.get("status") != "ok":
            update_admin_job(job_id, status="failed", message=payload.get("message") or "Update check failed", details=payload, finished=True)
            return
        if not payload.get("update_available"):
            update_admin_job(job_id, status="completed", message="Already up to date", details=payload, finished=True)
            return

        target_sha, target_result = _resolve_target_update_sha()
        if not target_sha:
            update_admin_job(
                job_id,
                status="failed",
                message="Failed to resolve remote update target",
                details={"precheck": payload, "target_result": target_result},
                finished=True,
            )
            return

        update_admin_job(job_id, status="running", message="Creating pre-update backup")
        ensure_runtime_dirs()
        manifest = _build_backup_manifest(triggered_by, "pre-update")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir=BACKUP_ROOT) as tmp:
            temp_path = Path(tmp.name)
        database.engine.dispose()
        _write_backup_zip(temp_path, manifest)
        _rotate_current_to_previous(db)
        current_path = BACKUP_ROOT / CURRENT_BACKUP_NAME
        shutil.move(str(temp_path), current_path)
        temp_path = None
        _upsert_backup_artifact(
            db,
            "current",
            filename=CURRENT_BACKUP_NAME,
            file_path=str(current_path),
            size_bytes=current_path.stat().st_size,
            created_at=utcnow(),
            manifest=manifest,
        )

        update_admin_job(job_id, status="running", message="Fetching target revision from remote")
        fetch_result = _fetch_target_update_ref(target_sha)
        if not fetch_result or not fetch_result.get("ok"):
            raise RuntimeError((fetch_result or {}).get("stderr") or "git fetch failed")

        changed_files_raw = _run_git(["diff", "--name-only", f"HEAD..{target_sha}"], fallback="") or ""
        changed_files = [line.strip() for line in changed_files_raw.splitlines() if line.strip()]
        rebuild_required = any(path in REBUILD_REQUIRED_PATHS for path in changed_files)

        update_admin_job(job_id, status="running", message="Applying update to local workspace")
        reset_result = _run_git(["reset", "--hard", target_sha], fallback=None)
        if reset_result is None:
            raise RuntimeError("git reset --hard failed")

        applied_migrations = apply_schema_migrations()
        post_payload = asyncio.run(_get_update_check_payload())
        save_update_state(db, post_payload)

        details = {
            "before": payload,
            "after": post_payload,
            "changed_files": changed_files[:100],
            "rebuild_required": rebuild_required,
            "applied_migrations": applied_migrations,
        }
        if rebuild_required:
            message = "Update applied to workspace. Container rebuild/restart is required for infrastructure changes."
        else:
            message = "Update applied successfully to workspace. Hot-reload should pick up code changes."
        update_admin_job(job_id, status="completed", message=message, details=details, finished=True)
    except Exception as e:
        update_admin_job(job_id, status="failed", message=f"Apply update failed: {e}", finished=True)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        try:
            _release_lock(db)
        finally:
            db.close()


def _run_compose_update(job_id: int, changed_files: list[str]):
    services = [svc.strip() for svc in settings.UPDATE_MANAGED_SERVICES.split() if svc.strip()]
    rebuild_required = any(path in REBUILD_REQUIRED_PATHS for path in changed_files)
    update_admin_job_progress(
        job_id,
        message=f"Recreating services: {' '.join(services)}",
        phase="recreate",
        progress=85,
        status="running",
        extra={"changed_files": changed_files[:100], "rebuild_required": rebuild_required},
    )
    completed = _run_compose_command(["up", "-d", "--build", "--remove-orphans", *services], check=False)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "docker compose up failed").strip())

    update_admin_job_progress(
        job_id,
        message="Pruning dangling Docker images and builder cache",
        phase="cleanup",
        progress=95,
        status="running",
    )
    subprocess.run(["docker", "image", "prune", "-f"], capture_output=True, text=True, check=False)
    subprocess.run(["docker", "builder", "prune", "-f"], capture_output=True, text=True, check=False)
    return rebuild_required


def run_apply_update_job_from_updater(job_id: int, triggered_by: str | None):
    db = database.SessionLocal()
    temp_path = None
    try:
        if not _acquire_lock(db, job_id):
            update_admin_job_progress(job_id, message="Another admin operation is already running", status="failed", progress=100, finished=True)
            return

        if _get_worktree_dirty():
            update_admin_job_progress(
                job_id,
                message="Repository has local tracked changes. Refusing to apply update.",
                status="failed",
                phase="preflight",
                progress=100,
                extra={"dirty": True},
                finished=True,
            )
            return

        update_admin_job_progress(job_id, message="Checking GitHub main for updates", status="running", phase="check", progress=10)
        payload = asyncio.run(_get_update_check_payload())
        save_update_state(db, payload)
        if payload.get("status") != "ok":
            update_admin_job_progress(job_id, message=payload.get("message") or "Update check failed", status="failed", phase="check", progress=100, extra=payload, finished=True)
            return
        if not payload.get("update_available"):
            update_admin_job_progress(job_id, message="Already up to date", status="completed", phase="done", progress=100, extra=payload, finished=True)
            return

        target_sha, target_result = _resolve_target_update_sha()
        if not target_sha:
            update_admin_job_progress(
                job_id,
                message="Failed to resolve remote update target",
                status="failed",
                phase="check",
                progress=100,
                extra={"precheck": payload, "target_result": target_result},
                finished=True,
            )
            return

        update_admin_job_progress(job_id, message="Creating pre-update backup", status="running", phase="backup", progress=25)
        ensure_runtime_dirs()
        manifest = _build_backup_manifest(triggered_by, "pre-update")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir=BACKUP_ROOT) as tmp:
            temp_path = Path(tmp.name)
        database.engine.dispose()
        _write_backup_zip(temp_path, manifest)
        _rotate_current_to_previous(db)
        current_path = BACKUP_ROOT / CURRENT_BACKUP_NAME
        shutil.move(str(temp_path), current_path)
        temp_path = None
        _upsert_backup_artifact(
            db,
            "current",
            filename=CURRENT_BACKUP_NAME,
            file_path=str(current_path),
            size_bytes=current_path.stat().st_size,
            created_at=utcnow(),
            manifest=manifest,
        )

        update_admin_job_progress(job_id, message="Fetching target revision from remote", status="running", phase="fetch", progress=40)
        fetch_result = _fetch_target_update_ref(target_sha)
        if not fetch_result or not fetch_result.get("ok"):
            raise RuntimeError((fetch_result or {}).get("stderr") or "git fetch failed")

        changed_files_raw = _run_git(["diff", "--name-only", f"HEAD..{target_sha}"], fallback="") or ""
        changed_files = [line.strip() for line in changed_files_raw.splitlines() if line.strip()]

        update_admin_job_progress(job_id, message="Applying Git update to workspace", status="running", phase="workspace", progress=50)
        reset_result = _run_git(["reset", "--hard", target_sha], fallback=None)
        if reset_result is None:
            raise RuntimeError("git reset --hard failed")

        update_admin_job_progress(job_id, message="Running schema migrations", status="running", phase="migrate", progress=70)
        applied_migrations = apply_schema_migrations()
        rebuild_required = _run_compose_update(job_id, changed_files)
        health_result = _run_post_update_health_check()
        if not health_result.get("ok"):
            raise RuntimeError(f"health check failed: {health_result.get('error', 'unknown error')}")

        post_payload = asyncio.run(_get_update_check_payload())
        save_update_state(db, post_payload)
        details = {
            "before": payload,
            "after": post_payload,
            "changed_files": changed_files[:100],
            "rebuild_required": rebuild_required,
            "applied_migrations": applied_migrations,
            "health_check": health_result,
            "target_sha": target_sha,
        }
        update_admin_job_progress(
            job_id,
            message="Update applied and services recreated successfully",
            status="completed",
            phase="done",
            progress=100,
            extra=details,
            finished=True,
        )
    except Exception as e:
        update_admin_job_progress(job_id, message=f"Apply update failed: {e}", status="failed", phase="error", progress=100, finished=True)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        try:
            _release_lock(db)
        finally:
            db.close()


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


def queue_check_update(triggered_by: str | None):
    job_id = create_admin_job("check_update", triggered_by, "Update check queued", {"source_branch": settings.UPDATE_SOURCE_BRANCH})
    asyncio.create_task(asyncio.to_thread(_run_check_update_job, job_id, triggered_by))
    return job_id


def queue_apply_update(triggered_by: str | None):
    job_id = create_admin_job(
        "apply_update",
        triggered_by,
        "Apply update queued",
        {
            "source_branch": settings.UPDATE_SOURCE_BRANCH,
            "progress": 0,
            "phase": "queued",
            "logs": [{"at": utcnow().isoformat(), "message": "Apply update queued"}],
        },
    )
    try:
        response = httpx.post(
            UPDATER_INTERNAL_URL,
            headers={"Authorization": f"Bearer {get_internal_updater_token()}"},
            json={"job_id": job_id, "triggered_by": triggered_by},
            timeout=10.0,
        )
        response.raise_for_status()
    except Exception as e:
        update_admin_job_progress(job_id, message=f"Failed to contact internal updater: {e}", status="failed", phase="error", progress=100, finished=True)
    return job_id


def run_silent_update_refresh():
    db = database.SessionLocal()
    try:
        if not _acquire_lock(db, None):
            return False
        payload = asyncio.run(_get_update_check_payload())
        save_update_state(db, payload)
        return True
    except Exception:
        return False
    finally:
        try:
            _release_lock(db)
        except Exception:
            pass
        db.close()


def queue_silent_update_refresh_if_stale(max_age_hours: int = 24):
    db = database.SessionLocal()
    try:
        if not is_update_state_stale(db, max_age_hours=max_age_hours):
            return False
        if get_active_operation_lock(db):
            return False
    finally:
        db.close()
    asyncio.create_task(asyncio.to_thread(run_silent_update_refresh))
    return True


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


def _acquire_lock(db, job_id: int):
    existing = db.query(database.AdminOperationLock).filter(
        database.AdminOperationLock.name == GLOBAL_OPERATION_LOCK
    ).first()
    if existing:
        return False
    lock = database.AdminOperationLock(name=GLOBAL_OPERATION_LOCK, job_id=job_id)
    db.add(lock)
    db.commit()
    return True


def _release_lock(db):
    db.query(database.AdminOperationLock).filter(
        database.AdminOperationLock.name == GLOBAL_OPERATION_LOCK
    ).delete(synchronize_session=False)
    db.commit()


def _build_backup_manifest(triggered_by: str | None, mode: str):
    build_info = get_build_info()
    return {
        "created_at": utcnow().isoformat(),
        "triggered_by": triggered_by,
        "mode": mode,
        "build": build_info,
        "db_file": DB_FILE_PATH,
        "env_file": ENV_FILE_PATH if os.path.exists(ENV_FILE_PATH) else None,
    }


def _rotate_current_to_previous(db):
    current_path = BACKUP_ROOT / CURRENT_BACKUP_NAME
    previous_path = BACKUP_ROOT / PREVIOUS_BACKUP_NAME
    current_artifact = db.query(database.BackupArtifact).filter(database.BackupArtifact.slot == "current").first()
    if not current_artifact or not current_artifact.file_path or not os.path.exists(current_artifact.file_path):
        return

    if previous_path.exists():
        previous_path.unlink()
    shutil.copy2(current_path, previous_path)
    _upsert_backup_artifact(
        db,
        "previous",
        filename=PREVIOUS_BACKUP_NAME,
        file_path=str(previous_path),
        size_bytes=previous_path.stat().st_size,
        created_at=current_artifact.created_at,
        manifest=json.loads(current_artifact.manifest_json or "{}"),
    )


def _write_backup_zip(target_path: Path, manifest: dict):
    ensure_runtime_dirs()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db", dir=BACKUP_ROOT) as tmp_db:
        temp_db_path = Path(tmp_db.name)

    with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        src = sqlite3.connect(DB_FILE_PATH)
        dst = sqlite3.connect(str(temp_db_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        zf.write(temp_db_path, arcname="concierge.db")
        if os.path.exists(ENV_FILE_PATH):
            zf.write(ENV_FILE_PATH, arcname=".env")
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
        if not _acquire_lock(db, job_id):
            update_admin_job(job_id, status="failed", message="Another admin operation is already running", finished=True)
            return

        update_admin_job(job_id, status="running", message="Creating backup archive")
        ensure_runtime_dirs()
        manifest = _build_backup_manifest(triggered_by, mode)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir=BACKUP_ROOT) as tmp:
            temp_path = Path(tmp.name)

        database.engine.dispose()
        _write_backup_zip(temp_path, manifest)

        _rotate_current_to_previous(db)
        current_path = BACKUP_ROOT / CURRENT_BACKUP_NAME
        shutil.move(str(temp_path), current_path)
        temp_path = None

        _upsert_backup_artifact(
            db,
            "current",
            filename=CURRENT_BACKUP_NAME,
            file_path=str(current_path),
            size_bytes=current_path.stat().st_size,
            created_at=utcnow(),
            manifest=manifest,
        )
        update_admin_job(
            job_id,
            status="completed",
            message=f"Backup created successfully ({format_bytes(current_path.stat().st_size)})",
            details={"slot": "current", "size_bytes": current_path.stat().st_size, "mode": mode},
            finished=True,
        )
    except Exception as e:
        update_admin_job(job_id, status="failed", message=f"Backup failed: {e}", finished=True)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        try:
            _release_lock(db)
        finally:
            db.close()


def run_restore_job(job_id: int, slot: str, triggered_by: str | None):
    db = database.SessionLocal()
    extract_dir = None
    try:
        if not _acquire_lock(db, job_id):
            update_admin_job(job_id, status="failed", message="Another admin operation is already running", finished=True)
            return

        artifact = db.query(database.BackupArtifact).filter(database.BackupArtifact.slot == slot).first()
        if not artifact or not artifact.file_path or not os.path.exists(artifact.file_path):
            update_admin_job(job_id, status="failed", message=f"{slot.title()} backup not available", finished=True)
            return

        update_admin_job(job_id, status="running", message=f"Restoring {slot} backup")
        extract_dir = Path(tempfile.mkdtemp(prefix="navipod-restore-", dir=BACKUP_ROOT))
        with zipfile.ZipFile(artifact.file_path, "r") as zf:
            zf.extractall(extract_dir)

        restored_db = extract_dir / "concierge.db"
        restored_env = extract_dir / ".env"
        if not restored_db.exists():
            raise RuntimeError("Backup archive does not contain concierge.db")

        database.engine.dispose()
        shutil.copy2(restored_db, DB_FILE_PATH)
        if restored_env.exists():
            shutil.copy2(restored_env, ENV_FILE_PATH)

        update_admin_job(
            job_id,
            status="completed",
            message=f"{slot.title()} backup restored. Restart recommended to reload configuration.",
            details={"slot": slot, "restart_required": True, "triggered_by": triggered_by},
            finished=True,
        )
    except Exception as e:
        update_admin_job(job_id, status="failed", message=f"Restore failed: {e}", finished=True)
    finally:
        if extract_dir and extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
        try:
            _release_lock(db)
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
    settings_row = ensure_system_settings_record(db)
    if not settings_row.autobackup_enabled:
        return False, "autobackup disabled"

    scheduler_timezone = get_scheduler_timezone(settings_row)
    now = datetime.now(scheduler_timezone)
    scheduled_today = now.replace(
        hour=settings_row.autobackup_hour,
        minute=settings_row.autobackup_minute,
        second=0,
        microsecond=0,
    )
    if now < scheduled_today:
        return False, f"scheduled time not reached ({scheduled_today.isoformat()} {get_scheduler_timezone_name(settings_row)})"

    current = db.query(database.BackupArtifact).filter(database.BackupArtifact.slot == "current").first()
    if current and current.created_at:
        file_exists = bool(current.file_path and os.path.exists(current.file_path))
        local_created = current.created_at
        if file_exists and hasattr(local_created, "date") and local_created.date() == now.date():
            return False, "backup already exists for today"
        if not file_exists:
            print("[BACKUP-SCHEDULER] Ignoring stale current backup metadata because the backup file is missing.")

    lock = db.query(database.AdminOperationLock).filter(
        database.AdminOperationLock.name == GLOBAL_OPERATION_LOCK
    ).first()
    if lock is not None:
        return False, f"admin lock active (job #{lock.job_id})"
    return True, "backup should run"


def update_autobackup_settings(enabled: bool, hour: int, minute: int, timezone_name: str | None = None):
    db = database.SessionLocal()
    try:
        settings_row = ensure_system_settings_record(db)
        settings_row.autobackup_enabled = bool(enabled)
        settings_row.autobackup_hour = max(0, min(23, int(hour)))
        settings_row.autobackup_minute = max(0, min(59, int(minute)))
        tz_name = (timezone_name or "UTC").strip()
        try:
            ZoneInfo(tz_name)
        except Exception:
            tz_name = "UTC"
        settings_row.autobackup_timezone = tz_name
        db.commit()
    finally:
        db.close()


async def autobackup_scheduler():
    global _scheduled_backup_task
    _scheduled_backup_task = asyncio.current_task()
    print(
        f"[BACKUP-SCHEDULER] Started. Poll interval={settings.BACKUP_SCHEDULER_POLL_SECONDS}s "
        f"backup_root={BACKUP_ROOT}"
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
            await asyncio.sleep(settings.BACKUP_SCHEDULER_POLL_SECONDS)
        except Exception as e:
            print(f"[BACKUP-SCHEDULER] {e}")
            await asyncio.sleep(60)


# Facade exports for extracted services.
from build_info_service import (
    format_bytes,
    format_datetime_for_display,
    get_build_info,
    get_timezone_options,
)
from job_service import (
    create_admin_job,
    get_active_operation_lock,
    get_admin_job,
    get_recent_admin_jobs,
    release_lock as _release_lock,
    acquire_lock as _acquire_lock,
    update_admin_job,
    update_admin_job_progress,
)
from backup_service import (
    get_backup_state,
    queue_backup,
    queue_restore,
    run_backup_job,
    run_restore_job,
    should_run_autobackup,
    update_autobackup_settings,
)
from update_service import (
    get_internal_updater_token,
    get_update_state,
    is_update_state_stale,
    queue_apply_update,
    queue_check_update,
    queue_silent_update_refresh_if_stale,
    run_apply_update_job_from_updater,
    run_silent_update_refresh,
    save_update_state,
)
