import json
import logging
import os
import shutil
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator
logger = logging.getLogger(__name__)


CACHE_DB_PATH = os.getenv("METADATA_CACHE_DB_PATH", "/saas-data/cache/metadata_cache.db")
CACHE_DB_MAX_BYTES = int(float(os.getenv("METADATA_CACHE_MAX_GB", "5")) * 1024 * 1024 * 1024)
CACHE_COLD_MAX_AGE_SECONDS = int(os.getenv("METADATA_CACHE_COLD_MAX_DAYS", str(180 * 24 * 3600)))
CACHE_HARD_MAX_AGE_SECONDS = int(os.getenv("METADATA_CACHE_HARD_MAX_DAYS", str(365 * 24 * 3600)))
CACHE_PRUNE_BATCH_SIZE = int(os.getenv("METADATA_CACHE_PRUNE_BATCH_SIZE", "5000"))
CACHE_BUSY_TIMEOUT_MS = int(os.getenv("METADATA_CACHE_BUSY_TIMEOUT_MS", "2000"))


def _log(message: str) -> None:
    logger.info(message)


def _ensure_parent_dir() -> None:
    os.makedirs(os.path.dirname(CACHE_DB_PATH), exist_ok=True)


def _backup_corrupt_db() -> None:
    if not os.path.exists(CACHE_DB_PATH):
        return

    timestamp = int(time.time())
    corrupt_path = f"{CACHE_DB_PATH}.corrupt.{timestamp}"
    try:
        shutil.move(CACHE_DB_PATH, corrupt_path)
        for suffix in ("-wal", "-shm"):
            sidecar_path = f"{CACHE_DB_PATH}{suffix}"
            if os.path.exists(sidecar_path):
                shutil.move(sidecar_path, f"{sidecar_path}.corrupt.{timestamp}")
        _log(f"Corrupt cache database moved to {corrupt_path}")
    except Exception as exc:
        _log(f"Failed to rotate corrupt cache database: {exc}")
        try:
            os.remove(CACHE_DB_PATH)
            _log("Corrupt cache database removed")
        except Exception as inner_exc:
            _log(f"Failed to remove corrupt cache database: {inner_exc}")
        for suffix in ("-wal", "-shm"):
            sidecar_path = f"{CACHE_DB_PATH}{suffix}"
            try:
                if os.path.exists(sidecar_path):
                    os.remove(sidecar_path)
            except Exception as inner_exc:
                _log(f"Failed to remove sidecar {sidecar_path}: {inner_exc}")


