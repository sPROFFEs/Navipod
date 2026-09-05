import asyncio
import base64
import json
import sqlite3

import app
import pytest


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/playlist?list=test", "youtube"),
        ("https://music.youtube.com/playlist?list=test", "youtube"),
        ("https://youtu.be/abcdefghijk", "youtube"),
        ("https://open.spotify.com/playlist/abc123", "spotify"),
    ],
)
def test_detect_source(url, expected):
    assert app.detect_source(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/playlist/abc",
        "https://evil.youtube.com/playlist?list=test",
        "ftp://www.youtube.com/playlist?list=test",
        "javascript://open.spotify.com/playlist/abc",
        "not-a-url",
    ],
)
def test_detect_source_rejects_unapproved_hosts(url):
    with pytest.raises(ValueError):
        app.detect_source(url)


def test_fernet_round_trip():
    value = "header.payload.signature"
    assert app.decode_token(app.encode_token(value)) == value


def test_unverified_subject_decoder_is_not_a_signature_validator():
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "reviewer"}).encode()).decode().rstrip("=")
    assert app.jwt_subject_unverified(f"x.{payload}.x") == "reviewer"
    assert app.jwt_subject_unverified("invalid") is None


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://navipod.example.test", True),
        ("https://navipod.example.test/portal", True),
        ("http://navipod.example.test", False),
        ("https://attacker.example", False),
        ("null", False),
        ("", False),
    ],
)
def test_same_origin(source, expected):
    assert app.is_same_origin(source) is expected


def test_database_schema_and_cascade(tmp_path, monkeypatch):
    db_path = tmp_path / "importer.db"
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "DB_PATH", db_path)
    app.init_db()

    timestamp = app.now()
    with app.connect_db() as db:
        db.execute(
            """
            INSERT INTO imports(
                id, owner, source_url, source_type, destination,
                status, token_enc, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "import-1",
                "reviewer",
                "https://www.youtube.com/playlist?list=test",
                "youtube",
                "liked",
                "paused",
                app.encode_token("token"),
                timestamp,
                timestamp,
            ),
        )
        db.execute(
            """
            INSERT INTO tracks(import_id, position, status)
            VALUES('import-1', 1, 'pending')
            """
        )
        db.execute("DELETE FROM imports WHERE id='import-1'")
        remaining = db.execute("SELECT COUNT(*) FROM tracks WHERE import_id='import-1'").fetchone()[0]

    assert remaining == 0
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_health_payload():
    assert asyncio.run(app.health()) == {"ok": True}
