from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from pathlib import Path

import httpx
from navipod_config import settings

logger = logging.getLogger(__name__)

VALID_DOWNLOADER_MODES = {"automatic", "worker", "legacy"}
VALID_SPOTIFLAC_PROVIDERS = {"tidal", "qobuz", "deezer", "amazon"}
TERMINAL_WORKER_STATUSES = {"completed", "failed", "cancelled"}
VALID_WORKER_STATUSES = {"pending", "running", *TERMINAL_WORKER_STATUSES}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav", ".aac", ".webm"}
MAX_COOKIE_BYTES = 1024 * 1024
CANCEL_CONFIRM_TIMEOUT_SECONDS = 30.0


class WorkerUnavailable(RuntimeError):
    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.detail = detail


class WorkerConflict(RuntimeError):
    pass


class WorkerDownloadFailed(RuntimeError):
    pass


class WorkerCleanupPending(RuntimeError):
    """The worker may still be writing, so starting legacy fallback is unsafe."""

    pass


def get_downloader_mode(db) -> str:
    import ops_core

    row = ops_core.ensure_system_settings_record(db)
    mode = str(getattr(row, "downloader_mode", None) or "automatic").strip().lower()
    return mode if mode in VALID_DOWNLOADER_MODES else "automatic"


def set_downloader_mode(db, mode: str) -> str:
    import ops_core

    normalized = str(mode or "").strip().lower()
    if normalized not in VALID_DOWNLOADER_MODES:
        raise ValueError("Invalid downloader mode")
    row = ops_core.ensure_system_settings_record(db)
    row.downloader_mode = normalized
    db.commit()
    return normalized


def _read_worker_token() -> str:
    token_path = Path(settings.DOWNLOADER_WORKER_TOKEN_FILE)
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise WorkerUnavailable(f"Downloader worker token is unavailable: {exc}") from exc
    if not token:
        raise WorkerUnavailable("Downloader worker token is empty")
    return token


def _client(timeout: float = 5.0) -> httpx.Client:
    return httpx.Client(
        base_url=settings.DOWNLOADER_WORKER_URL.rstrip("/"),
        headers={"Authorization": f"Bearer {_read_worker_token()}"},
        timeout=timeout,
    )


def get_worker_status() -> dict:
    try:
        with _client(timeout=settings.DOWNLOADER_WORKER_HEALTH_TIMEOUT_SECONDS) as client:
            response = client.get("/status")
            response.raise_for_status()
            payload = response.json()
        return {"available": True, **payload}
    except Exception as exc:
        return {
            "available": False,
            "status": "unavailable",
            "error": str(exc),
            "versions": {},
            "extensions": {},
            "active_jobs": 0,
            "failed_jobs": 0,
        }


