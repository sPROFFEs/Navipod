import asyncio
import importlib.util
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

WORKER_PATH = Path(__file__).with_name("worker.py")
SPEC = importlib.util.spec_from_file_location("navipod_downloader_worker", WORKER_PATH)
worker = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(worker)


def test_request_rejects_local_file_urls():
    with pytest.raises(ValidationError, match=r"only HTTP\(S\) media URLs"):
        worker.DownloadRequest(job_id="safe-id", url="file:///etc/passwd")


def test_worker_auth_uses_generated_shared_token(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "DOWNLOAD_ROOT", tmp_path)
    monkeypatch.setattr(worker, "TOKEN_FILE", tmp_path / ".worker-token")

    token = worker._ensure_token()
    worker.require_auth(f"Bearer {token}")

    with pytest.raises(HTTPException) as exc_info:
        worker.require_auth("Bearer wrong-token")
    assert exc_info.value.status_code == 401


def test_spotiflac_uses_pinned_extension_provider_names(tmp_path, monkeypatch):
    request = worker.DownloadRequest(
        job_id="provider-test",
        url="https://open.spotify.com/track/abc",
    )
    state = worker.JobState(request)
    captured = []
    monkeypatch.setattr(
        worker,
        "_connected_spotiflac_services",
        lambda: ["ext:tidal-web", "ext:qobuz-web", "ext:deezer", "ext:amazon"],
    )

    def fake_run_command(_state, command, timeout=2400):
        captured.append(command)
        return False, "expected test failure"

    monkeypatch.setattr(worker, "_run_command", fake_run_command)

    assert worker._run_spotiflac(state, tmp_path) is False
    assert [command[command.index("--service") + 1] for command in captured] == [
        "ext:tidal-web",
        "ext:qobuz-web",
        "ext:deezer",
        "ext:amazon",
    ]
    assert all(command[command.index("--timeout") + 1] == "90" for command in captured)
    assert len(state.fallback_reasons) == 4


def test_spotiflac_skips_browser_auth_when_no_provider_session_exists(tmp_path, monkeypatch):
    request = worker.DownloadRequest(
        job_id="unattended-provider-test",
        url="https://open.spotify.com/track/abc",
    )
    state = worker.JobState(request)
    monkeypatch.setattr(worker, "_connected_spotiflac_services", lambda: [])
    monkeypatch.setattr(
        worker,
        "_run_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("SpotiFLAC command was started")),
    )

    assert worker._run_spotiflac(state, tmp_path) is False
    assert "No connected SpotiFLAC lossless provider session" in state.fallback_reasons[0]


def test_spotiflac_shared_auth_timeout_does_not_retry_every_provider(tmp_path, monkeypatch):
    request = worker.DownloadRequest(
        job_id="shared-auth-timeout-test",
        url="https://open.spotify.com/track/abc",
    )
    state = worker.JobState(request)
    captured = []
    monkeypatch.setattr(worker, "_connected_spotiflac_services", lambda: ["ext:tidal-web", "ext:qobuz-web"])

    def fake_run_command(_state, command, timeout=2400):
        captured.append(command)
        return False, "Provider timed out after 90s during Turnstile authentication"

    monkeypatch.setattr(worker, "_run_command", fake_run_command)

    assert worker._run_spotiflac(state, tmp_path) is False
    assert len(captured) == 1
    assert "skipped equivalent provider retries" in state.fallback_reasons[-1]


def test_provider_auth_start_returns_real_challenge_without_callback(monkeypatch):
    class FakeClient:
        authenticated = False
        pending_challenge_id = "chl_safe123"
        endpoints = {"challenge": "/challenge"}
        base_url = "https://api.zarz.moe/v2"

        async def bootstrap(self):
            return "https://unused.example"

        async def aclose(self):
            return None

    monkeypatch.setattr(worker, "_spotiflac_client", lambda _provider: FakeClient())

    payload = asyncio.run(worker.start_provider_auth("tidal"))

    assert payload["status"] == "verification_required"
    assert payload["verification_url"] == "https://api.zarz.moe/v2/challenge?id=chl_safe123"
    assert "cb=" not in payload["verification_url"]


