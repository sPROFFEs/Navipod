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
    captured = {}

    def fake_run_command(_state, command, timeout=2400):
        captured["command"] = command
        return False, "expected test failure"

    monkeypatch.setattr(worker, "_run_command", fake_run_command)

    assert worker._run_spotiflac(state, tmp_path) is False
    assert captured["command"][captured["command"].index("--service") + 1 :] == [
        "ext:tidal-web",
        "ext:qobuz-web",
        "ext:deezer",
        "ext:amazon",
        "--max-concurrent",
        "1",
        "--no-lyrics",
        "--no-enrich",
    ]


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
