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
WRAPPED_SUMMARY_VERSION = 1
WRAPPED_TOP_TRACK_LIMIT = 100
WRAPPED_TOP_DISPLAY_LIMIT = 5
WRAPPED_MAX_REASONABLE_SECONDS = 365 * 24 * 60 * 60
DEFAULT_ARTIST_CLIP_MESSAGE = "Your year had range. The admin can make this message worse later."


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
            CREATE TABLE IF NOT EXISTS wrapped_user_summaries (
                year INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (year, user_id)
            );

            CREATE TABLE IF NOT EXISTS wrapped_party_summaries (
                year INTEGER PRIMARY KEY,
                generated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS ix_wrapped_user_summaries_username_year
                ON wrapped_user_summaries(username, year);
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


def _fetch_activity_rows(username: str, year: int) -> list[sqlite3.Row]:
    activity_path = personalization_service.get_user_activity_db_path(username)
    if not activity_path.exists():
        return []

    start, end = _year_bounds(year)
    try:
        with sqlite3.connect(str(activity_path)) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                """
                SELECT
                    track_id,
                    played_seconds,
                    duration_seconds,
                    completed,
                    skipped_early,
                    context_type,
                    context_key,
                    recorded_at
                FROM listen_events
                WHERE recorded_at >= ? AND recorded_at < ?
                ORDER BY recorded_at ASC, id ASC
                """,
                (start, end),
            ).fetchall()
    except sqlite3.Error as e:
        logger.warning("Failed to read wrapped activity for %s/%s: %s", username, year, e)
        return []


def _track_lookup(db: Session, track_ids: set[int]) -> dict[int, database.Track]:
    if not track_ids:
        return {}
    tracks = db.query(database.Track).filter(database.Track.id.in_(sorted(track_ids))).all()
    return {int(track.id): track for track in tracks}


def _safe_played_seconds(value: Any) -> float:
    try:
        seconds = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not isfinite(seconds) or seconds < 0:
        return 0.0
    return min(seconds, WRAPPED_MAX_REASONABLE_SECONDS)


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


