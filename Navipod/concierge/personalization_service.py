from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

import database
logger = logging.getLogger(__name__)


USER_ACTIVITY_DB_NAME = "user_activity.db"
MIX_CACHE_NAME = "personalized_mixes.json"
LEGACY_RECENT_CACHE_NAME = "recent_activity.json"
TOP_POOL_CACHE_NAME = "top_pool_tracks.json"
MIX_CACHE_VERSION = 4
TOP_POOL_CACHE_VERSION = 3
MIX_CACHE_TTL_SECONDS = 12 * 3600
RECENT_ITEMS_LIMIT = 3
RECENT_HISTORY_LIMIT = 12
MIN_TRACK_PLAY_SECONDS = 8
COMPLETION_RATIO = 0.85
EARLY_SKIP_SECONDS = 30
WRAPPED_MIN_LISTEN_SECONDS = 30.0
WRAPPED_MIN_LISTEN_RATIO = 0.20
TRACKING_SCHEMA_VERSION = 1
TRACKING_DEDUPE_WINDOW_SECONDS = 10
MIX_TRACK_LIMIT = 50
TOP_POOL_TRACK_LIMIT = 50
LATEST_POOL_TRACK_LIMIT = 100
TOP_REPEAT_EXCLUDE_COUNT = 10

MIX_DEFINITIONS = [
    ("repeat", "Repeat Mix"),
    ("deep_cuts", "Deep Cuts Mix"),
    ("favorites", "Favorites Mix"),
    ("rediscovery", "Rediscovery Mix"),
    ("top_pool_tracks", "Top Pool Tracks"),
    ("latest_pool_additions", "Latest Pool Additions"),
]

