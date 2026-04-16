from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
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


def _normalize_repo_path(path: str | Path | None) -> str:
    if path is None:
        return ""
    normalized = str(path).replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def _path_variants_for_match(path: str | Path | None) -> set[str]:
    normalized = _normalize_repo_path(path)
    if not normalized:
        return set()

    variants = {normalized}
    nested_prefixes = []

    if COMPOSE_PROJECT_ROOT != REPO_ROOT:
        nested_prefixes.append(COMPOSE_PROJECT_ROOT.name)
    if REPO_ROOT.name and REPO_ROOT.name != COMPOSE_PROJECT_ROOT.name:
        nested_prefixes.append(REPO_ROOT.name)

    for prefix in nested_prefixes:
        prefix_token = f"{prefix}/"
        if normalized.startswith(prefix_token):
            variants.add(normalized[len(prefix_token):])

    return {variant for variant in variants if variant}


def _path_matches_required_target(path: str | Path | None, required_target: str) -> bool:
    normalized_target = _normalize_repo_path(required_target)
    if not normalized_target:
        return False
    for variant in _path_variants_for_match(path):
        if variant == normalized_target or variant.endswith(f"/{normalized_target}"):
            return True
    return False


def normalize_changed_files(changed_files: list[str]) -> list[str]:
    normalized = []
    seen = set()
    for path in changed_files:
        for variant in sorted(_path_variants_for_match(path), key=len):
            if variant not in seen:
                normalized.append(variant)
                seen.add(variant)
    return normalized


def should_rebuild_for_changed_files(changed_files: list[str]) -> bool:
    for path in changed_files:
        for target in REBUILD_REQUIRED_PATHS:
            if _path_matches_required_target(path, target):
                return True
    return False


def matched_rebuild_targets(changed_files: list[str]) -> list[str]:
    matches = []
    seen = set()
    for target in REBUILD_REQUIRED_PATHS:
        if any(_path_matches_required_target(path, target) for path in changed_files):
            if target not in seen:
                matches.append(target)
                seen.add(target)
    return sorted(matches)


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


def _run_compose_command(args, *, check=True, timeout_seconds: int | None = None, on_wait=None, wait_tick_seconds: int = 15):
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
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=str(COMPOSE_PROJECT_ROOT),
                )
                started_at = time.monotonic()
                while True:
                    elapsed = int(time.monotonic() - started_at)
                    remaining = None if timeout_seconds is None else max(0, timeout_seconds - elapsed)
                    communicate_timeout = wait_tick_seconds if remaining is None else min(wait_tick_seconds, max(1, remaining))
                    try:
                        stdout_text, stderr_text = process.communicate(timeout=communicate_timeout)
                        completed = subprocess.CompletedProcess(cmd, process.returncode, stdout=stdout_text, stderr=stderr_text)
                        if check and completed.returncode != 0:
                            raise subprocess.CalledProcessError(
                                completed.returncode,
                                cmd,
                                output=stdout_text,
                                stderr=stderr_text,
                            )
                        return completed
                    except subprocess.TimeoutExpired:
                        if on_wait:
                            try:
                                on_wait(elapsed + communicate_timeout)
                            except Exception:
                                pass
                        if timeout_seconds is not None and (time.monotonic() - started_at) >= timeout_seconds:
                            process.kill()
                            stdout_text, stderr_text = process.communicate()
                            stderr = f"Command timed out after {timeout_seconds}s"
                            if stderr_text:
                                stderr = f"{stderr}: {stderr_text}"
                            if check:
                                raise RuntimeError(stderr)
                            return subprocess.CompletedProcess(
                                cmd,
                                124,
                                stdout=stdout_text,
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


def _run_docker_command(args, *, check=False, timeout_seconds: int | None = None):
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=True,
        text=True,
        cwd=str(COMPOSE_PROJECT_ROOT),
        timeout=timeout_seconds,
    )