def build_user_wrapped_summary(db: Session, user: database.User, year: int | None = None) -> dict[str, Any]:
    year = _safe_year(year)
    rows = _fetch_activity_rows(user.username, year)
    track_ids = {int(row["track_id"]) for row in rows if int(row["track_id"] or 0) > 0}
    tracks_by_id = _track_lookup(db, track_ids)

    total_played_seconds = 0.0
    track_stats: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"stream_count": 0, "completion_count": 0, "skip_count": 0, "played_seconds": 0.0}
    )
    artist_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"stream_count": 0, "completion_count": 0, "skip_count": 0, "played_seconds": 0.0}
    )
    monthly_artist_stats: dict[int, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(lambda: {"stream_count": 0, "played_seconds": 0.0})
    )

    for row in rows:
        track_id = int(row["track_id"] or 0)
        track = tracks_by_id.get(track_id)
        if not track:
            continue

        played = _safe_played_seconds(row["played_seconds"])
        completed = int(row["completed"] or 0)
        skipped = int(row["skipped_early"] or 0)
        total_played_seconds += played

        tstats = track_stats[track_id]
        tstats["stream_count"] += 1
        tstats["completion_count"] += completed
        tstats["skip_count"] += skipped
        tstats["played_seconds"] += played

        artist = _artist_key(track)
        astats = artist_stats[artist]
        astats["stream_count"] += 1
        astats["completion_count"] += completed
        astats["skip_count"] += skipped
        astats["played_seconds"] += played

        try:
            month = datetime.fromisoformat(str(row["recorded_at"])).month
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

    return {
        "version": WRAPPED_SUMMARY_VERSION,
        "year": year,
        "generated_at": _iso_now(),
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


def save_user_wrapped_summary(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_wrapped_summary_db()
    user = payload["user"]
    with _connect_summary() as conn:
        conn.execute(
            """
            INSERT INTO wrapped_user_summaries (year, user_id, username, generated_at, payload_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(year, user_id) DO UPDATE SET
                username = excluded.username,
                generated_at = excluded.generated_at,
                payload_json = excluded.payload_json
            """,
            (
                int(payload["year"]),
                int(user["id"]),
                str(user["username"]),
                str(payload["generated_at"]),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        conn.commit()
    return payload


def get_cached_user_wrapped_summary(user_id: int, year: int) -> dict[str, Any] | None:
    ensure_wrapped_summary_db()
    with _connect_summary() as conn:
        row = conn.execute(
            "SELECT payload_json FROM wrapped_user_summaries WHERE year = ? AND user_id = ?",
            (int(year), int(user_id)),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["payload_json"])
    except Exception:
        return None


def get_or_build_user_wrapped_summary(
    db: Session, user: database.User, year: int | None = None, *, force_refresh: bool = False
) -> dict[str, Any]:
    year = _safe_year(year)
    if not force_refresh:
        cached = get_cached_user_wrapped_summary(int(user.id), year)
        if cached:
            return cached
    return save_user_wrapped_summary(build_user_wrapped_summary(db, user, year))


def build_party_summary(db: Session, year: int | None = None) -> dict[str, Any]:
    year = _safe_year(year)
    users = db.query(database.User).filter(database.User.is_active == True).order_by(database.User.username.asc()).all()
    summaries = [get_or_build_user_wrapped_summary(db, user, year) for user in users]
    active_summaries = [
        summary
        for summary in summaries
        if _safe_played_seconds(summary.get("played_seconds")) > 0 and int(summary.get("event_count") or 0) > 0
    ]

    minutes_ranking = sorted(
        active_summaries,
        key=lambda item: (_safe_played_seconds(item.get("played_seconds")), int(item.get("event_count") or 0)),
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
        "year": year,
        "generated_at": _iso_now(),
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
            INSERT INTO wrapped_party_summaries (year, generated_at, payload_json)
            VALUES (?, ?, ?)
            ON CONFLICT(year) DO UPDATE SET
                generated_at = excluded.generated_at,
                payload_json = excluded.payload_json
            """,
            (
                int(payload["year"]),
                str(payload["generated_at"]),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        conn.commit()
    return payload


def get_cached_party_summary(year: int) -> dict[str, Any] | None:
    ensure_wrapped_summary_db()
    with _connect_summary() as conn:
        row = conn.execute("SELECT payload_json FROM wrapped_party_summaries WHERE year = ?", (int(year),)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["payload_json"])
    except Exception:
        return None


def get_or_build_party_summary(db: Session, year: int | None = None, *, force_refresh: bool = False) -> dict[str, Any]:
    year = _safe_year(year)
    if not force_refresh:
        cached = get_cached_party_summary(year)
        if cached:
            return cached
    return build_party_summary(db, year)


def regenerate_wrapped_year(db: Session, year: int | None = None) -> dict[str, Any]:
    year = _safe_year(year)
    ensure_wrapped_summary_db()
    users = db.query(database.User).filter(database.User.is_active == True).order_by(database.User.username.asc()).all()
    generated = []
    for user in users:
        generated.append(save_user_wrapped_summary(build_user_wrapped_summary(db, user, year)))
    party = build_party_summary(db, year)
    return {
        "year": year,
        "user_count": len(generated),
        "party_user_count": int(party.get("user_count") or 0),
        "summary_db": str(get_wrapped_summary_db_path()),
    }


def run_wrapped_regeneration_job(job_id: int, year: int) -> None:
    db = database.SessionLocal()
    try:
        update_admin_job_progress(
            job_id,
            status="running",
            message=f"Regenerating Wrapped {year}",
            phase="wrapped",
            progress=10,
        )
        result = regenerate_wrapped_year(db, year)
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


def queue_wrapped_regeneration(triggered_by: str | None, year: int) -> int:
    year = _safe_year(year)
    return create_admin_job(
        "wrapped_regeneration",
        triggered_by,
        f"Wrapped {year} regeneration queued",
        {"phase": "queued", "progress": 0, "year": year},
    )
