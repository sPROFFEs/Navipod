import ops_core
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def legacy_party_connection():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE tracks (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO users(id) VALUES (1), (2)"))
        conn.execute(text("INSERT INTO tracks(id) VALUES (10)"))
        conn.execute(
            text("""
            CREATE TABLE party_rooms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL UNIQUE,
                name TEXT NOT NULL,
                max_users INTEGER NOT NULL DEFAULT 5,
                allow_guests_queue INTEGER NOT NULL DEFAULT 1,
                playback_status TEXT NOT NULL DEFAULT 'paused',
                current_index INTEGER NOT NULL DEFAULT -1,
                playback_position_ms INTEGER NOT NULL DEFAULT 0,
                playback_started_at DATETIME,
                revision INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE,
                CHECK (max_users >= 2 AND max_users <= 15),
                CHECK (playback_status IN ('paused', 'playing'))
            )
            """)
        )
        conn.execute(
            text("""
            CREATE TABLE party_room_queue_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER NOT NULL,
                track_id INTEGER NOT NULL,
                added_by_user_id INTEGER,
                position INTEGER NOT NULL DEFAULT 0,
                added_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (room_id) REFERENCES party_rooms(id) ON DELETE CASCADE,
                FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE,
                FOREIGN KEY (added_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            )
            """)
        )
        conn.execute(
            text("""
            INSERT INTO party_rooms (
                id, owner_id, name, max_users, allow_guests_queue,
                playback_status, current_index, playback_position_ms, revision
            ) VALUES (7, 1, 'Existing room', 9, 0, 'paused', 0, 1234, 4)
            """)
        )
        conn.execute(
            text("""
            INSERT INTO party_room_queue_items (
                id, room_id, track_id, added_by_user_id, position
            ) VALUES (11, 7, 10, 2, 0)
            """)
        )
    try:
        yield engine
    finally:
        engine.dispose()


def test_party_loading_status_migration_preserves_existing_rooms_and_queue(legacy_party_connection):
    with legacy_party_connection.begin() as conn:
        ops_core._migration_025_party_room_loading_status(conn)
        conn.execute(text("UPDATE party_rooms SET playback_status = 'loading' WHERE id = 7"))

        room = conn.execute(
            text("""
            SELECT owner_id, name, max_users, allow_guests_queue,
                   playback_status, current_index, playback_position_ms, revision
            FROM party_rooms WHERE id = 7
            """)
        ).one()
        queue_item = conn.execute(
            text("SELECT room_id, track_id, added_by_user_id, position FROM party_room_queue_items WHERE id = 11")
        ).one()
        foreign_key_errors = conn.execute(text("PRAGMA foreign_key_check")).all()

    assert room == (1, "Existing room", 9, 0, "loading", 0, 1234, 4)
    assert queue_item == (7, 10, 2, 0)
    assert foreign_key_errors == []


def test_party_room_schema_still_rejects_unknown_playback_status(legacy_party_connection):
    with legacy_party_connection.begin() as conn:
        ops_core._migration_025_party_room_loading_status(conn)
        with pytest.raises(IntegrityError):
            conn.execute(text("UPDATE party_rooms SET playback_status = 'buffering' WHERE id = 7"))


def test_new_party_room_schema_accepts_loading_status():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE tracks (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO users(id) VALUES (1)"))
        ops_core._migration_024_party_rooms(conn)
        conn.execute(text("INSERT INTO party_rooms(owner_id, name, playback_status) VALUES (1, 'New room', 'loading')"))

    assert ops_core.MIGRATIONS[-1][0] == "029_delete_reference_cleanup"
    engine.dispose()


def test_track_loudness_columns_migration():
    """Migration 026 adds gain_db, peak, loudness_measured_at to tracks."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tracks (id INTEGER PRIMARY KEY, title TEXT)"))
        ops_core._migration_026_track_loudness_columns(conn)
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(tracks)")).fetchall()}
    assert "gain_db" in cols
    assert "peak" in cols
    assert "loudness_measured_at" in cols
    # Idempotent: running twice should not error.
    with engine.begin() as conn:
        ops_core._migration_026_track_loudness_columns(conn)
    engine.dispose()


def test_downloader_mode_migration_preserves_existing_system_settings():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE system_settings (id INTEGER PRIMARY KEY, pool_limit_gb INTEGER)"))
        conn.execute(text("INSERT INTO system_settings(id, pool_limit_gb) VALUES (1, 250)"))
        ops_core._migration_028_downloader_mode(conn)
        row = conn.execute(text("SELECT pool_limit_gb, downloader_mode FROM system_settings WHERE id = 1")).one()
        ops_core._migration_028_downloader_mode(conn)

    assert row == (250, "automatic")
    engine.dispose()


def test_delete_reference_cleanup_migration_handles_legacy_foreign_keys():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE tracks (id INTEGER PRIMARY KEY)"))
        conn.execute(
            text("""
            CREATE TABLE playlists (
                id INTEGER PRIMARY KEY,
                cover_track_id INTEGER,
                FOREIGN KEY (cover_track_id) REFERENCES tracks(id)
            )
            """)
        )
        conn.execute(
            text("""
            CREATE TABLE playlist_items (
                id INTEGER PRIMARY KEY,
                playlist_id INTEGER NOT NULL,
                track_id INTEGER NOT NULL,
                FOREIGN KEY (playlist_id) REFERENCES playlists(id),
                FOREIGN KEY (track_id) REFERENCES tracks(id)
            )
            """)
        )
        conn.execute(
            text("""
            CREATE TABLE download_jobs (
                id INTEGER PRIMARY KEY,
                target_modern_playlist_id INTEGER,
                resolved_track_id INTEGER,
                FOREIGN KEY (target_modern_playlist_id) REFERENCES playlists(id),
                FOREIGN KEY (resolved_track_id) REFERENCES tracks(id)
            )
            """)
        )
        conn.execute(
            text("""
            CREATE TABLE user_favorites (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                track_id INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (track_id) REFERENCES tracks(id)
            )
            """)
        )
        conn.execute(
            text("""
            CREATE TABLE track_delete_requests (
                id INTEGER PRIMARY KEY,
                track_id INTEGER,
                FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE SET NULL
            )
            """)
        )
        conn.execute(
            text("""
            CREATE TABLE party_room_queue_items (
                id INTEGER PRIMARY KEY,
                track_id INTEGER NOT NULL,
                FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
            )
            """)
        )

        conn.execute(text("INSERT INTO users(id) VALUES (1)"))
        conn.execute(text("INSERT INTO tracks(id) VALUES (10)"))
        conn.execute(text("INSERT INTO playlists(id, cover_track_id) VALUES (20, 10), (21, 10)"))
        conn.execute(text("INSERT INTO playlist_items(id, playlist_id, track_id) VALUES (1, 20, 10), (2, 21, 10)"))
        conn.execute(
            text("INSERT INTO download_jobs(id, target_modern_playlist_id, resolved_track_id) VALUES (30, 20, 10)")
        )
        conn.execute(text("INSERT INTO user_favorites(id, user_id, track_id) VALUES (40, 1, 10)"))
        conn.execute(text("INSERT INTO track_delete_requests(id, track_id) VALUES (50, 10)"))
        conn.execute(text("INSERT INTO party_room_queue_items(id, track_id) VALUES (60, 10)"))

        ops_core._migration_029_delete_reference_cleanup(conn)
        ops_core._migration_029_delete_reference_cleanup(conn)

        conn.execute(text("DELETE FROM playlists WHERE id = 20"))
        assert conn.execute(text("SELECT target_modern_playlist_id FROM download_jobs WHERE id = 30")).scalar() is None
        assert conn.execute(text("SELECT COUNT(*) FROM playlist_items WHERE playlist_id = 20")).scalar() == 0

        conn.execute(text("DELETE FROM tracks WHERE id = 10"))
        assert conn.execute(text("SELECT COUNT(*) FROM user_favorites")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM playlist_items")).scalar() == 0
        assert conn.execute(text("SELECT cover_track_id FROM playlists WHERE id = 21")).scalar() is None
        assert conn.execute(text("SELECT resolved_track_id FROM download_jobs WHERE id = 30")).scalar() is None
        assert conn.execute(text("SELECT track_id FROM track_delete_requests WHERE id = 50")).scalar() is None
        assert conn.execute(text("SELECT COUNT(*) FROM party_room_queue_items")).scalar() == 0
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []

    engine.dispose()


def test_fresh_schema_uses_native_delete_actions():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        for _name, migration in ops_core.MIGRATIONS:
            migration(conn)

        actions = {}
        for table_name in (
            "playlist_items",
            "playlists",
            "download_jobs",
            "user_favorites",
            "track_delete_requests",
            "party_room_queue_items",
        ):
            for row in conn.execute(text(f"PRAGMA foreign_key_list({table_name})")):
                actions[(table_name, row[3])] = row[6]

        expected = {
            ("playlist_items", "playlist_id"): "CASCADE",
            ("playlist_items", "track_id"): "CASCADE",
            ("playlists", "cover_track_id"): "SET NULL",
            ("download_jobs", "target_modern_playlist_id"): "SET NULL",
            ("download_jobs", "resolved_track_id"): "SET NULL",
            ("user_favorites", "track_id"): "CASCADE",
            ("track_delete_requests", "track_id"): "SET NULL",
            ("party_room_queue_items", "track_id"): "CASCADE",
        }

        assert {key: actions.get(key) for key in expected} == expected
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []

    engine.dispose()
