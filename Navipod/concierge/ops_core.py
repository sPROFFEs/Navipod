from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import text

import database
from navipod_config import settings


def _detect_compose_project_root(repo_root: Path) -> Path:
    direct = repo_root / "docker-compose.yaml"
    nested = repo_root / "Navipod" / "docker-compose.yaml"
    if direct.exists():
        return repo_root
    if nested.exists():
        return repo_root / "Navipod"
    return repo_root / "Navipod"


DB_FILE_PATH = "/saas-data/concierge.db"
REPO_ROOT = Path(settings.APP_SOURCE_ROOT)
COMPOSE_PROJECT_ROOT = _detect_compose_project_root(REPO_ROOT)
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


def utcnow():
    return datetime.now(timezone.utc)


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
        completed = subprocess.run(["git", "-c", f"safe.directory={REPO_ROOT}", *args], check=False, capture_output=True, text=True, cwd=str(REPO_ROOT))
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        result = {"ok": completed.returncode == 0, "stdout": stdout, "stderr": stderr, "returncode": completed.returncode, "command": "git " + " ".join(args)}
        if check and not result["ok"]:
            if include_details:
                return result
            return fallback
        if include_details:
            return result
        return stdout
    except Exception as e:
        if include_details:
            return {"ok": False, "stdout": "", "stderr": str(e), "returncode": None, "command": "git " + " ".join(args)}
        return fallback


def _get_container_mount_source(destination_path: Path):
    container_name = os.getenv("SELF_CONTAINER_NAME")
    if not container_name:
        return None
    try:
        completed = subprocess.run(["docker", "inspect", "--format", "{{json .Mounts}}", container_name], check=False, capture_output=True, text=True)
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
    try:
        compose_relative = COMPOSE_PROJECT_ROOT.relative_to(REPO_ROOT)
    except ValueError:
        compose_relative = Path(".")
    host_compose_root = (host_repo_root / compose_relative).resolve()
    return host_repo_root, host_compose_root


def _build_host_bind_compose_file():
    host_repo_root, host_app_root = _get_host_visible_compose_roots()
    compose_file = COMPOSE_PROJECT_ROOT / "docker-compose.yaml"
    if not host_repo_root or not host_app_root or not compose_file.exists():
        return None

    compose_data = yaml.safe_load(compose_file.read_text(encoding="utf-8")) or {}

    def _resolve_host_path(raw_path: str) -> str:
        candidate = Path(raw_path)
        if candidate.is_absolute():
            return candidate.as_posix()
        if raw_path.startswith("../"):
            return (host_app_root / raw_path).resolve().as_posix()
        return (host_app_root / raw_path).resolve().as_posix()

    def _rewrite_volume_entry(entry):
        if isinstance(entry, str):
            if ":" not in entry:
                return _resolve_host_path(entry)
            source, remainder = entry.split(":", 1)
            if not source or source.startswith("/") or source.startswith("${"):
                return entry
            return f"{_resolve_host_path(source)}:{remainder}"
        if isinstance(entry, dict):
            source = entry.get("source")
            if entry.get("type") == "bind" and isinstance(source, str) and source and not source.startswith("/") and not source.startswith("${"):
                updated = dict(entry)
                updated["source"] = _resolve_host_path(source)
                return updated
        return entry

    services = compose_data.get("services") or {}
    for service in services.values():
        volumes = service.get("volumes")
        if isinstance(volumes, list):
            service["volumes"] = [_rewrite_volume_entry(volume) for volume in volumes]

    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".host-bind.yml", prefix="navipod-compose-", dir=str(COMPOSE_PROJECT_ROOT), delete=False) as tmp:
        yaml.safe_dump(compose_data, tmp, sort_keys=False)
        return Path(tmp.name)


def _run_compose_command(args, *, check=True, timeout_seconds: int | None = None):
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
                    timeout=timeout_seconds,
                )
                return completed
            except subprocess.TimeoutExpired as e:
                stderr = f"Command timed out after {timeout_seconds}s"
                if e.stderr:
                    stderr = f"{stderr}: {e.stderr}"
                if check:
                    raise RuntimeError(stderr) from e
                return subprocess.CompletedProcess(
                    cmd,
                    124,
                    stdout=(e.stdout or ""),
                    stderr=stderr,
                )
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


def _ensure_schema_migrations_table():
    with database.engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))