MIX_THUMBNAILS = {
    "repeat": "/assets/img/repeat.webp",
    "deep_cuts": "/assets/img/deep.webp",
    "favorites": "/assets/img/fav.webp",
    "rediscovery": "/assets/img/rediscovery.webp",
    "top_pool_tracks": "/assets/img/top.webp",
    "latest_pool_additions": "/assets/img/last.webp",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return utcnow().isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _days_since(value: str | None) -> float | None:
    parsed = _parse_dt(value)
    if not parsed:
        return None
    return max(0.0, (utcnow() - parsed).total_seconds() / 86400.0)


def _user_cache_dir(username: str) -> Path:
    path = Path(f"/saas-data/users/{username}/cache")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _global_cache_dir() -> Path:
    path = Path("/saas-data/cache")
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_user_activity_db_path(username: str) -> Path:
    return _user_cache_dir(username) / USER_ACTIVITY_DB_NAME


def get_mix_cache_path(username: str) -> Path:
    return _user_cache_dir(username) / MIX_CACHE_NAME


def get_legacy_recent_cache_path(username: str) -> Path:
    return _user_cache_dir(username) / LEGACY_RECENT_CACHE_NAME


def get_top_pool_cache_path() -> Path:
    return _global_cache_dir() / TOP_POOL_CACHE_NAME


def _connect(username: str) -> sqlite3.Connection:
    path = get_user_activity_db_path(username)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def ensure_user_activity_db(username: str) -> Path:
    conn = _connect(username)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS listen_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER NOT NULL,
                played_seconds REAL NOT NULL DEFAULT 0,
                duration_seconds REAL,
                completed INTEGER NOT NULL DEFAULT 0,
                skipped_early INTEGER NOT NULL DEFAULT 0,
                context_type TEXT,
                context_key TEXT,
                recorded_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS track_stats (
                track_id INTEGER PRIMARY KEY,
                play_count INTEGER NOT NULL DEFAULT 0,
                completion_count INTEGER NOT NULL DEFAULT 0,
                skip_count INTEGER NOT NULL DEFAULT 0,
                total_played_seconds REAL NOT NULL DEFAULT 0,
                first_played_at TEXT,
                last_played_at TEXT
            );

            CREATE TABLE IF NOT EXISTS artist_stats (
                artist_name TEXT PRIMARY KEY,
                play_count INTEGER NOT NULL DEFAULT 0,
                completion_count INTEGER NOT NULL DEFAULT 0,
                skip_count INTEGER NOT NULL DEFAULT 0,
                total_played_seconds REAL NOT NULL DEFAULT 0,
                first_played_at TEXT,
                last_played_at TEXT
            );

            CREATE TABLE IF NOT EXISTS recent_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_type TEXT NOT NULL,
                item_key TEXT NOT NULL,
                item_label TEXT,
                item_data_json TEXT,
                last_accessed_at TEXT NOT NULL,
                UNIQUE(item_type, item_key)
            );

            CREATE TABLE IF NOT EXISTS user_tracking_raw (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                track_id INTEGER NOT NULL,
                user_id INTEGER,
                session_id TEXT,
                played_ms INTEGER NOT NULL DEFAULT 0,
                duration_ms INTEGER,
                timestamp_utc TEXT NOT NULL,
                context_type TEXT,
                context_key TEXT,
                source_context TEXT,
                wrapped_schema_version INTEGER NOT NULL DEFAULT 1,
                client_event_id TEXT,
                event_payload_json TEXT,
                dedupe_bucket INTEGER NOT NULL DEFAULT 0,
                dedupe_key TEXT NOT NULL,
                inserted_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS ix_listen_events_track_id ON listen_events(track_id);
            CREATE INDEX IF NOT EXISTS ix_listen_events_recorded_at ON listen_events(recorded_at);
            CREATE INDEX IF NOT EXISTS ix_recent_items_type_accessed ON recent_items(item_type, last_accessed_at DESC);
            CREATE INDEX IF NOT EXISTS ix_user_tracking_raw_timestamp ON user_tracking_raw(timestamp_utc);
            CREATE INDEX IF NOT EXISTS ix_user_tracking_raw_track_type ON user_tracking_raw(track_id, event_type);
            CREATE INDEX IF NOT EXISTS ix_user_tracking_raw_session ON user_tracking_raw(session_id, timestamp_utc);
            CREATE UNIQUE INDEX IF NOT EXISTS ux_user_tracking_raw_dedupe_key ON user_tracking_raw(dedupe_key);
            """
        )
        conn.commit()
    finally:
        conn.close()

    _migrate_legacy_recent_cache(username)
    return get_user_activity_db_path(username)


def _migrate_legacy_recent_cache(username: str) -> None:
    legacy_path = get_legacy_recent_cache_path(username)
    if not legacy_path.exists():
        return

    try:
        with _connect(username) as conn:
            count = conn.execute("SELECT COUNT(*) FROM recent_items").fetchone()[0]
            if count > 0:
                return

            with open(legacy_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)

            for entry in payload.get("playlists", []):
                playlist_id = int(entry.get("playlist_id") or 0)
                if playlist_id:
                    _upsert_recent_item(
                        conn,
                        item_type="playlist",
                        item_key=str(playlist_id),
                        item_label="",
                        item_data_json="",
                        accessed_at=_iso_from_timestamp(entry.get("accessed_at")),
                    )

            for entry in payload.get("radios", []):
                radio_id = str(entry.get("radio_id") or "").strip()
                if radio_id:
                    item_data = json.dumps(
                        {
                            "id": radio_id,
                            "name": str(entry.get("name") or "").strip(),
                            "streamUrl": str(entry.get("streamUrl") or "").strip(),
                        },
                        ensure_ascii=False,
                    )
                    _upsert_recent_item(
                        conn,
                        item_type="radio",
                        item_key=radio_id,
                        item_label=str(entry.get("name") or "").strip(),
                        item_data_json=item_data,
                        accessed_at=_iso_from_timestamp(entry.get("accessed_at")),
                    )
            conn.commit()
    except Exception as e:
        logger.warning("Legacy recent cache migration failed for %s: %s", username, e)


def _iso_from_timestamp(value: Any) -> str:
    try:
        ts = float(value)
    except Exception:
        return _iso_now()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _upsert_recent_item(
    conn: sqlite3.Connection,
    *,
    item_type: str,
    item_key: str,
    item_label: str,
    item_data_json: str,
    accessed_at: str | None = None,
) -> None:
    accessed = accessed_at or _iso_now()
    conn.execute(
        """
        INSERT INTO recent_items (item_type, item_key, item_label, item_data_json, last_accessed_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(item_type, item_key) DO UPDATE SET
            item_label = excluded.item_label,
            item_data_json = excluded.item_data_json,
            last_accessed_at = excluded.last_accessed_at
        """,
        (item_type, item_key, item_label, item_data_json, accessed),
    )
    rows = conn.execute(
        """
        SELECT id FROM recent_items
        WHERE item_type = ?
        ORDER BY last_accessed_at DESC, id DESC
        LIMIT -1 OFFSET ?
        """,
        (item_type, RECENT_HISTORY_LIMIT),
    ).fetchall()
    if rows:
        conn.executemany("DELETE FROM recent_items WHERE id = ?", [(row["id"],) for row in rows])


def record_recent_playlist(username: str, playlist_id: int) -> None:
    ensure_user_activity_db(username)
    with _connect(username) as conn:
        _upsert_recent_item(
            conn,
            item_type="playlist",
            item_key=str(int(playlist_id)),
            item_label="",
            item_data_json="",
        )
        conn.commit()


def record_recent_radio(username: str, radio_id: str, name: str = "", stream_url: str = "") -> None:
    ensure_user_activity_db(username)
    payload = json.dumps(
        {
            "id": radio_id,
            "name": name.strip(),
            "streamUrl": stream_url.strip(),
        },
        ensure_ascii=False,
    )
    with _connect(username) as conn:
        _upsert_recent_item(
            conn,
            item_type="radio",
            item_key=radio_id.strip(),
            item_label=name.strip(),
            item_data_json=payload,
        )
        conn.commit()


def remove_recent_playlist(username: str, playlist_id: int) -> None:
    ensure_user_activity_db(username)
    with _connect(username) as conn:
        conn.execute("DELETE FROM recent_items WHERE item_type = 'playlist' AND item_key = ?", (str(int(playlist_id)),))
        conn.commit()


def remove_recent_radio(username: str, radio_id: str) -> None:
    ensure_user_activity_db(username)
    with _connect(username) as conn:
        conn.execute("DELETE FROM recent_items WHERE item_type = 'radio' AND item_key = ?", (str(radio_id).strip(),))
        conn.commit()


def get_recent_activity_payload(db: Session, user) -> dict[str, Any]:
    ensure_user_activity_db(user.username)
    from routers.music.playlists import fetch_playlist_summaries

    with _connect(user.username) as conn:
        playlist_ids = [
            int(row["item_key"])
            for row in conn.execute(
                """
                SELECT item_key FROM recent_items
                WHERE item_type = 'playlist'
                ORDER BY last_accessed_at DESC, id DESC
                LIMIT ?
                """,
                (RECENT_HISTORY_LIMIT,),
            ).fetchall()
            if str(row["item_key"]).isdigit()
        ]
        radio_rows = conn.execute(
            """
            SELECT item_data_json FROM recent_items
            WHERE item_type = 'radio'
            ORDER BY last_accessed_at DESC, id DESC
            LIMIT ?
            """,
            (RECENT_HISTORY_LIMIT,),
        ).fetchall()

    playlist_summaries = fetch_playlist_summaries(db, viewer_id=user.id, owner_id=user.id)
    playlist_lookup = {int(item["id"]): item for item in playlist_summaries}
    playlists = []
    for playlist_id in playlist_ids:
        playlist = playlist_lookup.get(playlist_id)
        if playlist:
            playlists.append(playlist)
        if len(playlists) >= RECENT_ITEMS_LIMIT:
            break

    radios = []
    for row in radio_rows:
        try:
            payload = json.loads(row["item_data_json"] or "{}")
        except Exception:
            payload = {}
        radio_id = str(payload.get("id") or "").strip()
        stream_url = str(payload.get("streamUrl") or "").strip()
        name = str(payload.get("name") or "").strip()
        if radio_id and stream_url and name:
            radios.append({"id": radio_id, "name": name, "streamUrl": stream_url})
        if len(radios) >= RECENT_ITEMS_LIMIT:
            break

    return {"playlists": playlists, "radios": radios}


def _normalize_tracking_event_type(value: str | None) -> str:
    allowed = {"play_start", "play_30s", "play_complete", "skip", "seek", "source_context"}
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else ""


def normalize_tracking_event_type(value: str | None) -> str:
    return _normalize_tracking_event_type(value)


def _normalize_iso_utc(value: str | None) -> str:
    if value:
        parsed = _parse_dt(value)
        if parsed:
            return parsed.isoformat()
    return _iso_now()


def _safe_int_ms(value: Any) -> int:
    try:
        ms = int(float(value or 0))
    except Exception:
        return 0
    return max(0, ms)


def is_wrapped_qualified_play(played_seconds: float, duration_seconds: float | None = None, completed: bool = False) -> bool:
    played = max(0.0, float(played_seconds or 0.0))
    if completed:
        return True
    if played >= WRAPPED_MIN_LISTEN_SECONDS:
        return True
    if duration_seconds is None:
        return False
    try:
        duration = float(duration_seconds)
    except Exception:
        return False
    if duration <= 0:
        return False
    return played >= duration * WRAPPED_MIN_LISTEN_RATIO


def _tracking_dedupe_key(
    *,
    event_type: str,
    track_id: int,
    session_id: str,
    client_event_id: str,
    timestamp_utc: str,
    played_ms: int,
) -> tuple[str, int]:
    if client_event_id:
        return (f"client:{client_event_id.strip()[:120]}", -1)

    parsed = _parse_dt(timestamp_utc) or utcnow()
    bucket = int(parsed.timestamp()) // TRACKING_DEDUPE_WINDOW_SECONDS
    played_bucket = played_ms // 1000
    return (f"auto:{event_type}:{track_id}:{session_id[:120]}:{bucket}:{played_bucket}", bucket)


def record_tracking_event(
    *,
    username: str,
    user_id: int,
    track_id: int,
    event_type: str,
    session_id: str = "",
    played_ms: int = 0,
    duration_ms: int | None = None,
    timestamp_utc: str | None = None,
    context_type: str = "",
    context_key: str = "",
    source_context: str = "",
    wrapped_schema_version: int = TRACKING_SCHEMA_VERSION,
    client_event_id: str = "",
    event_payload: dict[str, Any] | None = None,
) -> bool:
    normalized_event = _normalize_tracking_event_type(event_type)
    if not normalized_event or not int(track_id or 0):
        return False

    ensure_user_activity_db(username)
    ts_utc = _normalize_iso_utc(timestamp_utc)
    safe_played_ms = _safe_int_ms(played_ms)
    safe_duration_ms = _safe_int_ms(duration_ms) if duration_ms is not None else None
    dedupe_key, dedupe_bucket = _tracking_dedupe_key(
        event_type=normalized_event,
        track_id=int(track_id),
        session_id=str(session_id or "").strip(),
        client_event_id=str(client_event_id or "").strip(),
        timestamp_utc=ts_utc,
        played_ms=safe_played_ms,
    )
    payload_json = ""
    if event_payload:
        try:
            payload_json = json.dumps(event_payload, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            payload_json = ""

    with _connect(username) as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO user_tracking_raw (
                event_type, track_id, user_id, session_id, played_ms, duration_ms,
                timestamp_utc, context_type, context_key, source_context,
                wrapped_schema_version, client_event_id, event_payload_json,
                dedupe_bucket, dedupe_key, inserted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_event,
                int(track_id),
                int(user_id or 0),
                str(session_id or "").strip()[:120] or None,
                safe_played_ms,
                safe_duration_ms,
                ts_utc,
                str(context_type or "").strip()[:64] or None,
                str(context_key or "").strip()[:128] or None,
                str(source_context or "").strip()[:128] or None,
                int(wrapped_schema_version or TRACKING_SCHEMA_VERSION),
                str(client_event_id or "").strip()[:120] or None,
                payload_json or None,
                int(dedupe_bucket),
                dedupe_key,
                _iso_now(),
            ),
        )
        conn.commit()
        return cur.rowcount > 0


def record_track_play(
    db: Session,
    user,
    *,
    track_id: int,
    played_seconds: float,
    duration_seconds: float | None = None,
    completed: bool = False,
    skipped_early: bool = False,
    context_type: str = "",
    context_key: str = "",
    write_tracking_backfill: bool = True,
) -> bool:
    if not track_id or played_seconds < MIN_TRACK_PLAY_SECONDS:
        return False

    ensure_user_activity_db(user.username)
    track = db.query(database.Track).filter(database.Track.id == track_id).first()
    if not track:
        return False

    played_seconds = max(0.0, float(played_seconds))
    duration_seconds = float(duration_seconds) if duration_seconds is not None else None
    now_iso = _iso_now()
    artist_name = (track.artist or "").strip()

    with _connect(user.username) as conn:
        conn.execute(
            """
            INSERT INTO listen_events (
                track_id, played_seconds, duration_seconds, completed, skipped_early, context_type, context_key, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(track_id),
                played_seconds,
                duration_seconds,
                1 if completed else 0,
                1 if skipped_early else 0,
                context_type.strip()[:64] or None,
                context_key.strip()[:128] or None,
                now_iso,
            ),
        )
        conn.execute(
            """
            INSERT INTO track_stats (
                track_id, play_count, completion_count, skip_count, total_played_seconds, first_played_at, last_played_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                play_count = track_stats.play_count + 1,
                completion_count = track_stats.completion_count + excluded.completion_count,
                skip_count = track_stats.skip_count + excluded.skip_count,
                total_played_seconds = track_stats.total_played_seconds + excluded.total_played_seconds,
                last_played_at = excluded.last_played_at
            """,
            (
                int(track_id),
                1 if completed else 0,
                1 if skipped_early else 0,
                played_seconds,
                now_iso,
                now_iso,
            ),
        )
        if artist_name:
            conn.execute(
                """
                INSERT INTO artist_stats (
                    artist_name, play_count, completion_count, skip_count, total_played_seconds, first_played_at, last_played_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(artist_name) DO UPDATE SET
                    play_count = artist_stats.play_count + 1,
                    completion_count = artist_stats.completion_count + excluded.completion_count,
                    skip_count = artist_stats.skip_count + excluded.skip_count,
                    total_played_seconds = artist_stats.total_played_seconds + excluded.total_played_seconds,
                    last_played_at = excluded.last_played_at
                """,
                (
                    artist_name,
                    1 if completed else 0,
                    1 if skipped_early else 0,
                    played_seconds,
                    now_iso,
                    now_iso,
                ),
            )
        conn.commit()

    if write_tracking_backfill:
        # Backfill canonical tracking table for legacy clients that only send final listen payload.
        final_event_type = "play_complete" if completed else ("skip" if skipped_early else "play_30s")
        record_tracking_event(
            username=user.username,
            user_id=int(user.id),
            track_id=int(track_id),
            event_type=final_event_type,
            played_ms=int(played_seconds * 1000),
            duration_ms=int(duration_seconds * 1000) if duration_seconds else None,
            timestamp_utc=now_iso,
            context_type=context_type,
            context_key=context_key,
            source_context=context_type,
            client_event_id=f"legacy:{user.id}:{track_id}:{int(time.time() * 1000)}",
            event_payload={
                "legacy": True,
                "completed": bool(completed),
                "skipped_early": bool(skipped_early),
                "qualified_wrapped": is_wrapped_qualified_play(played_seconds, duration_seconds, completed),
            },
        )
    return True


def fetch_user_tracking_events(username: str, year: int) -> list[sqlite3.Row]:
    ensure_user_activity_db(username)
    start = datetime(int(year), 1, 1, tzinfo=timezone.utc).isoformat()
    end = datetime(int(year) + 1, 1, 1, tzinfo=timezone.utc).isoformat()
    with _connect(username) as conn:
        return conn.execute(
            """
            SELECT
                id,
                event_type,
                track_id,
                user_id,
                session_id,
                played_ms,
                duration_ms,
                timestamp_utc,
                context_type,
                context_key,
                source_context,
                wrapped_schema_version
            FROM user_tracking_raw
            WHERE timestamp_utc >= ? AND timestamp_utc < ?
            ORDER BY timestamp_utc ASC, id ASC
            """,
            (start, end),
        ).fetchall()


def get_user_tracking_stats(username: str, year: int) -> dict[str, Any]:
    ensure_user_activity_db(username)
    start = datetime(int(year), 1, 1, tzinfo=timezone.utc).isoformat()
    end = datetime(int(year) + 1, 1, 1, tzinfo=timezone.utc).isoformat()
    with _connect(username) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS raw_event_count,
                MAX(timestamp_utc) AS latest_event_at
            FROM user_tracking_raw
            WHERE timestamp_utc >= ? AND timestamp_utc < ?
            """,
            (start, end),
        ).fetchone()
        raw_event_count = int((row["raw_event_count"] or 0) if row else 0)
        latest_event_at = (row["latest_event_at"] if row else None) or ""
        if raw_event_count <= 0:
            legacy = conn.execute(
                """
                SELECT COUNT(*) AS raw_event_count, MAX(recorded_at) AS latest_event_at
                FROM listen_events
                WHERE recorded_at >= ? AND recorded_at < ?
                """,
                (start, end),
            ).fetchone()
            raw_event_count = int((legacy["raw_event_count"] or 0) if legacy else 0)
            latest_event_at = (legacy["latest_event_at"] if legacy else None) or ""
    return {"raw_event_count": raw_event_count, "latest_event_at": latest_event_at}


def _load_mix_cache(username: str) -> dict[str, Any] | None:
    path = get_mix_cache_path(username)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        if int(payload.get("version") or 0) != MIX_CACHE_VERSION:
            return None
        return payload
    except Exception:
        return None


def _write_mix_cache(username: str, payload: dict[str, Any]) -> None:
    path = get_mix_cache_path(username)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, path)


def _load_top_pool_cache() -> dict[str, Any] | None:
    path = get_top_pool_cache_path()
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        if int(payload.get("version") or 0) != TOP_POOL_CACHE_VERSION:
            return None
        return payload
    except Exception:
        return None


def _write_top_pool_cache(payload: dict[str, Any]) -> None:
    path = get_top_pool_cache_path()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, path)


def _track_to_item(track_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": track_row["source_id"] or f"local:{track_row['id']}",
        "db_id": track_row["id"],
        "title": track_row["title"],
        "artist": track_row["artist"] or "Unknown",
        "album": track_row["album"] or "",
        "thumbnail": f"/api/cover/{track_row['id']}",
        "is_local": True,
        "source": "local",
        "duration": track_row.get("duration") or 0,
    }


def _normalize_artist(value: str | None) -> str:
    return (value or "").strip().lower()


def _normalize_album(value: str | None) -> str:
    return (value or "").strip().lower()


def _recency_bonus(last_played_at: str | None) -> float:
    days = _days_since(last_played_at)
    if days is None:
        return 0.0
    if days <= 1:
        return 6.0
    if days <= 3:
        return 4.0
    if days <= 7:
        return 2.0
    if days <= 14:
        return 1.0
    return 0.0


def _staleness_bonus(last_played_at: str | None) -> float:
    days = _days_since(last_played_at)
    if days is None:
        return 4.0
    if days < 7:
        return -4.0
    if days < 14:
        return 1.0
    if days < 30:
        return 4.0
    if days < 90:
        return 7.0
    return 8.5


def _select_diverse_tracks(candidates: list[dict[str, Any]], *, limit: int = MIX_TRACK_LIMIT, max_per_artist: int = 2) -> list[dict[str, Any]]:
    picked = []
    artist_counts: dict[str, int] = {}
    seen_ids = set()

    for candidate in candidates:
        track_id = int(candidate["id"])
        if track_id in seen_ids:
            continue
        artist_key = candidate.get("artist_key") or f"track:{track_id}"
        if artist_counts.get(artist_key, 0) >= max_per_artist:
            continue
        picked.append(candidate)
        seen_ids.add(track_id)
        artist_counts[artist_key] = artist_counts.get(artist_key, 0) + 1
        if len(picked) >= limit:
            return picked

    if len(picked) >= limit:
        return picked

    for candidate in candidates:
        track_id = int(candidate["id"])
        if track_id in seen_ids:
            continue
        picked.append(candidate)
        seen_ids.add(track_id)
        if len(picked) >= limit:
            break
    return picked


def _candidate_payload(track_row: dict[str, Any], stats: dict[str, Any], *, favorite_ids: set[int], playlist_counts: dict[int, int], artist_affinity: dict[str, Any], favorite_artists: set[str], favorite_albums: set[str]) -> dict[str, Any]:
    track_id = int(track_row["id"])
    artist_key = _normalize_artist(track_row.get("artist"))
    album_key = _normalize_album(track_row.get("album"))
    return {
        "id": track_id,
        "track": track_row,
        "artist_key": artist_key,
        "album_key": album_key,
        "favorite": track_id in favorite_ids,
        "playlist_count": int(playlist_counts.get(track_id) or 0),
        "play_count": int(stats.get("play_count") or 0),
        "completion_count": int(stats.get("completion_count") or 0),
        "skip_count": int(stats.get("skip_count") or 0),
        "total_played_seconds": float(stats.get("total_played_seconds") or 0.0),
        "first_played_at": stats.get("first_played_at"),
        "last_played_at": stats.get("last_played_at"),
        "artist_affinity_play_count": int((artist_affinity.get(artist_key) or {}).get("play_count") or 0),
        "artist_affinity_completion_count": int((artist_affinity.get(artist_key) or {}).get("completion_count") or 0),
        "same_favorite_artist": artist_key in favorite_artists if artist_key else False,
        "same_favorite_album": album_key in favorite_albums if album_key else False,
    }


def _build_repeat_mix(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        candidates,
        key=lambda c: (
            c["play_count"] * 5
            + c["completion_count"] * 6
            + (8 if c["favorite"] else 0)
            + c["playlist_count"] * 2
            + _recency_bonus(c["last_played_at"])
            - c["skip_count"] * 4
        ),
        reverse=True,
    )
    return _select_diverse_tracks(ranked)


def _build_deep_cuts_mix(candidates: list[dict[str, Any]], top_repeat_ids: set[int]) -> list[dict[str, Any]]:
    filtered = [
        c
        for c in candidates
        if c["id"] not in top_repeat_ids and (c["play_count"] >= 2 or c["completion_count"] >= 1 or c["favorite"] or c["playlist_count"] >= 1)
    ]
    ranked = sorted(
        filtered,
        key=lambda c: (
            min(c["play_count"], 6) * 3
            + c["completion_count"] * 4
            + c["playlist_count"] * 2
            + (2 if c["favorite"] else 0)
            + _staleness_bonus(c["last_played_at"])
            - c["skip_count"] * 3
        ),
        reverse=True,
    )
    return _select_diverse_tracks(ranked)


def _build_favorites_mix(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        candidates,
        key=lambda c: (
            (12 if c["favorite"] else 0)
            + (5 if c["same_favorite_artist"] else 0)
            + (3 if c["same_favorite_album"] else 0)
            + min(c["artist_affinity_play_count"], 8)
            + c["completion_count"] * 2
            + c["playlist_count"]
            - c["skip_count"] * 2
        ),
        reverse=True,
    )
    return _select_diverse_tracks(ranked, max_per_artist=3)


def _build_rediscovery_mix(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [
        c
        for c in candidates
        if (
            c["play_count"] > 0
            or c["completion_count"] > 0
            or c["favorite"]
            or c["playlist_count"] > 0
        )
    ]
    ranked = sorted(
        filtered,
        key=lambda c: (
            c["play_count"] * 2
            + c["completion_count"] * 5
            + (5 if c["favorite"] else 0)
            + c["playlist_count"] * 2
            + _staleness_bonus(c["last_played_at"])
            - c["skip_count"] * 3
        ),
        reverse=True,
    )
    ranked = [c for c in ranked if _days_since(c["last_played_at"]) is None or (_days_since(c["last_played_at"]) or 0) >= 7]
    return _select_diverse_tracks(ranked)


def _fallback_mix(candidates: list[dict[str, Any]], *, offset: int = 0) -> list[dict[str, Any]]:
    ranked = sorted(
        candidates,
        key=lambda c: (
            c["favorite"],
            c["playlist_count"],
            c["completion_count"],
            c["play_count"],
            c["track"].get("created_at") or "",
        ),
        reverse=True,
    )
    if offset:
        ranked = ranked[offset:]
    return _select_diverse_tracks(ranked)


def _assemble_mix(key: str, title: str, selected: list[dict[str, Any]]) -> dict[str, Any]:
    items = [_track_to_item(candidate["track"]) for candidate in selected]
    return {
        "key": key,
        "title": title,
        "track_count": len(items),
        "thumbnail": MIX_THUMBNAILS.get(key) or (items[0]["thumbnail"] if items else "/static/img/default_cover.png"),
        "items": items,
    }


def _assemble_track_item_mix(key: str, title: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "track_count": len(items),
        "thumbnail": MIX_THUMBNAILS.get(key) or (items[0]["thumbnail"] if items else "/static/img/default_cover.png"),
        "items": items,
    }


def _generate_top_pool_mix(db: Session) -> dict[str, Any]:
    aggregate: dict[int, dict[str, Any]] = {}
    usernames = [str(username) for (username,) in db.query(database.User.username).all() if username]

    for username in usernames:
        activity_path = get_user_activity_db_path(username)
        if not activity_path.exists():
            continue
        try:
            with sqlite3.connect(str(activity_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT track_id, play_count, completion_count, skip_count, total_played_seconds, last_played_at
                    FROM track_stats
                    """
                ).fetchall()
        except Exception as e:
            logger.warning("Failed to read top-pool stats for %s: %s", username, e)
            continue

        for row in rows:
            track_id = int(row["track_id"] or 0)
            if not track_id:
                continue
            bucket = aggregate.setdefault(
                track_id,
                {
                    "play_count": 0,
                    "completion_count": 0,
                    "skip_count": 0,
                    "total_played_seconds": 0.0,
                    "last_played_at": None,
                },
            )
            bucket["play_count"] += int(row["play_count"] or 0)
            bucket["completion_count"] += int(row["completion_count"] or 0)
            bucket["skip_count"] += int(row["skip_count"] or 0)
            bucket["total_played_seconds"] += float(row["total_played_seconds"] or 0.0)

            row_last_played = row["last_played_at"]
            if row_last_played and (not bucket["last_played_at"] or str(row_last_played) > str(bucket["last_played_at"])):
                bucket["last_played_at"] = row_last_played

    if not aggregate:
        return _assemble_track_item_mix("top_pool_tracks", "Top Pool Tracks", [])

    tracks = db.query(database.Track).filter(database.Track.id.in_(list(aggregate.keys()))).all()
    ranked_candidates = []
    for track in tracks:
        stats = aggregate.get(int(track.id))
        if not stats:
            continue
        ranked_candidates.append(
            {
                "track": {
                    "id": int(track.id),
                    "source_id": track.source_id,
                    "title": track.title,
                    "artist": track.artist,
                    "album": track.album,
                    "duration": track.duration,
                },
                "play_count": int(stats["play_count"]),
                "completion_count": int(stats["completion_count"]),
                "skip_count": int(stats["skip_count"]),
                "total_played_seconds": float(stats["total_played_seconds"]),
                "last_played_at": stats["last_played_at"],
            }
        )

    ranked_candidates.sort(
        key=lambda c: (
            c["play_count"],
            c["completion_count"],
            c["total_played_seconds"],
            c["last_played_at"] or "",
            -c["skip_count"],
        ),
        reverse=True,
    )

    items = [_track_to_item(candidate["track"]) for candidate in ranked_candidates[:TOP_POOL_TRACK_LIMIT]]
    return _assemble_track_item_mix("top_pool_tracks", "Top Pool Tracks", items)


