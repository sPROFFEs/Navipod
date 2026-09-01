import sqlite3

import personalization_service
import wrapped_service


def _assert_closed(connections):
    assert connections
    for connection in connections:
        try:
            connection.execute("SELECT 1")
        except sqlite3.ProgrammingError as exc:
            assert "closed" in str(exc).lower()
        else:  # pragma: no cover - makes a leaked descriptor fail clearly
            raise AssertionError("SQLite connection was not closed")


def test_personalization_operations_close_every_sqlite_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(
        personalization_service,
        "get_user_activity_db_path",
        lambda _username, create_parent=True: tmp_path / "activity.db",
    )
    monkeypatch.setattr(
        personalization_service,
        "get_legacy_recent_cache_path",
        lambda _username: tmp_path / "missing-recent-cache.json",
    )
    original_connect = personalization_service._connect
    connections = []

    def tracked_connect(username):
        connection = original_connect(username)
        connections.append(connection)
        return connection

    monkeypatch.setattr(personalization_service, "_connect", tracked_connect)

    personalization_service.record_recent_playlist("listener", 42)

    _assert_closed(connections)


def test_wrapped_reads_close_summary_connections(tmp_path, monkeypatch):
    monkeypatch.setattr(wrapped_service, "get_wrapped_summary_db_path", lambda: tmp_path / "wrapped.db")
    original_connect = wrapped_service._connect_summary
    connections = []

    def tracked_connect():
        connection = original_connect()
        connections.append(connection)
        return connection

    monkeypatch.setattr(wrapped_service, "_connect_summary", tracked_connect)

    assert wrapped_service._cached_user_meta(1, 2026) is None

    _assert_closed(connections)