def cleanup_stale_recreate_containers(services: list[str]) -> list[str]:
    service_set = {service.strip() for service in services if service and service.strip()}
    if not service_set:
        return []

    removed = []
    try:
        completed = _run_docker_command(
            ["ps", "-a", "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}"],
            timeout_seconds=30,
        )
        if completed.returncode != 0:
            return removed

        for line in (completed.stdout or "").splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue

            container_id, name, status = parts
            match = re.fullmatch(r"([0-9a-f]{12,})_(.+)", name.strip())
            if not match:
                continue

            service_name = match.group(2)
            normalized_service = service_name.removeprefix("navipod_")
            if service_name not in service_set and normalized_service not in service_set:
                continue

            if status.lower().startswith("up "):
                continue

            rm_result = _run_docker_command(["rm", "-f", container_id], timeout_seconds=30)
            if rm_result.returncode == 0:
                removed.append(name.strip())
    except Exception:
        return removed

    return removed


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
            target_modern_playlist_id INTEGER,
            new_playlist_name TEXT,
            status TEXT DEFAULT 'pending',
            progress_percent INTEGER DEFAULT 0,
            current_file TEXT,
            error_log TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (target_playlist_id) REFERENCES user_playlists(id),
            FOREIGN KEY (target_modern_playlist_id) REFERENCES playlists(id)
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


def _migration_010_playlist_cover_fields(conn):
    columns = {row[1] for row in conn.execute(text("PRAGMA table_info(playlists)")).fetchall()}
    if "cover_path" not in columns:
        conn.execute(text("ALTER TABLE playlists ADD COLUMN cover_path TEXT"))
    if "cover_track_id" not in columns:
        conn.execute(text("ALTER TABLE playlists ADD COLUMN cover_track_id INTEGER"))


def _migration_011_track_identity_fields(conn):
    columns = {row[1] for row in conn.execute(text("PRAGMA table_info(tracks)")).fetchall()}
    required_columns = {
        "artist_norm": "TEXT",
        "title_norm": "TEXT",
        "version_tag": "TEXT",
        "fingerprint": "TEXT",
    }
    for col_name, col_type in required_columns.items():
        if col_name not in columns:
            conn.execute(text(f"ALTER TABLE tracks ADD COLUMN {col_name} {col_type}"))

    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tracks_artist_norm ON tracks(artist_norm)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tracks_title_norm ON tracks(title_norm)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tracks_version_tag ON tracks(version_tag)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tracks_fingerprint ON tracks(fingerprint)"))


def _migration_012_download_job_metadata(conn):
    columns = {row[1] for row in conn.execute(text("PRAGMA table_info(download_jobs)")).fetchall()}
    if "requested_title" not in columns:
        conn.execute(text("ALTER TABLE download_jobs ADD COLUMN requested_title TEXT"))
    if "requested_source" not in columns:
        conn.execute(text("ALTER TABLE download_jobs ADD COLUMN requested_source TEXT"))