def get_top_pool_mix(db: Session, *, force_refresh: bool = False) -> dict[str, Any]:
    cached = None if force_refresh else _load_top_pool_cache()
    if cached and float(cached.get("expires_at") or 0) > time.time():
        mix = cached.get("mix")
        if isinstance(mix, dict):
            return mix

    mix = _generate_top_pool_mix(db)
    payload = {
        "version": TOP_POOL_CACHE_VERSION,
        "generated_at": _iso_now(),
        "expires_at": time.time() + MIX_CACHE_TTL_SECONDS,
        "mix": mix,
    }
    _write_top_pool_cache(payload)
    return mix


def get_latest_pool_additions_mix(db: Session) -> dict[str, Any]:
    tracks = (
        db.query(database.Track)
        .order_by(database.Track.created_at.desc(), database.Track.id.desc())
        .limit(LATEST_POOL_TRACK_LIMIT)
        .all()
    )

    items = [
        _track_to_item(
            {
                "id": int(track.id),
                "source_id": track.source_id,
                "title": track.title,
                "artist": track.artist,
                "album": track.album,
                "duration": track.duration,
            }
        )
        for track in tracks
    ]
    return _assemble_track_item_mix("latest_pool_additions", "Latest Pool Additions", items)


def _generate_mixes(db: Session, user) -> dict[str, Any]:
    ensure_user_activity_db(user.username)
    with _connect(user.username) as conn:
        track_stats_rows = conn.execute("SELECT * FROM track_stats").fetchall()
        artist_stats_rows = conn.execute("SELECT * FROM artist_stats").fetchall()

    tracks = db.query(database.Track).all()
    favorite_ids = {
        int(track_id)
        for (track_id,) in db.query(database.UserFavorite.track_id).filter(database.UserFavorite.user_id == user.id).all()
    }
    playlist_counts = {
        int(track_id): int(count or 0)
        for track_id, count in (
            db.query(database.PlaylistItem.track_id, func.count(database.PlaylistItem.id))
            .join(database.Playlist, database.Playlist.id == database.PlaylistItem.playlist_id)
            .filter(database.Playlist.owner_id == user.id)
            .group_by(database.PlaylistItem.track_id)
            .all()
        )
    }

    stats_lookup = {int(row["track_id"]): dict(row) for row in track_stats_rows}
    artist_affinity = {_normalize_artist(row["artist_name"]): dict(row) for row in artist_stats_rows if row["artist_name"]}

    favorite_tracks = [track for track in tracks if int(track.id) in favorite_ids]
    favorite_artists = {_normalize_artist(track.artist) for track in favorite_tracks if track.artist}
    favorite_albums = {_normalize_album(track.album) for track in favorite_tracks if track.album}

    candidates = [
        _candidate_payload(
            {
                "id": int(track.id),
                "source_id": track.source_id,
                "title": track.title,
                "artist": track.artist,
                "album": track.album,
                "duration": track.duration,
                "created_at": track.created_at.isoformat() if getattr(track, "created_at", None) else "",
            },
            stats_lookup.get(int(track.id), {}),
            favorite_ids=favorite_ids,
            playlist_counts=playlist_counts,
            artist_affinity=artist_affinity,
            favorite_artists=favorite_artists,
            favorite_albums=favorite_albums,
        )
        for track in tracks
    ]

    repeat_mix = _build_repeat_mix(candidates)
    if not repeat_mix:
        repeat_mix = _fallback_mix(candidates)

    top_repeat_ids = {candidate["id"] for candidate in repeat_mix[:TOP_REPEAT_EXCLUDE_COUNT]}

    deep_cuts_mix = _build_deep_cuts_mix(candidates, top_repeat_ids)
    if not deep_cuts_mix:
        deep_cuts_mix = _fallback_mix(candidates, offset=min(len(repeat_mix), 8))

    favorites_mix = _build_favorites_mix(candidates)
    if not favorites_mix:
        favorites_mix = repeat_mix[:]

    rediscovery_mix = _build_rediscovery_mix(candidates)
    if not rediscovery_mix:
        rediscovery_mix = _fallback_mix(sorted(candidates, key=lambda c: c["track"].get("created_at") or ""), offset=6)

    mixes = [
        _assemble_mix("repeat", "Repeat Mix", repeat_mix),
        _assemble_mix("deep_cuts", "Deep Cuts Mix", deep_cuts_mix),
        _assemble_mix("favorites", "Favorites Mix", favorites_mix),
        _assemble_mix("rediscovery", "Rediscovery Mix", rediscovery_mix),
    ]
    now_ts = time.time()
    return {
        "version": MIX_CACHE_VERSION,
        "generated_at": _iso_now(),
        "expires_at": now_ts + MIX_CACHE_TTL_SECONDS,
        "mixes": mixes,
    }


