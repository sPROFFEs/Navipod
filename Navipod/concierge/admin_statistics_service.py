from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any

import auth
import database
import personalization_service
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

STATISTICS_CACHE_TTL_SECONDS = 30
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50
PERIODS = {"24h", "7d", "30d", "year", "all"}
SORT_FIELDS = {"username", "qualified_listens", "listening_seconds", "last_listen_at"}

_cache_lock = threading.Lock()
_period_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_period_build_locks: dict[str, threading.Lock] = {period: threading.Lock() for period in PERIODS}


def clear_statistics_cache() -> None:
    with _cache_lock:
        _period_cache.clear()


def normalize_period(value: str | None) -> str:
    period = str(value or "30d").strip().lower()
    if period not in PERIODS:
        raise ValueError("Invalid statistics period")
    return period


def normalize_sort(value: str | None) -> str:
    sort = str(value or "listening_seconds").strip().lower()
    if sort not in SORT_FIELDS:
        raise ValueError("Invalid statistics sort")
    return sort


def normalize_order(value: str | None) -> str:
    order = str(value or "desc").strip().lower()
    if order not in {"asc", "desc"}:
        raise ValueError("Invalid statistics order")
    return order


def _period_start(period: str, now: datetime) -> datetime | None:
    if period == "24h":
        return now - timedelta(hours=24)
    if period == "7d":
        return now - timedelta(days=7)
    if period == "30d":
        return now - timedelta(days=30)
    if period == "year":
        return datetime(now.year, 1, 1, tzinfo=timezone.utc)
    return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    text = str(value).strip()
    return text or None


def _safe_seconds(value: Any) -> float:
    try:
        seconds = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(seconds) or seconds < 0:
        return 0.0
    return seconds