def _migration_013_modern_download_playlist_targets(conn):
    columns = {row[1] for row in conn.execute(text("PRAGMA table_info(download_jobs)")).fetchall()}
    if "target_modern_playlist_id" not in columns:
        conn.execute(text("ALTER TABLE download_jobs ADD COLUMN target_modern_playlist_id INTEGER"))

    legacy_playlists = conn.execute(text("""
        SELECT id, user_id, name
        FROM user_playlists
        WHERE name IS NOT NULL AND TRIM(name) != ''
    """)).fetchall()

    legacy_to_modern = {}
    for legacy_id, user_id, name in legacy_playlists:
        existing = conn.execute(text("""
            SELECT id FROM playlists
            WHERE owner_id = :owner_id AND name = :name
            ORDER BY id ASC
            LIMIT 1
        """), {"owner_id": user_id, "name": name}).fetchone()

        if existing:
            modern_id = existing[0]
        else:
            result = conn.execute(text("""
                INSERT INTO playlists(name, owner_id, is_public, source_playlist_id)
                VALUES (:name, :owner_id, 0, :source_playlist_id)
            """), {"name": name, "owner_id": user_id, "source_playlist_id": legacy_id})
            modern_id = result.lastrowid

        legacy_to_modern[legacy_id] = modern_id

    for legacy_id, modern_id in legacy_to_modern.items():
        conn.execute(text("""
            UPDATE download_jobs
            SET target_modern_playlist_id = :modern_id
            WHERE target_modern_playlist_id IS NULL
              AND target_playlist_id = :legacy_id
        """), {"modern_id": modern_id, "legacy_id": legacy_id})

        legacy_tracks = conn.execute(text("""
            SELECT pt.file_path
            FROM playlist_tracks pt
            WHERE pt.playlist_id = :legacy_id
              AND pt.file_path IS NOT NULL
        """), {"legacy_id": legacy_id}).fetchall()

        for (file_path,) in legacy_tracks:
            track = conn.execute(text("""
                SELECT id FROM tracks
                WHERE filepath = :file_path
                LIMIT 1
            """), {"file_path": file_path}).fetchone()
            if not track:
                continue
            track_id = track[0]
            existing_item = conn.execute(text("""
                SELECT id FROM playlist_items
                WHERE playlist_id = :playlist_id AND track_id = :track_id
                LIMIT 1
            """), {"playlist_id": modern_id, "track_id": track_id}).fetchone()
            if existing_item:
                continue
            next_position = conn.execute(text("""
                SELECT COALESCE(MAX(position), 0) + 1
                FROM playlist_items
                WHERE playlist_id = :playlist_id
            """), {"playlist_id": modern_id}).scalar()
            conn.execute(text("""
                INSERT INTO playlist_items(playlist_id, track_id, position)
                VALUES (:playlist_id, :track_id, :position)
            """), {
                "playlist_id": modern_id,
                "track_id": track_id,
                "position": next_position,
            })


def _migration_014_performance_indexes(conn):
    tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}

    if "tracks" in tables:
        track_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(tracks)")).fetchall()}
        if "created_at" in track_columns:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tracks_created_at ON tracks(created_at)"))
        if {"artist_norm", "title_norm", "fingerprint"}.issubset(track_columns):
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tracks_identity_lookup ON tracks(artist_norm, title_norm, fingerprint)"))
        if {"source_provider", "created_at"}.issubset(track_columns):
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tracks_provider_created ON tracks(source_provider, created_at)"))

    if "playlist_items" in tables:
        item_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(playlist_items)")).fetchall()}
        if "playlist_id" in item_columns:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_playlist_items_playlist_id ON playlist_items(playlist_id)"))
        if "track_id" in item_columns:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_playlist_items_track_id ON playlist_items(track_id)"))
        if {"playlist_id", "position"}.issubset(item_columns):
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_playlist_items_playlist_position ON playlist_items(playlist_id, position)"))

    if "download_jobs" in tables:
        job_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(download_jobs)")).fetchall()}
        if {"user_id", "created_at"}.issubset(job_columns):
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_download_jobs_user_created ON download_jobs(user_id, created_at)"))
        if {"status", "created_at"}.issubset(job_columns):
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_download_jobs_status_created ON download_jobs(status, created_at)"))

    if "user_favorites" in tables:
        favorite_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(user_favorites)")).fetchall()}
        if {"user_id", "track_id"}.issubset(favorite_columns):
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_favorites_user_track ON user_favorites(user_id, track_id)"))


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
    ("010_playlist_cover_fields", _migration_010_playlist_cover_fields),
    ("011_track_identity_fields", _migration_011_track_identity_fields),
    ("012_download_job_metadata", _migration_012_download_job_metadata),
    ("013_modern_download_playlist_targets", _migration_013_modern_download_playlist_targets),
    ("014_performance_indexes", _migration_014_performance_indexes),
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
