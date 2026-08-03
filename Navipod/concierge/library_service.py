"""Library facets and smart-playlist rule evaluation."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import database
import personalization_service
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

FACET_COLUMNS = {
    "artists": database.Track.artist,
    "albums": database.Track.album,
    "genres": database.Track.genre,
}
SMART_SORTS = {"newest", "oldest", "artist", "album", "title", "most_played", "least_played"}


def describe_smart_rules(raw_rules: dict) -> str:
    rules = normalize_smart_rules(raw_rules)
    parts = []
    for key, label in (("artist", "Artist"), ("album", "Album"), ("genre", "Genre")):
        if rules[key]:
            parts.append(f"{label}: {rules[key]}")
    if rules["year_min"] is not None or rules["year_max"] is not None:
        parts.append(f"Years: {rules['year_min'] or 'any'}–{rules['year_max'] or 'any'}")
    if rules["favorite_only"]:
        parts.append("Favorites")
    if rules["added_within_days"] is not None:
        parts.append(f"Added in {rules['added_within_days']}d")
    if rules["min_plays"] is not None:
        parts.append(f"At least {rules['min_plays']} plays")
    if rules["not_played_days"] is not None:
        parts.append(f"Not played in {rules['not_played_days']}d")
    parts.append(f"Up to {rules['limit']} · {rules['sort'].replace('_', ' ')}")
    return " · ".join(parts)


def serialize_track(track: database.Track) -> dict:
    return {
        "id": track.id,
        "db_id": track.id,
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "genre": track.genre or "",
        "year": track.year,
        "duration": track.duration,
        "thumbnail": f"/api/cover/{track.id}",
    }


def list_facets(
    db: Session, kind: str, query: str = "", limit: int = 100, offset: int = 0, sort: str = "name"
) -> list[dict]:
    column = FACET_COLUMNS.get(kind)
    if column is None:
        raise ValueError("kind must be artists, albums, or genres")
    fields = [column.label("name"), func.count(database.Track.id).label("track_count")]
    if kind == "albums":
        fields.extend([database.Track.artist.label("artist"), func.min(database.Track.id).label("cover_track_id")])
    statement = db.query(*fields)
    statement = statement.filter(column.isnot(None), func.trim(column) != "")
    if query.strip():
        statement = statement.filter(column.ilike(f"%{query.strip()}%"))
    grouping = [column, database.Track.artist] if kind == "albums" else [column]
    statement = statement.group_by(*grouping)
    statement = statement.order_by(
        func.count(database.Track.id).desc() if sort == "count" else func.lower(column),
        func.lower(database.Track.artist) if kind == "albums" else column,
    )
    rows = statement.offset(max(0, offset)).limit(max(1, min(limit, 500))).all()
    result = []
    for row in rows:
        item = {"name": row.name, "track_count": int(row.track_count)}
        if kind == "albums":
            item.update(
                {
                    # Preserve the real filter value. The UI may render an
                    # empty artist as unknown, but must not query that label.
                    "artist": row.artist or "",
                    "thumbnail": f"/api/cover/{row.cover_track_id}",
                }
            )
        result.append(item)
    return result


def count_facets(db: Session, kind: str, query: str = "") -> int:
    column = FACET_COLUMNS.get(kind)
    if column is None:
        raise ValueError("kind must be artists, albums, or genres")
    statement = db.query(column)
    statement = statement.filter(column.isnot(None), func.trim(column) != "")
    if query.strip():
        statement = statement.filter(column.ilike(f"%{query.strip()}%"))
    if kind == "albums":
        return statement.group_by(column, database.Track.artist).count()
    return statement.distinct().count()


def list_tracks(
    db: Session,
    *,
    artist: str = "",
    album: str = "",
    genre: str = "",
    year: int | None = None,
    query: str = "",
    limit: int = 200,
    offset: int = 0,
    sort: str = "artist",
) -> list[dict]:
    statement = _track_query(db, artist=artist, album=album, genre=genre, year=year, query=query)
    orders = {
        "title": (database.Track.title, database.Track.artist),
        "album": (database.Track.album, database.Track.title),
        "year_desc": (database.Track.year.desc(), database.Track.title),
        "newest": (database.Track.created_at.desc(), database.Track.id.desc()),
        "artist": (database.Track.artist, database.Track.album, database.Track.title),
    }
    rows = statement.order_by(*orders.get(sort, orders["artist"])).offset(max(0, offset)).limit(max(1, min(limit, 500)))
    return [serialize_track(track) for track in rows]


def count_tracks(db: Session, **filters) -> int:
    return _track_query(db, **filters).count()


def _track_query(
    db: Session, *, artist: str = "", album: str = "", genre: str = "", year: int | None = None, query: str = ""
):
    statement = db.query(database.Track)
    if artist:
        statement = statement.filter(database.Track.artist == artist)
    if album:
        statement = statement.filter(database.Track.album == album)
    if genre:
        statement = statement.filter(database.Track.genre == genre)
    if year is not None:
        statement = statement.filter(database.Track.year == year)
    if query.strip():
        needle = f"%{query.strip()}%"
        statement = statement.filter(
            or_(
                database.Track.title.ilike(needle),
                database.Track.artist.ilike(needle),
                database.Track.album.ilike(needle),
            )
        )
    return statement


def normalize_smart_rules(rules: dict) -> dict:
    clean = {
        "artist": str(rules.get("artist") or "").strip(),
        "album": str(rules.get("album") or "").strip(),
        "genre": str(rules.get("genre") or "").strip(),
        "year_min": rules.get("year_min"),
        "year_max": rules.get("year_max"),
        "favorite_only": bool(rules.get("favorite_only", False)),
        "added_within_days": rules.get("added_within_days"),
        "min_plays": rules.get("min_plays"),
        "not_played_days": rules.get("not_played_days"),
        "limit": max(1, min(int(rules.get("limit") or 50), 500)),
        "sort": str(rules.get("sort") or "newest"),
    }
    for key in ("year_min", "year_max", "added_within_days", "min_plays", "not_played_days"):
        if clean[key] is not None:
            clean[key] = max(0, int(clean[key]))
    if clean["year_min"] is not None and clean["year_max"] is not None and clean["year_min"] > clean["year_max"]:
        raise ValueError("year_min cannot be greater than year_max")
    if clean["sort"] not in SMART_SORTS:
        raise ValueError(f"sort must be one of: {', '.join(sorted(SMART_SORTS))}")
    return clean


def _activity_stats(username: str) -> dict[int, dict]:
    try:
        path = personalization_service.ensure_user_activity_db(username)
        with sqlite3.connect(str(path)) as conn:
            rows = conn.execute("SELECT track_id, play_count, last_played_at FROM track_stats").fetchall()
    except (OSError, sqlite3.Error):
        return {}
    return {
        int(track_id): {"play_count": int(play_count or 0), "last_played_at": last_played_at}
        for track_id, play_count, last_played_at in rows
    }


def smart_track_ids(db: Session, user: database.User, raw_rules: dict) -> list[int]:
    rules = normalize_smart_rules(raw_rules)
    statement = db.query(database.Track)
    if rules["artist"]:
        statement = statement.filter(database.Track.artist == rules["artist"])
    if rules["album"]:
        statement = statement.filter(database.Track.album == rules["album"])
    if rules["genre"]:
        statement = statement.filter(database.Track.genre == rules["genre"])
    if rules["year_min"] is not None:
        statement = statement.filter(database.Track.year >= rules["year_min"])
    if rules["year_max"] is not None:
        statement = statement.filter(database.Track.year <= rules["year_max"])
    if rules["added_within_days"] is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=rules["added_within_days"])
        statement = statement.filter(database.Track.created_at >= cutoff)
    if rules["favorite_only"]:
        statement = statement.join(database.UserFavorite).filter(database.UserFavorite.user_id == user.id)

    tracks = statement.all()
    stats = _activity_stats(user.username)
    now = datetime.now(timezone.utc)

    def created_timestamp(track) -> float:
        value = track.created_at
        if value is None:
            return 0.0
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()

    def keep(track) -> bool:
        activity = stats.get(track.id, {})
        if rules["min_plays"] is not None and activity.get("play_count", 0) < rules["min_plays"]:
            return False
        if rules["not_played_days"] is not None:
            last = activity.get("last_played_at")
            if last:
                try:
                    parsed = datetime.fromisoformat(last)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    if now - parsed < timedelta(days=rules["not_played_days"]):
                        return False
                except ValueError:
                    pass
        return True

    tracks = [track for track in tracks if keep(track)]
    sort = rules["sort"]
    if sort in {"most_played", "least_played"}:
        reverse = sort == "most_played"
        tracks.sort(key=lambda track: (stats.get(track.id, {}).get("play_count", 0), track.id), reverse=reverse)
    elif sort == "oldest":
        tracks.sort(key=lambda track: (created_timestamp(track), track.id))
    elif sort == "artist":
        tracks.sort(key=lambda track: ((track.artist or "").casefold(), (track.title or "").casefold()))
    elif sort == "album":
        tracks.sort(key=lambda track: ((track.album or "").casefold(), (track.title or "").casefold()))
    elif sort == "title":
        tracks.sort(key=lambda track: (track.title or "").casefold())
    else:
        tracks.sort(key=lambda track: (created_timestamp(track), track.id), reverse=True)
    return [track.id for track in tracks[: rules["limit"]]]