def test_provider_grant_is_exchanged_without_being_returned(monkeypatch):
    exchanged = []

    class FakeClient:
        authenticated = True

        async def exchange_grant(self, grant):
            exchanged.append(grant)

        async def aclose(self):
            return None

    monkeypatch.setattr(worker, "_spotiflac_client", lambda _provider: FakeClient())
    monkeypatch.setattr(
        worker,
        "_spotiflac_provider_status",
        lambda provider: {"provider": provider, "connected": True, "status": "connected"},
    )
    request = worker.ProviderGrantRequest(grant="grant-value-that-is-long-enough")

    payload = asyncio.run(worker.complete_provider_auth("tidal", request))

    assert exchanged == ["grant-value-that-is-long-enough"]
    assert payload["status"] == "connected"
    assert "grant" not in payload


def test_provider_grant_rejects_whitespace_and_extra_fields():
    with pytest.raises(ValidationError):
        worker.ProviderGrantRequest(grant="grant value that must not contain spaces")

    with pytest.raises(ValidationError):
        worker.ProviderGrantRequest(grant="grant-value-that-is-long-enough", session_secret="leak")


def test_idle_provider_sessions_are_refreshed_and_clients_closed(monkeypatch):
    clients = {}

    class FakeClient:
        def __init__(self, provider):
            self.provider = provider
            self.authenticated = provider in {"tidal", "qobuz"}
            self.session_id = f"session-{provider}"
            self.expires_at = "before"
            self.closed = False
            self.ensure_calls = 0

        async def ensure_session(self):
            self.ensure_calls += 1
            if self.provider == "tidal":
                self.expires_at = "after"

        async def aclose(self):
            self.closed = True

    def fake_client(provider):
        client = FakeClient(provider)
        clients[provider] = client
        return client

    monkeypatch.setattr(worker, "_spotiflac_client", fake_client)

    result = asyncio.run(worker._refresh_spotiflac_sessions_once())

    assert result == {
        "tidal": "refreshed",
        "qobuz": "current",
        "deezer": "disconnected",
        "amazon": "disconnected",
    }
    assert clients["tidal"].ensure_calls == 1
    assert clients["qobuz"].ensure_calls == 1
    assert clients["deezer"].ensure_calls == 0
    assert all(client.closed for client in clients.values())


def test_idle_refresh_failure_does_not_block_other_providers(monkeypatch):
    class FakeClient:
        authenticated = True
        session_id = "session"
        expires_at = "expiry"

        def __init__(self, provider):
            self.provider = provider

        async def ensure_session(self):
            if self.provider == "tidal":
                raise RuntimeError("temporary provider failure")

        async def aclose(self):
            return None

    monkeypatch.setattr(worker, "_spotiflac_client", lambda provider: FakeClient(provider))

    result = asyncio.run(worker._refresh_spotiflac_sessions_once())

    assert result["tidal"] == "refresh_failed"
    assert result["qobuz"] == "current"
    assert result["deezer"] == "current"
    assert result["amazon"] == "current"


def test_idle_refresh_waits_for_active_provider_download(monkeypatch):
    ensure_called = threading.Event()

    class FakeClient:
        authenticated = True
        session_id = "session"
        expires_at = "expiry"

        async def ensure_session(self):
            ensure_called.set()

        async def aclose(self):
            return None

    monkeypatch.setattr(worker, "_spotiflac_client", lambda _provider: FakeClient())

    async def exercise_lock():
        lock = worker.SPOTIFLAC_SESSION_LOCKS["tidal"]
        lock.acquire()
        try:
            task = asyncio.create_task(worker._refresh_spotiflac_sessions_once())
            await asyncio.sleep(0.15)
            assert not ensure_called.is_set()
        finally:
            lock.release()
        await task

    asyncio.run(exercise_lock())


