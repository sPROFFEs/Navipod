import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import database
import httpx
import pytest

# downloader_service imports the production Docker manager at module import time.
# These unit tests exercise only downloader routing, so keep collection independent
# from a running Docker socket.
manager_stub = ModuleType("manager")
manager_stub.invalidate_pool_status_cache = lambda: None
manager_stub.get_pool_status = lambda _db: (0, 0, 0)
sys.modules.setdefault("manager", manager_stub)

import downloader_service
import downloader_worker_client


def _manager(monkeypatch, mode: str):
    manager = object.__new__(downloader_service.DownloadManager)
    manager.db = object()
    manager.settings = SimpleNamespace()
    manager._engine_used = None
    manager._last_download_reason = ""
    manager._fallback_reasons = []
    manager._log = lambda *_args, **_kwargs: None
    manager._set_engine_used = downloader_service.DownloadManager._set_engine_used.__get__(manager)
    manager._set_last_reason = downloader_service.DownloadManager._set_last_reason.__get__(manager)
    manager._append_fallback_reason = downloader_service.DownloadManager._append_fallback_reason.__get__(manager)
    monkeypatch.setattr(downloader_worker_client, "get_downloader_mode", lambda _db: mode)
    return manager


def test_automatic_mode_prefers_worker_without_running_legacy(monkeypatch, tmp_path):
    manager = _manager(monkeypatch, "automatic")
    monkeypatch.setattr(
        downloader_worker_client,
        "download_with_worker",
        lambda *_args: {"engine": "yt-dlp", "fallback_reasons": []},
    )
    manager._handle_ytdlp_robust = lambda *_args: (_ for _ in ()).throw(AssertionError("legacy called"))

    result = manager._download_source(SimpleNamespace(input_url="https://example.com/song"), str(tmp_path), 1)

    assert result is True
    assert manager._engine_used == "worker:yt-dlp"


def test_automatic_mode_falls_back_to_legacy_when_worker_is_unavailable(monkeypatch, tmp_path):
    manager = _manager(monkeypatch, "automatic")

    def unavailable(*_args):
        raise downloader_worker_client.WorkerUnavailable("connection refused")

    monkeypatch.setattr(downloader_worker_client, "download_with_worker", unavailable)
    manager._handle_ytdlp_robust = lambda *_args: True

    result = manager._download_source(SimpleNamespace(input_url="https://example.com/song"), str(tmp_path), 2)

    assert result is True
    assert manager._fallback_reasons == ["Isolated worker: connection refused"]


def test_worker_only_mode_does_not_silently_run_legacy(monkeypatch, tmp_path):
    manager = _manager(monkeypatch, "worker")

    def failed(*_args):
        raise downloader_worker_client.WorkerDownloadFailed("provider failed")

    monkeypatch.setattr(downloader_worker_client, "download_with_worker", failed)
    manager._handle_ytdlp_robust = lambda *_args: (_ for _ in ()).throw(AssertionError("legacy called"))

    result = manager._download_source(SimpleNamespace(input_url="https://example.com/song"), str(tmp_path), 3)

    assert result is False
    assert manager._last_download_reason == "provider failed"


def test_legacy_mode_never_contacts_worker(monkeypatch, tmp_path):
    manager = _manager(monkeypatch, "legacy")
    monkeypatch.setattr(
        downloader_worker_client,
        "download_with_worker",
        lambda *_args: (_ for _ in ()).throw(AssertionError("worker called")),
    )
    manager._handle_spotify_robust = lambda *_args: True

    result = manager._download_source(SimpleNamespace(input_url="https://open.spotify.com/track/abc"), str(tmp_path), 4)

    assert result is True


def test_downloader_mode_defaults_to_automatic_and_round_trips(db_session):
    row = database.SystemSettings(pool_limit_gb=100)
    db_session.add(row)
    db_session.commit()

    assert downloader_worker_client.get_downloader_mode(db_session) == "automatic"
    assert downloader_worker_client.set_downloader_mode(db_session, "legacy") == "legacy"
    assert downloader_worker_client.get_downloader_mode(db_session) == "legacy"


