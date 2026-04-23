from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any

import database
import ops_core as ops
import personalization_service
from job_service import create_admin_job, update_admin_job_progress
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

WRAPPED_SUMMARY_DB_NAME = "wrapped_summary.db"
WRAPPED_SUMMARY_VERSION = 2
WRAPPED_SCHEMA_VERSION = 1
WRAPPED_TOP_TRACK_LIMIT = 100
WRAPPED_TOP_DISPLAY_LIMIT = 5
WRAPPED_MAX_REASONABLE_SECONDS = 365 * 24 * 60 * 60
DEFAULT_ARTIST_CLIP_MESSAGE = "Your year had range. The admin can make this message worse later."
DAILY_WRAPPED_RUN_STATE_KEY = "wrapped_daily_last_run_utc"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return utcnow().isoformat()


def get_wrapped_summary_db_path() -> Path:
    cache_dir = Path("/saas-data/cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / WRAPPED_SUMMARY_DB_NAME


def _connect_summary() -> sqlite3.Connection:
    path = get_wrapped_summary_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def ensure_wrapped_summary_db() -> Path:
    with _connect_summary() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_wrapped_aggregate (
                year INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                wrapped_schema_version INTEGER NOT NULL DEFAULT 1,
                source_latest_event_at TEXT,
                raw_event_count INTEGER NOT NULL DEFAULT 0,
                qualified_event_count INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (year, user_id)
            );

            CREATE INDEX IF NOT EXISTS ix_user_wrapped_aggregate_username_year
                ON user_wrapped_aggregate(username, year);

            CREATE TABLE IF NOT EXISTS wrapped_party_aggregate (
                year INTEGER PRIMARY KEY,
                generated_at TEXT NOT NULL,
                wrapped_schema_version INTEGER NOT NULL DEFAULT 1,
                source_user_generated_at TEXT,
                user_count INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wrapped_regeneration_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                year INTEGER NOT NULL,
                scope TEXT NOT NULL,
                target_username TEXT,
                triggered_by TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                wrapped_schema_version INTEGER NOT NULL DEFAULT 1,
                raw_event_count INTEGER NOT NULL DEFAULT 0,
                qualified_event_count INTEGER NOT NULL DEFAULT 0,
                user_count INTEGER NOT NULL DEFAULT 0,
                message TEXT
            );

            CREATE INDEX IF NOT EXISTS ix_wrapped_regeneration_audit_year_started
                ON wrapped_regeneration_audit(year, started_at DESC);

            CREATE TABLE IF NOT EXISTS wrapped_yearly_snapshots (
                year INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                finalized_at TEXT NOT NULL,
                wrapped_schema_version INTEGER NOT NULL DEFAULT 1,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (year, user_id)
            );

            CREATE TABLE IF NOT EXISTS wrapped_yearly_party_snapshots (
                year INTEGER PRIMARY KEY,
                finalized_at TEXT NOT NULL,
                wrapped_schema_version INTEGER NOT NULL DEFAULT 1,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wrapped_job_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    return get_wrapped_summary_db_path()


def _year_bounds(year: int) -> tuple[str, str]:
    year = int(year)
    start = datetime(year, 1, 1, tzinfo=timezone.utc).isoformat()
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc).isoformat()
    return start, end


def _safe_year(year: int | None = None) -> int:
    candidate = int(year or utcnow().year)
    if candidate < 2020 or candidate > utcnow().year + 1:
        raise ValueError("Invalid wrapped year")
    return candidate


def normalize_year(year: int | None = None) -> int:
    return _safe_year(year)


def _parse_visibility_dt(value: str | None, default_tz=timezone.utc) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=default_tz).astimezone(timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _display_dt(value: str | None, display_tz=timezone.utc) -> str:
    parsed = _parse_visibility_dt(value)
    if not parsed:
        return ""
    return parsed.astimezone(display_tz).strftime("%Y-%m-%dT%H:%M")


def _display_date(value: str | None, display_tz=timezone.utc) -> str:
    parsed = _parse_visibility_dt(value)
    if not parsed:
        return ""
    return parsed.astimezone(display_tz).strftime("%Y-%m-%d")


def _display_time(value: str | None, display_tz=timezone.utc) -> str:
    parsed = _parse_visibility_dt(value)
    if not parsed:
        return ""
    return parsed.astimezone(display_tz).strftime("%H:%M")


def get_wrapped_settings(db: Session) -> dict[str, Any]:
    settings = ops.ensure_system_settings_record(db)
    scheduler_tz = ops.get_scheduler_timezone(settings)
    now = utcnow()
    visible_from = _parse_visibility_dt(settings.wrapped_visible_from)
    visible_until = _parse_visibility_dt(settings.wrapped_visible_until)
    window_open = (visible_from is None or visible_from <= now) and (visible_until is None or visible_until >= now)
    enabled = bool(settings.wrapped_enabled)
    return {
        "enabled": enabled,
        "visible": enabled and window_open,
        "visible_from": settings.wrapped_visible_from,
        "visible_until": settings.wrapped_visible_until,
        "visible_from_input": _display_dt(settings.wrapped_visible_from, scheduler_tz),
        "visible_until_input": _display_dt(settings.wrapped_visible_until, scheduler_tz),
        "visible_from_date_input": _display_date(settings.wrapped_visible_from, scheduler_tz),
        "visible_from_time_input": _display_time(settings.wrapped_visible_from, scheduler_tz),
        "visible_until_date_input": _display_date(settings.wrapped_visible_until, scheduler_tz),
        "visible_until_time_input": _display_time(settings.wrapped_visible_until, scheduler_tz),
        "timezone": ops.get_scheduler_timezone_name(settings),
        "artist_clip_message": settings.wrapped_artist_clip_message or DEFAULT_ARTIST_CLIP_MESSAGE,
        "year": normalize_year(),
        "wrapped_schema_version": WRAPPED_SCHEMA_VERSION,
    }


def update_wrapped_settings(
    db: Session,
    *,
    enabled: bool,
    visible_from: str | None = None,
    visible_until: str | None = None,
    artist_clip_message: str | None = None,
) -> dict[str, Any]:
    settings = ops.ensure_system_settings_record(db)
    scheduler_tz = ops.get_scheduler_timezone(settings)

    def normalize_raw_dt(value: str | None) -> str | None:
        parsed = _parse_visibility_dt(value, scheduler_tz)
        return parsed.isoformat() if parsed else None

    settings.wrapped_enabled = bool(enabled)
    settings.wrapped_visible_from = normalize_raw_dt(visible_from)
    settings.wrapped_visible_until = normalize_raw_dt(visible_until)
    message = (artist_clip_message or "").strip()
    settings.wrapped_artist_clip_message = message or None
    db.commit()
    return get_wrapped_settings(db)


def _safe_played_seconds(value: Any) -> float:
    try:
        seconds = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not isfinite(seconds) or seconds < 0:
        return 0.0
    return min(seconds, WRAPPED_MAX_REASONABLE_SECONDS)


def _track_lookup(db: Session, track_ids: set[int]) -> dict[int, database.Track]:
    if not track_ids:
        return {}
    tracks = db.query(database.Track).filter(database.Track.id.in_(sorted(track_ids))).all()
    return {int(track.id): track for track in tracks}


def _artist_key(track: database.Track | None) -> str:
    if not track or not track.artist:
        return "Unknown Artist"
    return " ".join(track.artist.strip().split()) or "Unknown Artist"


def _build_artist_sprint(monthly_artist_stats: dict[int, dict[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    sprint = []
    for month in range(1, 13):
        artists = monthly_artist_stats.get(month, {})
        ranked = sorted(
            artists.items(),
            key=lambda item: (
                float(item[1].get("played_seconds") or 0.0),
                int(item[1].get("stream_count") or 0),
                item[0].lower(),
            ),
            reverse=True,
        )
        sprint.append(
            {
                "month": month,
                "artists": [
                    {
                        "rank": idx + 1,
                        "artist": artist,
                        "played_seconds": round(float(stats.get("played_seconds") or 0.0), 2),
                        "stream_count": int(stats.get("stream_count") or 0),
                    }
                    for idx, (artist, stats) in enumerate(ranked[:WRAPPED_TOP_DISPLAY_LIMIT])
                ],
            }
        )
    return sprint


def _track_payload(track: database.Track, stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(track.id),
        "db_id": int(track.id),
        "source_id": track.source_id,
        "title": track.title or "Unknown",
        "artist": track.artist or "Unknown Artist",
        "album": track.album or "",
        "duration": int(track.duration or 0),
        "thumbnail": f"/api/cover/{track.id}",
        "stream_count": int(stats.get("stream_count") or 0),
        "completion_count": int(stats.get("completion_count") or 0),
        "skip_count": int(stats.get("skip_count") or 0),
        "played_seconds": round(_safe_played_seconds(stats.get("played_seconds")), 2),
    }


def _fetch_legacy_activity_rows(username: str, year: int) -> list[dict[str, Any]]:
    activity_path = personalization_service.get_user_activity_db_path(username)
    if not activity_path.exists():
        return []
    start, end = _year_bounds(year)
    try:
        with sqlite3.connect(str(activity_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, track_id, played_seconds, duration_seconds, completed, skipped_early, context_type, context_key, recorded_at
                FROM listen_events
                WHERE recorded_at >= ? AND recorded_at < ?
                ORDER BY recorded_at ASC, id ASC
                """,
                (start, end),
            ).fetchall()
            return [
                {
                    "id": int(row["id"] or 0),
                    "event_type": "play_complete" if int(row["completed"] or 0) else ("skip" if int(row["skipped_early"] or 0) else "play_30s"),
                    "track_id": int(row["track_id"] or 0),
                    "session_id": "",
                    "played_seconds": _safe_played_seconds(row["played_seconds"]),
                    "duration_seconds": _safe_played_seconds(row["duration_seconds"]),
                    "timestamp_utc": str(row["recorded_at"] or ""),
                    "context_type": str(row["context_type"] or ""),
                    "context_key": str(row["context_key"] or ""),
                    "source_context": str(row["context_type"] or ""),
                }
                for row in rows
            ]
    except sqlite3.Error as e:
        logger.warning("Failed to read legacy wrapped activity for %s/%s: %s", username, year, e)
        return []


def _fetch_canonical_tracking_rows(username: str, year: int) -> list[dict[str, Any]]:
    try:
        rows = personalization_service.fetch_user_tracking_events(username, year)
    except sqlite3.Error as e:
        logger.warning("Failed to read canonical wrapped tracking for %s/%s: %s", username, year, e)
        rows = []
    result = []
    for row in rows:
        result.append(
            {
                "id": int(row["id"] or 0),
                "event_type": str(row["event_type"] or "").strip().lower(),
                "track_id": int(row["track_id"] or 0),
                "session_id": str(row["session_id"] or ""),
                "played_seconds": _safe_played_seconds((row["played_ms"] or 0) / 1000.0),
                "duration_seconds": _safe_played_seconds((row["duration_ms"] or 0) / 1000.0) if row["duration_ms"] else 0.0,
                "timestamp_utc": str(row["timestamp_utc"] or ""),
                "context_type": str(row["context_type"] or ""),
                "context_key": str(row["context_key"] or ""),
                "source_context": str(row["source_context"] or ""),
            }
        )
    return result


def _collect_terminal_instances(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    terminal_types = {"play_30s", "play_complete", "skip"}
    priority = {"play_30s": 1, "skip": 2, "play_complete": 3}
    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        event_type = str(row.get("event_type") or "")
        if event_type not in terminal_types:
            continue
        track_id = int(row.get("track_id") or 0)
        if track_id <= 0:
            continue
        session_id = str(row.get("session_id") or "").strip()
        instance_key = (session_id or f"row:{int(row.get('id') or 0)}", track_id)
        candidate_priority = priority.get(event_type, 0)
        existing = grouped.get(instance_key)
        if not existing:
            grouped[instance_key] = dict(row)
            continue
        existing_priority = priority.get(str(existing.get("event_type") or ""), 0)
        if candidate_priority > existing_priority:
            grouped[instance_key] = dict(row)
            continue
        if candidate_priority == existing_priority and _safe_played_seconds(row.get("played_seconds")) > _safe_played_seconds(
            existing.get("played_seconds")
        ):
            grouped[instance_key] = dict(row)
    return list(grouped.values())


def _validate_summary_payload(payload: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    minutes = float(payload.get("minutes_listened") or 0.0)
    played_seconds = float(payload.get("played_seconds") or 0.0)
    if minutes < 0 or played_seconds < 0:
        warnings.append("negative_duration_guard_triggered")
    if played_seconds > WRAPPED_MAX_REASONABLE_SECONDS:
        warnings.append("listening_time_outlier_capped")
    top_tracks = payload.get("top_songs_playlist", {}).get("tracks") or []
    if any(int(item.get("stream_count") or 0) <= 0 for item in top_tracks):
        warnings.append("top_tracks_with_non_positive_stream_count")
    return {"warnings": warnings, "warning_count": len(warnings)}


def _build_user_summary_from_rows(db: Session, user: database.User, year: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    track_ids = {int(row["track_id"]) for row in rows if int(row.get("track_id") or 0) > 0}
    tracks_by_id = _track_lookup(db, track_ids)
    terminal_instances = _collect_terminal_instances(rows)

    total_played_seconds = 0.0
    qualified_event_count = 0
    track_stats: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"stream_count": 0, "completion_count": 0, "skip_count": 0, "played_seconds": 0.0}
    )
    artist_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"stream_count": 0, "completion_count": 0, "skip_count": 0, "played_seconds": 0.0}
    )
    monthly_artist_stats: dict[int, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(lambda: {"stream_count": 0, "played_seconds": 0.0})
    )

    latest_event_at = ""
    for row in rows:
        ts = str(row.get("timestamp_utc") or "")
        if ts and (not latest_event_at or ts > latest_event_at):
            latest_event_at = ts

    for row in terminal_instances:
        track_id = int(row.get("track_id") or 0)
        track = tracks_by_id.get(track_id)
        if not track:
            continue
        played = _safe_played_seconds(row.get("played_seconds"))
        duration = _safe_played_seconds(row.get("duration_seconds"))
        event_type = str(row.get("event_type") or "")
        completed = event_type == "play_complete"
        skipped = event_type == "skip"
        qualified = personalization_service.is_wrapped_qualified_play(played, duration, completed)
        if not qualified:
            continue

        qualified_event_count += 1
        total_played_seconds += played

        tstats = track_stats[track_id]
        tstats["stream_count"] += 1
        tstats["completion_count"] += 1 if completed else 0
        tstats["skip_count"] += 1 if skipped else 0
        tstats["played_seconds"] += played

        artist = _artist_key(track)
        astats = artist_stats[artist]
        astats["stream_count"] += 1
        astats["completion_count"] += 1 if completed else 0
        astats["skip_count"] += 1 if skipped else 0
        astats["played_seconds"] += played

        try:
            month = datetime.fromisoformat(str(row.get("timestamp_utc") or "")).month
        except Exception:
            month = 0
        if 1 <= month <= 12:
            monthly_artist_stats[month][artist]["stream_count"] += 1
            monthly_artist_stats[month][artist]["played_seconds"] += played

    ranked_tracks = sorted(
        track_stats.items(),
        key=lambda item: (
            _safe_played_seconds(item[1].get("played_seconds")),
            int(item[1].get("stream_count") or 0),
            int(item[1].get("completion_count") or 0),
        ),
        reverse=True,
    )
    top_tracks = [
        _track_payload(tracks_by_id[track_id], stats)
        for track_id, stats in ranked_tracks[:WRAPPED_TOP_TRACK_LIMIT]
        if track_id in tracks_by_id
    ]

    ranked_artists = sorted(
        artist_stats.items(),
        key=lambda item: (
            _safe_played_seconds(item[1].get("played_seconds")),
            int(item[1].get("stream_count") or 0),
            item[0].lower(),
        ),
        reverse=True,
    )
    top_artists = [
        {
            "rank": idx + 1,
            "artist": artist,
            "played_seconds": round(_safe_played_seconds(stats.get("played_seconds")), 2),
            "stream_count": int(stats.get("stream_count") or 0),
            "completion_count": int(stats.get("completion_count") or 0),
        }
        for idx, (artist, stats) in enumerate(ranked_artists[:WRAPPED_TOP_DISPLAY_LIMIT])
    ]

    payload = {
        "version": WRAPPED_SUMMARY_VERSION,
        "wrapped_schema_version": WRAPPED_SCHEMA_VERSION,
        "year": year,
        "generated_at": _iso_now(),
        "source_latest_event_at": latest_event_at or None,
        "raw_event_count": len(rows),
        "qualified_event_count": qualified_event_count,
        "user": {"id": int(user.id), "username": user.username},
        "minutes_listened": round(total_played_seconds / 60.0, 2),
        "played_seconds": round(total_played_seconds, 2),
        "event_count": len(rows),
        "top_songs": top_tracks[:WRAPPED_TOP_DISPLAY_LIMIT],
        "top_songs_playlist": {
            "title": f"Your Top Songs {year}",
            "track_count": len(top_tracks),
            "tracks": top_tracks,
        },
        "top_artists": top_artists,
        "top_genres": [],
        "top_artist_sprint": _build_artist_sprint(monthly_artist_stats),
        "artist_clip": {
            "title": "A message from Navipod",
            "message": get_wrapped_settings(db)["artist_clip_message"],
        },
        "data_quality": {
            "has_listen_events": bool(rows),
            "genres_available": False,
            "genres_note": "Genre metadata is not stored consistently yet, so Wrapped leaves this empty instead of guessing.",
        },
    }
    payload["data_quality"]["validation"] = _validate_summary_payload(payload)
    return payload


def build_user_wrapped_summary(db: Session, user: database.User, year: int | None = None) -> dict[str, Any]:
    year = _safe_year(year)
    rows = _fetch_canonical_tracking_rows(user.username, year)
    if not rows:
        rows = _fetch_legacy_activity_rows(user.username, year)
    return _build_user_summary_from_rows(db, user, year, rows)


def _cached_user_meta(user_id: int, year: int) -> sqlite3.Row | None:
    ensure_wrapped_summary_db()
    with _connect_summary() as conn:
        return conn.execute(
            """
            SELECT wrapped_schema_version, source_latest_event_at, raw_event_count
            FROM user_wrapped_aggregate
            WHERE year = ? AND user_id = ?
            """,
            (int(year), int(user_id)),
        ).fetchone()


def save_user_wrapped_summary(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_wrapped_summary_db()
    user = payload["user"]
    with _connect_summary() as conn:
        conn.execute(
            """
            INSERT INTO user_wrapped_aggregate (
                year, user_id, username, generated_at, wrapped_schema_version,
                source_latest_event_at, raw_event_count, qualified_event_count, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(year, user_id) DO UPDATE SET
                username = excluded.username,
                generated_at = excluded.generated_at,
                wrapped_schema_version = excluded.wrapped_schema_version,
                source_latest_event_at = excluded.source_latest_event_at,
                raw_event_count = excluded.raw_event_count,
                qualified_event_count = excluded.qualified_event_count,
                payload_json = excluded.payload_json
            """,
            (
                int(payload["year"]),
                int(user["id"]),
                str(user["username"]),
                str(payload["generated_at"]),
                int(payload.get("wrapped_schema_version") or WRAPPED_SCHEMA_VERSION),
                str(payload.get("source_latest_event_at") or ""),
                int(payload.get("raw_event_count") or 0),
                int(payload.get("qualified_event_count") or 0),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        conn.commit()
    return payload


def get_cached_user_wrapped_summary(user_id: int, year: int) -> dict[str, Any] | None:
    ensure_wrapped_summary_db()
    with _connect_summary() as conn:
        row = conn.execute(
            "SELECT payload_json FROM user_wrapped_aggregate WHERE year = ? AND user_id = ?",
            (int(year), int(user_id)),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["payload_json"])
    except Exception:
        return None


def _is_user_aggregate_stale(user: database.User, year: int, cached: dict[str, Any] | None) -> bool:
    if not cached:
        return True
    if int(cached.get("wrapped_schema_version") or 0) != WRAPPED_SCHEMA_VERSION:
        return True
    stats = personalization_service.get_user_tracking_stats(user.username, year)
    cached_latest = str(cached.get("source_latest_event_at") or "")
    cached_raw_count = int(cached.get("raw_event_count") or 0)
    return cached_latest != str(stats.get("latest_event_at") or "") or cached_raw_count != int(stats.get("raw_event_count") or 0)


def get_or_build_user_wrapped_summary(
    db: Session, user: database.User, year: int | None = None, *, force_refresh: bool = False
) -> dict[str, Any]:
    year = _safe_year(year)
    cached = get_cached_user_wrapped_summary(int(user.id), year)
    if not force_refresh and not _is_user_aggregate_stale(user, year, cached):
        return cached or {}
    payload = build_user_wrapped_summary(db, user, year)
    return save_user_wrapped_summary(payload)


def _party_source_marker(year: int) -> str:
    ensure_wrapped_summary_db()
    with _connect_summary() as conn:
        row = conn.execute(
            "SELECT MAX(generated_at) AS max_generated_at FROM user_wrapped_aggregate WHERE year = ?",
            (int(year),),
        ).fetchone()
    return str((row["max_generated_at"] if row else "") or "")


def build_party_summary(db: Session, year: int | None = None) -> dict[str, Any]:
    year = _safe_year(year)
    users = db.query(database.User).filter(database.User.is_active == True).order_by(database.User.username.asc()).all()
    summaries = [get_or_build_user_wrapped_summary(db, user, year) for user in users]
    active_summaries = [
        summary
        for summary in summaries
        if _safe_played_seconds(summary.get("played_seconds")) > 0 and int(summary.get("qualified_event_count") or 0) > 0
    ]

    minutes_ranking = sorted(
        active_summaries,
        key=lambda item: (_safe_played_seconds(item.get("played_seconds")), int(item.get("qualified_event_count") or 0)),
        reverse=True,
    )
    top_song_fans = []
    for summary in summaries:
        if _safe_played_seconds(summary.get("played_seconds")) <= 0:
            continue
        top_song = (summary.get("top_songs") or [None])[0]
        if top_song:
            top_song_fans.append(
                {
                    "username": summary["user"]["username"],
                    "track": top_song,
                    "stream_count": int(top_song.get("stream_count") or 0),
                    "played_seconds": _safe_played_seconds(top_song.get("played_seconds")),
                }
            )
    top_song_fans.sort(key=lambda item: (item["stream_count"], item["played_seconds"]), reverse=True)

    party = {
        "version": WRAPPED_SUMMARY_VERSION,
        "wrapped_schema_version": WRAPPED_SCHEMA_VERSION,
        "year": year,
        "generated_at": _iso_now(),
        "source_user_generated_at": _party_source_marker(year),
        "user_count": len(summaries),
        "most_minutes_listened": [
            {
                "rank": idx + 1,
                "username": item["user"]["username"],
                "minutes_listened": item.get("minutes_listened", 0),
                "played_seconds": item.get("played_seconds", 0),
            }
            for idx, item in enumerate(minutes_ranking[:10])
        ],
        "biggest_repeaters": top_song_fans[:10],
    }
    save_party_summary(party)
    return party


def save_party_summary(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_wrapped_summary_db()
    with _connect_summary() as conn:
        conn.execute(
            """
            INSERT INTO wrapped_party_aggregate (
                year, generated_at, wrapped_schema_version, source_user_generated_at, user_count, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(year) DO UPDATE SET
                generated_at = excluded.generated_at,
                wrapped_schema_version = excluded.wrapped_schema_version,
                source_user_generated_at = excluded.source_user_generated_at,
                user_count = excluded.user_count,
                payload_json = excluded.payload_json
            """,
            (
                int(payload["year"]),
                str(payload["generated_at"]),
                int(payload.get("wrapped_schema_version") or WRAPPED_SCHEMA_VERSION),
                str(payload.get("source_user_generated_at") or ""),
                int(payload.get("user_count") or 0),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        conn.commit()
    return payload


def get_cached_party_summary(year: int) -> dict[str, Any] | None:
    ensure_wrapped_summary_db()
    with _connect_summary() as conn:
        row = conn.execute("SELECT payload_json FROM wrapped_party_aggregate WHERE year = ?", (int(year),)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["payload_json"])
    except Exception:
        return None


def _is_party_stale(year: int, cached: dict[str, Any] | None) -> bool:
    if not cached:
        return True
    if int(cached.get("wrapped_schema_version") or 0) != WRAPPED_SCHEMA_VERSION:
        return True
    return str(cached.get("source_user_generated_at") or "") != _party_source_marker(year)


def get_or_build_party_summary(db: Session, year: int | None = None, *, force_refresh: bool = False) -> dict[str, Any]:
    year = _safe_year(year)
    cached = get_cached_party_summary(year)
    if not force_refresh and not _is_party_stale(year, cached):
        return cached or {}
    return build_party_summary(db, year)


def _upsert_job_state(key: str, value: str) -> None:
    ensure_wrapped_summary_db()
    with _connect_summary() as conn:
        conn.execute(
            """
            INSERT INTO wrapped_job_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, _iso_now()),
        )
        conn.commit()


def _get_job_state(key: str) -> str:
    ensure_wrapped_summary_db()
    with _connect_summary() as conn:
        row = conn.execute("SELECT value FROM wrapped_job_state WHERE key = ?", (key,)).fetchone()
    return str((row["value"] if row else "") or "")


def _snapshot_year_closed(year: int) -> bool:
    close_at = datetime(int(year) + 1, 1, 1, tzinfo=timezone.utc)
    return utcnow() >= close_at


def finalize_wrapped_year_snapshot(db: Session, year: int) -> dict[str, Any]:
    year = _safe_year(year)
    if not _snapshot_year_closed(year):
        return {"year": year, "finalized": False, "reason": "year_not_closed"}

    users = db.query(database.User).filter(database.User.is_active == True).order_by(database.User.username.asc()).all()
    user_snapshot_count = 0
    with _connect_summary() as conn:
        for user in users:
            summary = get_or_build_user_wrapped_summary(db, user, year, force_refresh=False)
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO wrapped_yearly_snapshots (
                    year, user_id, username, finalized_at, wrapped_schema_version, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(year),
                    int(user.id),
                    str(user.username),
                    _iso_now(),
                    int(summary.get("wrapped_schema_version") or WRAPPED_SCHEMA_VERSION),
                    json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            user_snapshot_count += 1 if cur.rowcount > 0 else 0

        party = get_or_build_party_summary(db, year, force_refresh=False)
        conn.execute(
            """
            INSERT OR IGNORE INTO wrapped_yearly_party_snapshots (
                year, finalized_at, wrapped_schema_version, payload_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                int(year),
                _iso_now(),
                int(party.get("wrapped_schema_version") or WRAPPED_SCHEMA_VERSION),
                json.dumps(party, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        conn.commit()

    return {"year": year, "finalized": True, "new_user_snapshots": user_snapshot_count}


def regenerate_wrapped_year(
    db: Session,
    year: int | None = None,
    *,
    target_username: str | None = None,
    force_refresh: bool = True,
) -> dict[str, Any]:
    year = _safe_year(year)
    ensure_wrapped_summary_db()

    query = db.query(database.User).filter(database.User.is_active == True)
    if target_username:
        query = query.filter(database.User.username == target_username)
    users = query.order_by(database.User.username.asc()).all()

    generated = []
    raw_event_total = 0
    qualified_event_total = 0
    for user in users:
        if force_refresh:
            payload = save_user_wrapped_summary(build_user_wrapped_summary(db, user, year))
        else:
            payload = get_or_build_user_wrapped_summary(db, user, year, force_refresh=False)
        generated.append(payload)
        raw_event_total += int(payload.get("raw_event_count") or 0)
        qualified_event_total += int(payload.get("qualified_event_count") or 0)

    party = get_or_build_party_summary(db, year, force_refresh=True)
    snapshot = finalize_wrapped_year_snapshot(db, year)

    return {
        "year": year,
        "wrapped_schema_version": WRAPPED_SCHEMA_VERSION,
        "scope": "single_user" if target_username else "all_users",
        "target_username": target_username or "",
        "user_count": len(generated),
        "party_user_count": int(party.get("user_count") or 0),
        "raw_event_count": raw_event_total,
        "qualified_event_count": qualified_event_total,
        "snapshot": snapshot,
        "summary_db": str(get_wrapped_summary_db_path()),
    }


def _create_regeneration_audit(
    *,
    year: int,
    scope: str,
    target_username: str = "",
    triggered_by: str = "",
    job_id: int | None = None,
    status: str = "running",
    message: str = "",
) -> int:
    ensure_wrapped_summary_db()
    with _connect_summary() as conn:
        cur = conn.execute(
            """
            INSERT INTO wrapped_regeneration_audit (
                job_id, year, scope, target_username, triggered_by, started_at,
                status, wrapped_schema_version, message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(job_id) if job_id is not None else None,
                int(year),
                scope,
                target_username or None,
                triggered_by or None,
                _iso_now(),
                status,
                WRAPPED_SCHEMA_VERSION,
                message or None,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def _finalize_regeneration_audit(
    audit_id: int,
    *,
    status: str,
    message: str = "",
    raw_event_count: int = 0,
    qualified_event_count: int = 0,
    user_count: int = 0,
) -> None:
    ensure_wrapped_summary_db()
    with _connect_summary() as conn:
        conn.execute(
            """
            UPDATE wrapped_regeneration_audit
            SET completed_at = ?, status = ?, raw_event_count = ?, qualified_event_count = ?, user_count = ?, message = ?
            WHERE id = ?
            """,
            (_iso_now(), status, int(raw_event_count), int(qualified_event_count), int(user_count), message or None, int(audit_id)),
        )
        conn.commit()


def get_latest_regeneration_audit(year: int | None = None) -> dict[str, Any] | None:
    ensure_wrapped_summary_db()
    with _connect_summary() as conn:
        if year is None:
            row = conn.execute(
                """
                SELECT *
                FROM wrapped_regeneration_audit
                ORDER BY started_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT *
                FROM wrapped_regeneration_audit
                WHERE year = ?
                ORDER BY started_at DESC, id DESC
                LIMIT 1
                """,
                (int(year),),
            ).fetchone()
    if not row:
        return None
    return dict(row)


def run_wrapped_regeneration_job(job_id: int, year: int) -> None:
    db = database.SessionLocal()
    audit_id = _create_regeneration_audit(
        year=year,
        scope="all_users",
        triggered_by="admin_job",
        job_id=job_id,
        status="running",
        message=f"Regenerating Wrapped {year}",
    )
    try:
        update_admin_job_progress(
            job_id,
            status="running",
            message=f"Regenerating Wrapped {year}",
            phase="wrapped",
            progress=10,
        )
        result = regenerate_wrapped_year(db, year, force_refresh=True)
        _finalize_regeneration_audit(
            audit_id,
            status="completed",
            message=f"Wrapped {year} regenerated",
            raw_event_count=int(result.get("raw_event_count") or 0),
            qualified_event_count=int(result.get("qualified_event_count") or 0),
            user_count=int(result.get("user_count") or 0),
        )
        update_admin_job_progress(
            job_id,
            status="completed",
            message=f"Wrapped {year} regenerated for {result['user_count']} user(s)",
            phase="completed",
            progress=100,
            extra={"result": result},
            finished=True,
        )
    except Exception as e:
        _finalize_regeneration_audit(audit_id, status="failed", message=str(e))
        update_admin_job_progress(
            job_id,
            status="failed",
            message=f"Wrapped regeneration failed: {e}",
            phase="failed",
            progress=100,
            extra={"error": str(e)},
            finished=True,
        )
    finally:
        db.close()


def run_wrapped_user_regeneration_job(job_id: int, year: int, username: str) -> None:
    db = database.SessionLocal()
    audit_id = _create_regeneration_audit(
        year=year,
        scope="single_user",
        target_username=username,
        triggered_by="admin_job",
        job_id=job_id,
        status="running",
        message=f"Regenerating Wrapped {year} for {username}",
    )
    try:
        update_admin_job_progress(
            job_id,
            status="running",
            message=f"Regenerating Wrapped {year} for {username}",
            phase="wrapped",
            progress=15,
        )
        result = regenerate_wrapped_year(db, year, target_username=username, force_refresh=True)
        _finalize_regeneration_audit(
            audit_id,
            status="completed",
            message=f"Wrapped {year} regenerated for {username}",
            raw_event_count=int(result.get("raw_event_count") or 0),
            qualified_event_count=int(result.get("qualified_event_count") or 0),
            user_count=int(result.get("user_count") or 0),
        )
        update_admin_job_progress(
            job_id,
            status="completed",
            message=f"Wrapped {year} regenerated for {username}",
            phase="completed",
            progress=100,
            extra={"result": result},
            finished=True,
        )
    except Exception as e:
        _finalize_regeneration_audit(audit_id, status="failed", message=str(e))
        update_admin_job_progress(
            job_id,
            status="failed",
            message=f"Wrapped per-user regeneration failed: {e}",
            phase="failed",
            progress=100,
            extra={"error": str(e)},
            finished=True,
        )
    finally:
        db.close()


def queue_wrapped_regeneration(triggered_by: str | None, year: int) -> int:
    year = _safe_year(year)
    return create_admin_job(
        "wrapped_regeneration",
        triggered_by,
        f"Wrapped {year} regeneration queued",
        {"phase": "queued", "progress": 0, "year": year, "wrapped_schema_version": WRAPPED_SCHEMA_VERSION},
    )


def queue_wrapped_user_regeneration(triggered_by: str | None, year: int, username: str) -> int:
    year = _safe_year(year)
    return create_admin_job(
        "wrapped_regeneration_user",
        triggered_by,
        f"Wrapped {year} regeneration queued for {username}",
        {
            "phase": "queued",
            "progress": 0,
            "year": year,
            "username": username,
            "wrapped_schema_version": WRAPPED_SCHEMA_VERSION,
        },
    )


def run_daily_wrapped_pipeline() -> dict[str, Any]:
    ensure_wrapped_summary_db()
    today_utc = utcnow().strftime("%Y-%m-%d")
    if _get_job_state(DAILY_WRAPPED_RUN_STATE_KEY) == today_utc:
        return {"ran": False, "reason": "already_ran_today", "date_utc": today_utc}

    db = database.SessionLocal()
    try:
        year = normalize_year()
        result = regenerate_wrapped_year(db, year, force_refresh=False)
        _upsert_job_state(DAILY_WRAPPED_RUN_STATE_KEY, today_utc)
        return {"ran": True, "date_utc": today_utc, "result": result}
    finally:
        db.close()