def _read_user_activity(username: str, start: datetime | None) -> tuple[list[dict[str, Any]], str]:
    if not auth.is_valid_username(username):
        return [], "invalid_username"

    activity_path = personalization_service.get_user_activity_db_path(username, create_parent=False)
    if not activity_path.exists():
        return [], "no_activity"

    where = "" if start is None else "AND recorded_at >= ?"
    params: tuple[Any, ...] = () if start is None else (start.isoformat(),)
    try:
        with closing(sqlite3.connect(f"file:{activity_path.as_posix()}?mode=ro", uri=True, timeout=5)) as conn, conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT
                    track_id,
                    COUNT(*) AS qualified_listens,
                    SUM(played_seconds) AS listening_seconds,
                    MAX(recorded_at) AS last_listen_at
                FROM listen_events
                WHERE (
                    completed = 1
                    OR played_seconds >= 30
                    OR (
                        duration_seconds IS NOT NULL
                        AND duration_seconds > 0
                        AND played_seconds >= duration_seconds * 0.20
                    )
                )
                {where}
                GROUP BY track_id
                """,
                params,
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("Failed to read admin listening statistics for %s: %s", username, exc)
        return [], "unavailable"

    return [dict(row) for row in rows], "ok" if rows else "no_activity"


def _count_by_user(db: Session, model, user_column) -> dict[int, int]:
    return {
        int(user_id): int(count or 0)
        for user_id, count in db.query(user_column, func.count(model.id)).group_by(user_column).all()
        if user_id is not None
    }


def _track_metadata(db: Session, track_ids: set[int]) -> dict[int, dict[str, str]]:
    metadata: dict[int, dict[str, str]] = {}
    ordered = sorted(track_ids)
    for index in range(0, len(ordered), 500):
        rows = (
            db.query(database.Track.id, database.Track.title, database.Track.artist)
            .filter(database.Track.id.in_(ordered[index : index + 500]))
            .all()
        )
        for track_id, title, artist in rows:
            metadata[int(track_id)] = {
                "title": str(title or "Unknown Track"),
                "artist": str(artist or "Unknown Artist"),
            }
    return metadata


def _build_period_snapshot(db: Session, period: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    start = _period_start(period, now)
    users = (
        db.query(database.User)
        .filter(or_(database.User.is_service_account == False, database.User.is_service_account.is_(None)))
        .order_by(database.User.username.asc())
        .all()
    )
    playlist_counts = _count_by_user(db, database.Playlist, database.Playlist.owner_id)
    favorite_counts = _count_by_user(db, database.UserFavorite, database.UserFavorite.user_id)

    activity_by_user: dict[int, list[dict[str, Any]]] = {}
    status_by_user: dict[int, str] = {}
    track_ids: set[int] = set()
    for user in users:
        rows, data_status = _read_user_activity(str(user.username or ""), start)
        activity_by_user[int(user.id)] = rows
        status_by_user[int(user.id)] = data_status
        track_ids.update(int(row.get("track_id") or 0) for row in rows if int(row.get("track_id") or 0) > 0)

    metadata = _track_metadata(db, track_ids)
    items: list[dict[str, Any]] = []
    total_listens = 0
    total_seconds = 0.0

    for user in users:
        user_id = int(user.id)
        rows = activity_by_user.get(user_id, [])
        listening_seconds = sum(_safe_seconds(row.get("listening_seconds")) for row in rows)
        qualified_listens = sum(max(0, int(row.get("qualified_listens") or 0)) for row in rows)
        last_listen_at = max((str(row.get("last_listen_at") or "") for row in rows), default="") or None

        top_track = None
        if rows:
            top_row = max(
                rows,
                key=lambda row: (
                    _safe_seconds(row.get("listening_seconds")),
                    int(row.get("qualified_listens") or 0),
                    int(row.get("track_id") or 0),
                ),
            )
            top_track_id = int(top_row.get("track_id") or 0)
            if top_track_id in metadata:
                top_track = {"id": top_track_id, **metadata[top_track_id]}

        artist_totals: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"listening_seconds": 0.0, "qualified_listens": 0}
        )
        for row in rows:
            track = metadata.get(int(row.get("track_id") or 0))
            if not track:
                continue
            artist = track["artist"]
            artist_totals[artist]["listening_seconds"] += _safe_seconds(row.get("listening_seconds"))
            artist_totals[artist]["qualified_listens"] += max(0, int(row.get("qualified_listens") or 0))
        top_artist = None
        if artist_totals:
            top_artist = max(
                artist_totals,
                key=lambda artist: (
                    artist_totals[artist]["listening_seconds"],
                    artist_totals[artist]["qualified_listens"],
                    artist.lower(),
                ),
            )

        total_listens += qualified_listens
        total_seconds += listening_seconds
        items.append(
            {
                "user_id": user_id,
                "username": str(user.username),
                "is_active": bool(user.is_active),
                "is_admin": bool(user.is_admin),
                "last_access": _iso(user.last_access),
                "qualified_listens": qualified_listens,
                "listening_seconds": round(listening_seconds, 2),
                "listening_minutes": round(listening_seconds / 60.0, 2),
                "unique_tracks": len(rows),
                "last_listen_at": last_listen_at,
                "top_track": top_track,
                "top_artist": top_artist,
                "playlist_count": playlist_counts.get(user_id, 0),
                "favorite_count": favorite_counts.get(user_id, 0),
                "data_status": status_by_user.get(user_id, "no_activity"),
            }
        )

    return {
        "generated_at": now.isoformat(),
        "period": period,
        "period_start": start.isoformat() if start else None,
        "totals": {
            "users": len(items),
            "active_users": sum(1 for item in items if item["is_active"]),
            "qualified_listens": total_listens,
            "listening_seconds": round(total_seconds, 2),
            "listening_minutes": round(total_seconds / 60.0, 2),
        },
        "users": items,
    }


def get_user_statistics(
    db: Session,
    *,
    period: str = "30d",
    sort: str = "listening_seconds",
    order: str = "desc",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    period = normalize_period(period)
    sort = normalize_sort(sort)
    order = normalize_order(order)
    limit = min(max(int(limit), 1), MAX_PAGE_SIZE)
    offset = max(int(offset), 0)

    now = time.monotonic()
    with _cache_lock:
        cached = _period_cache.get(period)
        snapshot = cached[1] if cached and now - cached[0] < STATISTICS_CACHE_TTL_SECONDS else None

    if snapshot is None:
        # Only one request rebuilds a period. Other admin polls wait briefly
        # and then reuse that snapshot instead of reopening every user DB.
        with _period_build_locks[period]:
            now = time.monotonic()
            with _cache_lock:
                cached = _period_cache.get(period)
                snapshot = cached[1] if cached and now - cached[0] < STATISTICS_CACHE_TTL_SECONDS else None
            if snapshot is None:
                snapshot = _build_period_snapshot(db, period)
                with _cache_lock:
                    _period_cache[period] = (time.monotonic(), snapshot)

    users = list(snapshot["users"])
    if sort == "username":
        users.sort(key=lambda item: item["username"].lower(), reverse=order == "desc")
    elif sort == "last_listen_at":
        users.sort(key=lambda item: item["username"].lower())
        if order == "desc":
            users.sort(
                key=lambda item: (item["last_listen_at"] is not None, item["last_listen_at"] or ""),
                reverse=True,
            )
        else:
            users.sort(key=lambda item: (item["last_listen_at"] is None, item["last_listen_at"] or ""))
    else:
        users.sort(key=lambda item: item["username"].lower())
        users.sort(key=lambda item: item[sort], reverse=order == "desc")

    total = len(users)
    return {
        **{key: value for key, value in snapshot.items() if key != "users"},
        "users": users[offset : offset + limit],
        "pagination": {"limit": limit, "offset": offset, "total": total},
        "sort": {"field": sort, "order": order},
    }