def get_personalized_mixes(db: Session, user, *, force_refresh: bool = False) -> dict[str, Any]:
    cached = None if force_refresh else _load_mix_cache(user.username)
    if cached and float(cached.get("expires_at") or 0) > time.time():
        return cached

    payload = _generate_mixes(db, user)
    _write_mix_cache(user.username, payload)
    return payload


def get_mix_summaries(db: Session, user, *, force_refresh: bool = False) -> list[dict[str, Any]]:
    payload = get_personalized_mixes(db, user, force_refresh=force_refresh)
    mixes = [
        {
            "key": mix["key"],
            "title": mix["title"],
            "track_count": mix["track_count"],
            "thumbnail": mix["thumbnail"],
        }
        for mix in payload.get("mixes", [])
        if mix.get("items")
    ]
    top_pool_mix = get_top_pool_mix(db, force_refresh=force_refresh)
    if top_pool_mix.get("items"):
        mixes.append(
            {
                "key": top_pool_mix["key"],
                "title": top_pool_mix["title"],
                "track_count": top_pool_mix["track_count"],
                "thumbnail": top_pool_mix["thumbnail"],
            }
        )

    latest_pool_mix = get_latest_pool_additions_mix(db)
    if latest_pool_mix.get("items"):
        mixes.append(
            {
                "key": latest_pool_mix["key"],
                "title": latest_pool_mix["title"],
                "track_count": latest_pool_mix["track_count"],
                "thumbnail": latest_pool_mix["thumbnail"],
            }
        )
    return mixes


def get_mix_detail(db: Session, user, mix_key: str, *, force_refresh: bool = False) -> dict[str, Any] | None:
    if mix_key == "top_pool_tracks":
        mix = get_top_pool_mix(db, force_refresh=force_refresh)
        return mix if mix.get("items") else None
    if mix_key == "latest_pool_additions":
        mix = get_latest_pool_additions_mix(db)
        return mix if mix.get("items") else None

    payload = get_personalized_mixes(db, user, force_refresh=force_refresh)
    for mix in payload.get("mixes", []):
        if mix.get("key") == mix_key and mix.get("items"):
            return mix
    return None