def test_session_refresh_loop_survives_unexpected_cycle_failure(monkeypatch):
    calls = 0

    async def fake_refresh():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("unexpected cycle failure")

    async def fake_sleep(_seconds):
        if calls >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(worker, "_refresh_spotiflac_sessions_once", fake_refresh)
    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker._spotiflac_session_refresh_loop())

    assert calls == 2


def test_worker_logs_redact_user_credentials():
    request = worker.DownloadRequest(
        job_id="redaction-test",
        url="https://example.com/song",
        spotify_client_secret="top-secret",
        youtube_cookies="cookie-secret",
    )

    output = worker._redact_output("top-secret cookie-secret", request)

    assert output == "*** ***"


def test_terminal_jobs_are_pruned_with_staging_files(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "DOWNLOAD_ROOT", tmp_path)
    monkeypatch.setattr(worker, "TERMINAL_JOB_TTL_SECONDS", 300)
    request = worker.DownloadRequest(job_id="expired-job", url="https://example.com/song")
    state = worker.JobState(request)
    state.status = "completed"
    state.updated_at = 0
    job_dir = tmp_path / "jobs" / request.job_id
    job_dir.mkdir(parents=True)
    (job_dir / "track.mp3").write_bytes(b"audio")
    worker.jobs[request.job_id] = state

    worker._prune_terminal_jobs()

    assert request.job_id not in worker.jobs
    assert not job_dir.exists()


def test_active_job_delete_requests_cancellation(monkeypatch):
    request = worker.DownloadRequest(job_id="cancel-job", url="https://example.com/song")
    state = worker.JobState(request)
    state.status = "running"
    worker.jobs[request.job_id] = state

    try:
        result = worker.delete_job(request.job_id)
        assert result == {"status": "cancelling"}
        assert state.cancel_event.is_set()
        assert state.message == "Cancelling download"
    finally:
        worker.jobs.pop(request.job_id, None)


def test_cancelled_subprocess_is_terminated_promptly():
    request = worker.DownloadRequest(job_id="process-cancel", url="https://example.com/song")
    state = worker.JobState(request)

    timer = threading.Timer(0.1, state.cancel_event.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(worker.JobCancelled):
            worker._run_command(state, [sys.executable, "-c", "import time; time.sleep(30)"])
    finally:
        timer.cancel()

    assert time.monotonic() - started < 5


def test_setup_failure_terminalizes_job_and_clears_credentials(monkeypatch):
    request = worker.DownloadRequest(
        job_id="setup-failure",
        url="https://example.com/song",
        spotify_client_id="client-id",
        spotify_client_secret="client-secret",
        youtube_cookies="cookie-secret",
    )
    state = worker.JobState(request)
    monkeypatch.setattr(worker, "_job_dir", lambda _job_id: (_ for _ in ()).throw(OSError("disk full")))

    worker._run_job(state)

    assert state.status == "failed"
    assert state.error_type == "worker_error"
    assert "disk full" in state.error
    assert state.request.spotify_client_id is None
    assert state.request.spotify_client_secret is None
    assert state.request.youtube_cookies is None


def test_cookie_permission_failure_removes_secret_file(tmp_path, monkeypatch):
    request = worker.DownloadRequest(
        job_id="cookie-permission-failure",
        url="https://example.com/song",
        youtube_cookies="cookie-secret",
    )
    state = worker.JobState(request)
    monkeypatch.setattr(worker, "DOWNLOAD_ROOT", tmp_path)
    monkeypatch.setattr(worker.os, "chmod", lambda *_args: (_ for _ in ()).throw(OSError("chmod failed")))

    worker._run_job(state)

    assert state.status == "failed"
    assert state.request.youtube_cookies is None
    assert not (tmp_path / "jobs" / request.job_id / ".cookies.txt").exists()
