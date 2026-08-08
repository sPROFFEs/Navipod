import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import admin_statistics_service
import database
import pytest


def _activity_db(path, events):
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE listen_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER NOT NULL,
                played_seconds REAL NOT NULL DEFAULT 0,
                duration_seconds REAL,
                completed INTEGER NOT NULL DEFAULT 0,
                skipped_early INTEGER NOT NULL DEFAULT 0,
                context_type TEXT,
                context_key TEXT,
                recorded_at TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO listen_events (
                track_id, played_seconds, duration_seconds, completed, skipped_early, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            events,
        )
        conn.commit()


@pytest.fixture(autouse=True)
def _clear_statistics_cache():
    admin_statistics_service.clear_statistics_cache()
    yield
    admin_statistics_service.clear_statistics_cache()


def test_user_statistics_aggregate_live_activity_without_wrapped_regeneration(db_session, tmp_path, monkeypatch):
    alice = database.User(username="alice", hashed_password="unused", is_active=True)
    bob = database.User(username="bob", hashed_password="unused", is_active=False)
    service = database.User(
        username="remote-peer",
        hashed_password="unused",
        is_active=True,
        is_service_account=True,
    )
    first = database.Track(title="First", artist="Artist A", filepath="/music/first.mp3")
    second = database.Track(title="Second", artist="Artist B", filepath="/music/second.mp3")
    db_session.add_all([alice, bob, service, first, second])
    db_session.commit()
    db_session.add_all(
        [
            database.Playlist(name="Alice list", owner_id=alice.id),
            database.UserFavorite(user_id=alice.id, track_id=first.id),
        ]
    )
    db_session.commit()

    now = datetime.now(timezone.utc)
    activity_paths = {name: tmp_path / f"{name}.db" for name in ("alice", "bob", "remote-peer")}
    monkeypatch.setattr(
        admin_statistics_service.personalization_service,
        "get_user_activity_db_path",
        lambda username, **_kwargs: activity_paths[username],
    )
    _activity_db(
        activity_paths["alice"],
        [
            (first.id, 60, 200, 0, 0, (now - timedelta(hours=2)).isoformat()),
            (first.id, 10, 40, 0, 0, (now - timedelta(hours=1)).isoformat()),
            (second.id, 20, 200, 0, 1, (now - timedelta(minutes=30)).isoformat()),
            (second.id, 120, 200, 1, 0, (now - timedelta(minutes=10)).isoformat()),
            (first.id, 90, 200, 1, 0, (now - timedelta(days=40)).isoformat()),
        ],
    )
    _activity_db(
        activity_paths["remote-peer"],
        [(first.id, 300, 300, 1, 0, now.isoformat())],
    )

    result = admin_statistics_service.get_user_statistics(db_session, period="30d")

    assert result["totals"] == {
        "users": 2,
        "active_users": 1,
        "qualified_listens": 3,
        "listening_seconds": 190.0,
        "listening_minutes": 3.17,
    }
    assert [user["username"] for user in result["users"]] == ["alice", "bob"]
    alice_stats = result["users"][0]
    assert alice_stats["unique_tracks"] == 2
    assert alice_stats["top_track"] == {"id": second.id, "title": "Second", "artist": "Artist B"}
    assert alice_stats["top_artist"] == "Artist B"
    assert alice_stats["playlist_count"] == 1
    assert alice_stats["favorite_count"] == 1
    assert alice_stats["data_status"] == "ok"
    assert result["users"][1]["data_status"] == "no_activity"

    all_time = admin_statistics_service.get_user_statistics(db_session, period="all")
    assert all_time["totals"]["qualified_listens"] == 4
    assert all_time["totals"]["listening_seconds"] == 280.0


def test_user_statistics_isolate_corrupt_activity_database(db_session, tmp_path, monkeypatch):
    user = database.User(username="alice", hashed_password="unused", is_active=True)
    db_session.add(user)
    db_session.commit()
    corrupt_path = tmp_path / "alice.db"
    corrupt_path.write_text("not a sqlite database", encoding="utf-8")
    monkeypatch.setattr(
        admin_statistics_service.personalization_service,
        "get_user_activity_db_path",
        lambda _username, **_kwargs: corrupt_path,
    )

    result = admin_statistics_service.get_user_statistics(db_session)

    assert result["users"][0]["data_status"] == "unavailable"
    assert result["users"][0]["qualified_listens"] == 0


def test_user_statistics_endpoint_requires_admin_dependency_and_disables_caching():
    router_source = (Path(__file__).resolve().parents[1] / "routers" / "admin.py").read_text(encoding="utf-8")
    route_start = router_source.index('@router.get("/api/user-statistics")')
    route_end = router_source.index("\n\n@router.", route_start)
    route_source = router_source[route_start:route_end]

    assert "admin: database.User = Depends(get_current_admin)" in route_source
    assert 'headers={"Cache-Control": "private, no-store"}' in route_source


@pytest.mark.parametrize("field,value", [("period", "century"), ("sort", "password"), ("order", "sideways")])
def test_user_statistics_reject_invalid_query_values(db_session, field, value):
    kwargs = {field: value}
    with pytest.raises(ValueError):
        admin_statistics_service.get_user_statistics(db_session, **kwargs)