def _migration_000_base_schema(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            hashed_password TEXT,
            is_active INTEGER DEFAULT 1,
            is_admin INTEGER DEFAULT 0,
            avatar_path TEXT,
            last_access DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_username ON users(username)"))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS download_settings (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL UNIQUE,
            spotify_client_id TEXT,
            spotify_client_secret TEXT,
            lastfm_api_key TEXT,
            lastfm_shared_secret TEXT,
            youtube_cookies_path TEXT,
            youtube_cookies TEXT,
            metadata_preferences TEXT DEFAULT '[\"spotify\", \"lastfm\", \"musicbrainz\"]',
            audio_quality TEXT DEFAULT '320',
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY,
            title TEXT,
            artist TEXT,
            album TEXT,
            duration INTEGER,
            filepath TEXT UNIQUE,
            source_id TEXT UNIQUE,
            file_hash TEXT UNIQUE,
            source_provider TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tracks_filepath ON tracks(filepath)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tracks_source_id ON tracks(source_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tracks_file_hash ON tracks(file_hash)"))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS user_playlists (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            name TEXT,
            source_url TEXT,
            folder_path TEXT,
            auto_sync INTEGER DEFAULT 0,
            last_synced_at DATETIME,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS playlist_tracks (
            id INTEGER PRIMARY KEY,
            playlist_id INTEGER NOT NULL,
            title TEXT,
            file_path TEXT,
            source_id TEXT,
            FOREIGN KEY (playlist_id) REFERENCES user_playlists(id)
        )
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS download_jobs (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            input_url TEXT,
            target_playlist_id INTEGER,
            new_playlist_name TEXT,
            status TEXT DEFAULT 'pending',
            progress_percent INTEGER DEFAULT 0,
            current_file TEXT,
            error_log TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (target_playlist_id) REFERENCES user_playlists(id)
        )
    """))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS token_blacklist (
            id INTEGER PRIMARY KEY,
            token TEXT NOT NULL UNIQUE,
            blacklisted_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_token_blacklist_token ON token_blacklist(token)"))


def _applied_migration_names(conn):
    return {row[0] for row in conn.execute(text("SELECT name FROM schema_migrations")).fetchall()}


def _register_migration(conn, name: str):
    conn.execute(text("INSERT INTO schema_migrations(name) VALUES (:name)"), {"name": name})


def _migration_001_tracks_library_columns(conn):
    columns = {row[1] for row in conn.execute(text("PRAGMA table_info(tracks)")).fetchall()}
    required_columns = {"duration": "INTEGER", "filepath": "TEXT", "source_id": "TEXT", "file_hash": "TEXT", "source_provider": "TEXT"}
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
    columns = {row[1] for row in conn.execute(text("PRAGMA table_info(download_settings)")).fetchall()}
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
    columns = {row[1] for row in conn.execute(text("PRAGMA table_info(playlists)")).fetchall()}
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
    columns = {row[1] for row in conn.execute(text("PRAGMA table_info(system_settings)")).fetchall()}
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
    columns = {row[1] for row in conn.execute(text("PRAGMA table_info(system_settings)")).fetchall()}
    if "autobackup_timezone" not in columns:
        conn.execute(text("ALTER TABLE system_settings ADD COLUMN autobackup_timezone TEXT DEFAULT 'UTC'"))


def _migration_008_system_settings_update_state(conn):
    columns = {row[1] for row in conn.execute(text("PRAGMA table_info(system_settings)")).fetchall()}
    if "update_state_json" not in columns:
        conn.execute(text("ALTER TABLE system_settings ADD COLUMN update_state_json TEXT"))


def _migration_009_tracks_fts(conn):
    try:
        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS tracks_fts
            USING fts5(title, artist, album, content='tracks', content_rowid='id')
        """))
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS tracks_ai AFTER INSERT ON tracks BEGIN
                INSERT INTO tracks_fts(rowid, title, artist, album)
                VALUES (new.id, COALESCE(new.title, ''), COALESCE(new.artist, ''), COALESCE(new.album, ''));
            END
        """))
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS tracks_ad AFTER DELETE ON tracks BEGIN
                INSERT INTO tracks_fts(tracks_fts, rowid, title, artist, album)
                VALUES ('delete', old.id, COALESCE(old.title, ''), COALESCE(old.artist, ''), COALESCE(old.album, ''));
            END
        """))
        conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS tracks_au AFTER UPDATE ON tracks BEGIN
                INSERT INTO tracks_fts(tracks_fts, rowid, title, artist, album)
                VALUES ('delete', old.id, COALESCE(old.title, ''), COALESCE(old.artist, ''), COALESCE(old.album, ''));
                INSERT INTO tracks_fts(rowid, title, artist, album)
                VALUES (new.id, COALESCE(new.title, ''), COALESCE(new.artist, ''), COALESCE(new.album, ''));
            END
        """))
        conn.execute(text("INSERT INTO tracks_fts(tracks_fts) VALUES ('rebuild')"))
    except Exception:
        # FTS is an optimization. Fall back to plain LIKE queries if unavailable.
        pass


MIGRATIONS = [
    ("000_base_schema", _migration_000_base_schema),
    ("001_tracks_library_columns", _migration_001_tracks_library_columns),
    ("002_user_favorites", _migration_002_user_favorites),
    ("003_download_settings_metadata", _migration_003_download_settings_metadata),
    ("004_playlists_and_sync_copy", _migration_004_playlists_and_sync_copy),
    ("005_system_settings_autobackup", _migration_005_system_settings_autobackup),
    ("006_admin_ops_tables", _migration_006_admin_ops_tables),
    ("007_system_settings_timezone", _migration_007_system_settings_timezone),
    ("008_system_settings_update_state", _migration_008_system_settings_update_state),
    ("009_tracks_fts", _migration_009_tracks_fts),
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
        settings_row = database.SystemSettings(pool_limit_gb=100, autobackup_enabled=True, autobackup_hour=0, autobackup_minute=0, autobackup_timezone="UTC", update_state_json=None)
        db.add(settings_row)
        db.commit()
        db.refresh(settings_row)
    return settings_row