def test_worker_output_path_cannot_escape_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader_worker_client.settings, "DOWNLOADER_STAGING_ROOT", str(tmp_path))
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"audio")

    try:
        downloader_worker_client._safe_worker_source("job-1", "../../outside.mp3")
    except downloader_worker_client.WorkerDownloadFailed as exc:
        assert "unsafe" in str(exc)
    else:
        raise AssertionError("path traversal was accepted")


def test_worker_reads_uploaded_path_based_youtube_cookies(tmp_path):
    cookie_path = tmp_path / "youtube.cookies.txt"
    cookie_path.write_text("# Netscape HTTP Cookie File\n.example.test\tTRUE", encoding="utf-8")
    user_settings = SimpleNamespace(youtube_cookies=None, youtube_cookies_path=str(cookie_path))

    assert downloader_worker_client._read_youtube_cookies(user_settings) == cookie_path.read_text(encoding="utf-8")


def test_worker_protocol_rejects_malformed_payload_for_safe_fallback():
    response = httpx.Response(200, json=["not", "an", "object"])

    with pytest.raises(downloader_worker_client.WorkerUnavailable, match="incompatible response"):
        downloader_worker_client._decode_job_payload(response)


def test_worker_output_copy_rolls_back_partial_promotion(tmp_path, monkeypatch):
    staging_root = tmp_path / "staging"
    job_root = staging_root / "jobs" / "job-atomic"
    job_root.mkdir(parents=True)
    (job_root / "one.mp3").write_bytes(b"one")
    (job_root / "two.mp3").write_bytes(b"two")
    destination = tmp_path / "destination"
    destination.mkdir()
    monkeypatch.setattr(downloader_worker_client.settings, "DOWNLOADER_STAGING_ROOT", str(staging_root))
    real_replace = downloader_worker_client.os.replace
    calls = 0

    def fail_second_replace(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk error")
        return real_replace(source, target)

    monkeypatch.setattr(downloader_worker_client.os, "replace", fail_second_replace)

    with pytest.raises(downloader_worker_client.WorkerDownloadFailed, match="atomically"):
        downloader_worker_client._copy_outputs("job-atomic", ["one.mp3", "two.mp3"], str(destination))

    assert list(destination.iterdir()) == []


def test_unconfirmed_worker_cleanup_never_falls_back_to_legacy(monkeypatch, tmp_path):
    manager = _manager(monkeypatch, "automatic")

    def cleanup_pending(*_args):
        raise downloader_worker_client.WorkerCleanupPending("still running")

    monkeypatch.setattr(downloader_worker_client, "download_with_worker", cleanup_pending)
    manager._handle_ytdlp_robust = lambda *_args: (_ for _ in ()).throw(AssertionError("legacy called"))

    with pytest.raises(downloader_worker_client.WorkerCleanupPending, match="still running"):
        manager._download_source(SimpleNamespace(input_url="https://example.com/song"), str(tmp_path), 5)


def test_admin_downloader_endpoints_require_admin_dependency():
    source = (Path(__file__).resolve().parents[1] / "routers" / "admin.py").read_text(encoding="utf-8")
    protected_routes = [
        '@router.get("/api/downloader/status")',
        '@router.get("/api/downloader/providers")',
        '@router.delete("/api/downloader/providers/{provider}")',
        '@router.post("/system/downloader/mode")',
    ]
    for route in protected_routes:
        start = source.index(route)
        next_route = source.find("\n\n@router.", start + len(route))
        block = source[start : next_route if next_route >= 0 else len(source)]
        assert "Depends(get_current_admin)" in block


def test_remote_provider_connection_routes_are_not_exposed():
    source = (Path(__file__).resolve().parents[1] / "routers" / "admin.py").read_text(encoding="utf-8")

    assert '@router.post("/api/downloader/providers/{provider}/start")' not in source
    assert '@router.post("/api/downloader/providers/{provider}/complete")' not in source
    assert '@router.get("/api/downloader/providers")' in source
    assert '@router.delete("/api/downloader/providers/{provider}")' in source


def test_auth_browser_url_authorizes_page_and_websocket_requests():
    source = (Path(__file__).resolve().parents[1] / "routers" / "admin.py").read_text(encoding="utf-8")
    assert 'websocket_path = quote(f"admin/auth-browser/websockify?session_id={session_id}&token={token}"' in source
    assert "f\"&session_id={quote(session_id, safe='')}&token={quote(token, safe='')}\"" in source