def validate_spotiflac_provider(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized not in VALID_SPOTIFLAC_PROVIDERS:
        raise ValueError("Invalid SpotiFLAC provider")
    return normalized


def _worker_json(response: httpx.Response) -> dict:
    try:
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("response is not an object")
        return payload
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        detail = ""
        structured_detail = None
        try:
            raw_detail = response.json().get("detail")
            if isinstance(raw_detail, dict):
                message = raw_detail.get("message")
                detail = message[:500] if isinstance(message, str) else ""
                structured_detail = {"message": detail or "Downloader worker request failed"}
                code = raw_detail.get("code")
                if isinstance(code, str) and len(code) <= 80 and code.replace("_", "").isalnum():
                    structured_detail["code"] = code
                provider = raw_detail.get("provider")
                if provider in VALID_SPOTIFLAC_PROVIDERS:
                    structured_detail["provider"] = provider
                retryable = raw_detail.get("retryable")
                if isinstance(retryable, bool):
                    structured_detail["retryable"] = retryable
                upstream_status = raw_detail.get("upstream_status")
                if isinstance(upstream_status, int) and 100 <= upstream_status <= 599:
                    structured_detail["upstream_status"] = upstream_status
                error_type = raw_detail.get("error_type")
                if isinstance(error_type, str) and len(error_type) <= 100 and error_type.replace("_", "").isalnum():
                    structured_detail["error_type"] = error_type
            elif isinstance(raw_detail, str):
                detail = raw_detail[:500]
        except Exception:
            pass
        if response.status_code == 409:
            raise WorkerConflict(detail or "Downloader worker request conflicts with an active session") from exc
        raise WorkerUnavailable(
            detail or f"Downloader worker request failed: {exc}",
            detail=structured_detail,
        ) from exc


def get_spotiflac_providers() -> list[dict]:
    with _client(timeout=10.0) as client:
        payload = _worker_json(client.get("/providers"))
    providers = payload.get("providers")
    if not isinstance(providers, list) or not all(isinstance(item, dict) for item in providers):
        raise WorkerUnavailable("Downloader worker returned invalid provider status")
    return providers


def disconnect_spotiflac_provider(provider: str) -> dict:
    provider = validate_spotiflac_provider(provider)
    with _client(timeout=10.0) as client:
        return _worker_json(client.delete(f"/providers/{provider}/auth"))


def start_auth_browser(provider: str, url: str) -> dict:
    """Start the worker-local browser used for interactive verification."""
    with _client(timeout=20.0) as client:
        return _worker_json(client.post("/browser/start", json={"provider": provider, "url": url}))


def get_auth_browser_status() -> dict:
    with _client(timeout=10.0) as client:
        return _worker_json(client.get("/browser/status"))


def stop_auth_browser() -> dict:
    with _client(timeout=10.0) as client:
        return _worker_json(client.delete("/browser/stop"))


def open_auth_browser(session_id: str, url: str) -> dict:
    with _client(timeout=10.0) as client:
        return _worker_json(client.post("/browser/open", json={"session_id": session_id, "url": url}))


def start_spotiflac_provider_auth(provider: str) -> dict:
    provider = validate_spotiflac_provider(provider)
    with _client(timeout=20.0) as client:
        return _worker_json(client.post(f"/providers/{provider}/auth/start"))


def check_spotiflac_provider(provider: str) -> dict:
    provider = validate_spotiflac_provider(provider)
    with _client(timeout=20.0) as client:
        return _worker_json(client.post(f"/providers/{provider}/auth/browser-complete"))


def _safe_worker_source(job_id: str, relative_path: str) -> Path:
    job_root = (Path(settings.DOWNLOADER_STAGING_ROOT) / "jobs" / job_id).resolve()
    candidate = (job_root / relative_path).resolve()
    try:
        candidate.relative_to(job_root)
    except ValueError as exc:
        raise WorkerDownloadFailed("Worker returned an unsafe output path") from exc
    if candidate.suffix.lower() not in AUDIO_EXTENSIONS or not candidate.is_file():
        raise WorkerDownloadFailed(f"Worker output is missing or unsupported: {relative_path}")
    return candidate


def _copy_outputs(job_id: str, files: list[str], destination: str) -> int:
    if not files:
        raise WorkerDownloadFailed("Worker completed without returning audio files")
    destination_root = Path(destination).resolve()
    staging_root = destination_root / f".worker-copy-{uuid.uuid4().hex}"
    planned: list[tuple[Path, Path, Path]] = []
    reserved: set[Path] = set()
    try:
        staging_root.mkdir(parents=False, exist_ok=False)
        for index, relative_path in enumerate(files, start=1):
            source = _safe_worker_source(job_id, relative_path)
            target = destination_root / source.name
            if target.exists() or target in reserved:
                target = destination_root / f"{target.stem}_{index}{target.suffix}"
            if target.exists() or target in reserved:
                target = destination_root / f"{target.stem}_{uuid.uuid4().hex[:8]}{target.suffix}"
            staged = staging_root / target.name
            shutil.copy2(source, staged)
            planned.append((source, staged, target))
            reserved.add(target)

        promoted: list[Path] = []
        try:
            for _source, staged, target in planned:
                os.replace(staged, target)
                promoted.append(target)
        except Exception:
            for target in promoted:
                target.unlink(missing_ok=True)
            raise
        return len(promoted)
    except WorkerDownloadFailed:
        raise
    except (OSError, shutil.Error) as exc:
        raise WorkerDownloadFailed(f"Could not copy worker output atomically: {exc}") from exc
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def _read_youtube_cookies(user_settings) -> str | None:
    inline = getattr(user_settings, "youtube_cookies", None)
    if inline:
        return str(inline)
    configured_path = getattr(user_settings, "youtube_cookies_path", None)
    if not configured_path:
        return None
    try:
        cookie_path = Path(configured_path)
        if cookie_path.stat().st_size > MAX_COOKIE_BYTES:
            raise OSError("cookie file exceeds the 1 MiB worker limit")
        return cookie_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        logger.warning("Could not read configured YouTube cookies for the downloader worker: %s", exc)
        return None


def _decode_job_payload(response: httpx.Response) -> dict:
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("response is not an object")
        status = payload.get("status")
        if not isinstance(status, str) or status not in VALID_WORKER_STATUSES:
            raise TypeError("response has an unknown status")
        progress = payload.get("progress", 0)
        if not isinstance(progress, (int, float, str)):
            raise TypeError("response has invalid progress")
        files = payload.get("files", [])
        if files is not None and (not isinstance(files, list) or not all(isinstance(item, str) for item in files)):
            raise TypeError("response has invalid files")
        return payload
    except (ValueError, TypeError) as exc:
        raise WorkerUnavailable(f"Downloader worker returned an incompatible response: {exc}") from exc


def _cancel_and_confirm(client: httpx.Client, worker_job_id: str) -> bool:
    try:
        response = client.delete(f"/jobs/{worker_job_id}")
        if response.status_code == 404:
            return True
        response.raise_for_status()
    except httpx.HTTPError:
        return False

    deadline = time.monotonic() + CANCEL_CONFIRM_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            response = client.get(f"/jobs/{worker_job_id}")
            if response.status_code == 404:
                return True
            response.raise_for_status()
            payload = _decode_job_payload(response)
            if payload["status"] in TERMINAL_WORKER_STATUSES:
                client.delete(f"/jobs/{worker_job_id}")
                return True
        except (httpx.HTTPError, WorkerUnavailable):
            return False
        time.sleep(0.25)
    return False


def download_with_worker(manager, url: str, destination: str, navipod_job_id: int) -> dict:
    worker_job_id = f"navipod-{navipod_job_id}-{uuid.uuid4().hex[:12]}"
    user_settings = manager.settings
    payload = {
        "job_id": worker_job_id,
        "url": url,
        "spotify_client_id": getattr(user_settings, "spotify_client_id", None),
        "spotify_client_secret": getattr(user_settings, "spotify_client_secret", None),
        "youtube_cookies": _read_youtube_cookies(user_settings),
    }
    deadline = time.monotonic() + settings.DOWNLOADER_WORKER_JOB_TIMEOUT_SECONDS
    last_payload = None
    submitted = False
    terminal = False
    last_log: tuple[str, int] | None = None

    try:
        with _client(timeout=10.0) as client:
            response = client.post("/jobs", json=payload)
            if response.status_code >= 500:
                raise WorkerUnavailable(f"Downloader worker returned HTTP {response.status_code}")
            response.raise_for_status()
            submitted = True

            while time.monotonic() < deadline:
                response = client.get(f"/jobs/{worker_job_id}")
                if response.status_code == 404:
                    raise WorkerUnavailable("Downloader worker lost the active job, likely after a restart")
                response.raise_for_status()
                last_payload = _decode_job_payload(response)
                progress = max(5, min(88, int(float(last_payload.get("progress") or 0))))
                message = str(last_payload.get("message") or "Downloading in worker")
                current_log = (message, progress)
                if current_log != last_log:
                    manager._log(navipod_job_id, message, progress)
                    last_log = current_log
                if last_payload.get("status") in TERMINAL_WORKER_STATUSES:
                    terminal = True
                    break
                time.sleep(settings.DOWNLOADER_WORKER_POLL_SECONDS)
            else:
                raise WorkerUnavailable("Downloader worker job timed out")

            if not last_payload or last_payload.get("status") != "completed":
                error = (last_payload or {}).get("error") or "Downloader worker failed"
                raise WorkerDownloadFailed(str(error))

            copied = _copy_outputs(worker_job_id, list(last_payload.get("files") or []), destination)
            if copied <= 0:
                raise WorkerDownloadFailed("Downloader worker produced no importable audio")
            return last_payload
    except (WorkerDownloadFailed, WorkerUnavailable) as exc:
        if submitted and not terminal:
            try:
                with _client(timeout=3.0) as cleanup_client:
                    cancelled = _cancel_and_confirm(cleanup_client, worker_job_id)
            except Exception:
                cancelled = False
            if not cancelled:
                raise WorkerCleanupPending(
                    "Downloader worker state could not be cancelled safely; legacy fallback was not started"
                ) from exc
        raise
    except (httpx.HTTPError, OSError) as exc:
        unavailable = WorkerUnavailable(str(exc))
        if submitted and not terminal:
            try:
                with _client(timeout=3.0) as cleanup_client:
                    cancelled = _cancel_and_confirm(cleanup_client, worker_job_id)
            except Exception:
                cancelled = False
            if not cancelled:
                raise WorkerCleanupPending(
                    "Downloader worker state could not be cancelled safely; legacy fallback was not started"
                ) from exc
        raise unavailable from exc
    finally:
        if terminal:
            try:
                with _client(timeout=3.0) as cleanup_client:
                    cleanup_client.delete(f"/jobs/{worker_job_id}")
            except Exception:
                logger.debug("Could not clean worker job %s", worker_job_id, exc_info=True)
