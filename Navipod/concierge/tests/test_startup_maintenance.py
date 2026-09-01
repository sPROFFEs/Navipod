from pathlib import Path

import database
import track_identity
import yaml
from sqlalchemy.pool import NullPool

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_sqlite_does_not_cap_concurrent_streaming_requests():
    assert isinstance(database.engine.pool, NullPool)


def test_identity_sync_only_reads_tracks_missing_identity_fields(db_session, monkeypatch):
    complete = database.Track(
        title="Complete",
        artist="Artist",
        filepath="/music/complete.mp3",
        artist_norm="artist",
        title_norm="complete",
        version_tag="original",
        fingerprint="artist::complete::original",
    )
    incomplete = database.Track(title="Missing", artist="Artist", filepath="/music/missing.mp3")
    db_session.add_all([complete, incomplete])
    db_session.commit()

    calls = []
    original = track_identity.compute_track_identity

    def recording_identity(artist, title):
        calls.append((artist, title))
        return original(artist, title)

    monkeypatch.setattr(track_identity, "compute_track_identity", recording_identity)

    assert track_identity.sync_track_identities(db_session) == 1
    assert calls == [("Artist", "Missing")]


def test_concierge_permission_repair_is_guarded_by_a_migration_marker():
    entrypoint = (PROJECT_ROOT / "concierge" / "entrypoint.sh").read_text(encoding="utf-8")

    assert 'PERMISSION_MARKER="/saas-data/.permissions-v2"' in entrypoint
    assert 'if [ ! -f "$PERMISSION_MARKER" ]; then' in entrypoint
    assert 'touch "$PERMISSION_MARKER"' in entrypoint


def test_runtime_tracing_and_startup_loudness_are_opt_in_in_all_compose_variants():
    compose_paths = [
        PROJECT_ROOT / "docker-compose.yaml",
        PROJECT_ROOT / "deployment-templates" / "internal" / "docker-compose.yaml",
        PROJECT_ROOT / "deployment-templates" / "domain" / "docker-compose.yaml",
    ]

    for compose_path in compose_paths:
        payload = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        environment = payload["services"]["concierge"]["environment"]
        assert "PYTHONTRACEMALLOC=${PYTHONTRACEMALLOC:-0}" in environment
        assert "STARTUP_LOUDNESS_BACKFILL=${STARTUP_LOUDNESS_BACKFILL:-false}" in environment


def test_short_remote_requests_share_connection_pool_and_shutdown_closes_provider_clients():
    concierge_root = PROJECT_ROOT / "concierge"
    request_sources = [
        concierge_root / "lyrics_service.py",
        concierge_root / "routers" / "music" / "favorites.py",
        concierge_root / "routers" / "music" / "playlists.py",
    ]

    for path in request_sources:
        source = path.read_text(encoding="utf-8")
        assert "from http_client import http_client" in source
        assert "httpx.AsyncClient" not in source

    main_source = (concierge_root / "main.py").read_text(encoding="utf-8")
    assert "lastfm_service.lastfm_service.client.aclose()" in main_source
    assert "musicbrainz_service.musicbrainz_service.client.aclose()" in main_source
    assert "spotify_service.spotify_service.client.aclose()" in main_source