def _migrate_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata_cache (
            cache_key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            last_accessed_at REAL NOT NULL,
            access_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(metadata_cache)").fetchall()
    }
    if "created_at" not in columns:
        now = time.time()
        conn.execute("ALTER TABLE metadata_cache ADD COLUMN created_at REAL")
        conn.execute("UPDATE metadata_cache SET created_at = COALESCE(updated_at, ?)", (now,))
    if "updated_at" not in columns:
        now = time.time()
        conn.execute("ALTER TABLE metadata_cache ADD COLUMN updated_at REAL")
        conn.execute("UPDATE metadata_cache SET updated_at = COALESCE(created_at, ?)", (now,))
    if "last_accessed_at" not in columns:
        now = time.time()
        conn.execute("ALTER TABLE metadata_cache ADD COLUMN last_accessed_at REAL")
        conn.execute(
            "UPDATE metadata_cache SET last_accessed_at = COALESCE(updated_at, created_at, ?)",
            (now,),
        )
    if "access_count" not in columns:
        conn.execute("ALTER TABLE metadata_cache ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE metadata_cache SET access_count = COALESCE(access_count, 0)")

    legacy_columns = {"expires_at"}
    if columns & legacy_columns:
        conn.execute(
            "DELETE FROM metadata_cache WHERE COALESCE(expires_at, 0) > 0 AND expires_at <= ?",
            (time.time(),),
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_metadata_cache_last_accessed_at ON metadata_cache(last_accessed_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_metadata_cache_created_at ON metadata_cache(created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_metadata_cache_access_count ON metadata_cache(access_count)"
    )


@contextmanager
def _get_conn() -> Iterator[sqlite3.Connection | None]:
    _ensure_parent_dir()
    for attempt in range(2):
        conn = None
        try:
            conn = sqlite3.connect(CACHE_DB_PATH, timeout=CACHE_BUSY_TIMEOUT_MS / 1000)
            conn.execute(f"PRAGMA busy_timeout = {CACHE_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            _migrate_schema(conn)
            yield conn
            return
        except sqlite3.DatabaseError as exc:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            _log(f"Database error: {exc}")
            if attempt == 0:
                _backup_corrupt_db()
                continue
            yield None
            return
        except OSError as exc:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            _log(f"Filesystem error: {exc}")
            yield None
            return
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def ensure_available() -> bool:
    with _get_conn() as conn:
        return conn is not None


def make_key(namespace: str, **parts: Any) -> str:
    normalized = {k: (str(v).strip().lower() if v is not None else "") for k, v in sorted(parts.items())}
    return f"{namespace}:{json.dumps(normalized, sort_keys=True, ensure_ascii=True)}"


def get(cache_key: str) -> dict | None:
    now = time.time()
    with _get_conn() as conn:
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT payload FROM metadata_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if not row:
                return None
            payload = row[0]
            conn.execute(
                "UPDATE metadata_cache SET last_accessed_at = ?, access_count = access_count + 1 WHERE cache_key = ?",
                (now, cache_key),
            )
            conn.commit()
            return json.loads(payload)
        except Exception as exc:
            _log(f"Cache read failed: {exc}")
            return None


def set(cache_key: str, payload: dict) -> None:
    now = time.time()
    try:
        serialized = json.dumps(payload, ensure_ascii=True)
    except Exception as exc:
        _log(f"Cache serialization failed: {exc}")
        return

    with _get_conn() as conn:
        if conn is None:
            return
        try:
            conn.execute(
                """
                INSERT INTO metadata_cache (
                    cache_key, payload, created_at, updated_at, last_accessed_at, access_count
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at,
                    last_accessed_at = excluded.last_accessed_at,
                    access_count = metadata_cache.access_count + 1
                """,
                (cache_key, serialized, now, now, now, 1),
            )
            conn.commit()
        except Exception as exc:
            _log(f"Cache write failed: {exc}")


def purge_expired() -> int:
    now = time.time()
    removed = 0
    vacuum_needed = False

    with _get_conn() as conn:
        if conn is None:
            return 0
        try:
            stale_cursor = conn.execute(
                "DELETE FROM metadata_cache WHERE last_accessed_at <= ? OR created_at <= ?",
                (now - CACHE_COLD_MAX_AGE_SECONDS, now - CACHE_HARD_MAX_AGE_SECONDS),
            )
            removed += stale_cursor.rowcount or 0
            if stale_cursor.rowcount:
                vacuum_needed = True

            if os.path.exists(CACHE_DB_PATH) and os.path.getsize(CACHE_DB_PATH) > CACHE_DB_MAX_BYTES:
                while os.path.getsize(CACHE_DB_PATH) > CACHE_DB_MAX_BYTES:
                    victim_rows = conn.execute(
                        """
                        SELECT cache_key
                        FROM metadata_cache
                        ORDER BY access_count ASC, last_accessed_at ASC, created_at ASC
                        LIMIT ?
                        """,
                        (CACHE_PRUNE_BATCH_SIZE,),
                    ).fetchall()
                    if not victim_rows:
                        break
                    victim_keys = [(row[0],) for row in victim_rows]
                    conn.executemany("DELETE FROM metadata_cache WHERE cache_key = ?", victim_keys)
                    removed += len(victim_keys)
                    vacuum_needed = True
                    conn.commit()
                    if len(victim_keys) < CACHE_PRUNE_BATCH_SIZE:
                        break

            conn.commit()

            if vacuum_needed:
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass
        except Exception as exc:
            _log(f"Cache maintenance failed: {exc}")
            return removed

    if vacuum_needed:
        try:
            with sqlite3.connect(CACHE_DB_PATH, timeout=CACHE_BUSY_TIMEOUT_MS / 1000) as vacuum_conn:
                vacuum_conn.execute(f"PRAGMA busy_timeout = {CACHE_BUSY_TIMEOUT_MS}")
                vacuum_conn.execute("VACUUM")
        except Exception as exc:
            _log(f"VACUUM failed: {exc}")

    return removed
