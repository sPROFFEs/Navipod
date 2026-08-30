from __future__ import annotations

import asyncio
import base64
import importlib.metadata
import json
import logging
import os
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlencode

import httpx
import yt_dlp
from auth_browser import manager as auth_browser_manager
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("navipod.downloader_worker")

DOWNLOAD_ROOT = Path(os.getenv("DOWNLOAD_ROOT", "/downloads")).resolve()
TOKEN_FILE = DOWNLOAD_ROOT / ".worker-token"
MAX_COOKIE_BYTES = 1024 * 1024
MAX_LOG_LENGTH = 2000
TERMINAL_JOB_TTL_SECONDS = max(300, int(os.getenv("TERMINAL_JOB_TTL_SECONDS", "3600")))
JOB_TIMEOUT_SECONDS = max(60, int(os.getenv("JOB_TIMEOUT_SECONDS", "2400")))
SPOTIFLAC_SESSION_REFRESH_INTERVAL_SECONDS = min(
    3600,
    max(60, int(os.getenv("SPOTIFLAC_SESSION_REFRESH_INTERVAL_SECONDS", "900"))),
)
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav", ".aac", ".webm"}
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
PERCENT_RE = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d+)?)%")
SPOTIFLAC_PROVIDERS = {
    "tidal": {"extension": "tidal-web", "service": "ext:tidal-web", "label": "TIDAL"},
    "qobuz": {"extension": "qobuz-web", "service": "ext:qobuz-web", "label": "Qobuz"},
    "deezer": {"extension": "deezer", "service": "ext:deezer", "label": "Deezer"},
    "amazon": {"extension": "amazon", "service": "ext:amazon", "label": "Amazon Music"},
}
SPOTIFLAC_AUTH_LOCKS = {provider: asyncio.Lock() for provider in SPOTIFLAC_PROVIDERS}
SPOTIFLAC_SESSION_LOCKS = {provider: threading.Lock() for provider in SPOTIFLAC_PROVIDERS}


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=4096)
    spotify_client_id: str | None = Field(default=None, max_length=512)
    spotify_client_secret: str | None = Field(default=None, max_length=512)
    youtube_cookies: str | None = Field(default=None, max_length=MAX_COOKIE_BYTES)

    @field_validator("job_id")
    @classmethod
    def validate_job_id(cls, value: str) -> str:
        if not JOB_ID_RE.fullmatch(value):
            raise ValueError("job_id contains unsupported characters")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        if value.startswith("ytsearch1:"):
            return value
        if not value.startswith(("https://", "http://")):
            raise ValueError("only HTTP(S) media URLs are accepted")
        return value


class ProviderGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant: str = Field(min_length=16, max_length=4096)

    @field_validator("grant")
    @classmethod
    def validate_grant(cls, value: str) -> str:
        value = value.strip()
        if not value or any(char.isspace() for char in value):
            raise ValueError("grant is malformed")
        return value


class BrowserStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=1, max_length=4096)


class BrowserOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=8, max_length=128)
    url: str = Field(min_length=1, max_length=4096)


class JobCancelled(RuntimeError):
    pass


class JobState:
    def __init__(self, request: DownloadRequest):
        self.request = request
        self.status = "pending"
        self.progress = 0
        self.message = "Queued"
        self.engine: str | None = None
        self.error: str | None = None
        self.error_type: str | None = None
        self.fallback_reasons: list[str] = []
        self.files: list[str] = []
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
        self.cancel_event = threading.Event()
        self.lock = threading.RLock()

    def update(self, **values) -> None:
        with self.lock:
            for key, value in values.items():
                setattr(self, key, value)
            self.updated_at = time.time()

    def append_fallback(self, reason: str | None) -> None:
        reason = (reason or "").strip()
        if not reason:
            return
        with self.lock:
            clipped = reason[-500:]
            if clipped not in self.fallback_reasons:
                self.fallback_reasons.append(clipped)
            self.updated_at = time.time()

    def serialize(self) -> dict:
        with self.lock:
            return {
                "job_id": self.request.job_id,
                "status": self.status,
                "progress": self.progress,
                "message": self.message,
                "engine": self.engine,
                "error": self.error,
                "error_type": self.error_type,
                "fallback_reasons": list(self.fallback_reasons),
                "files": list(self.files),
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }


app = FastAPI(title="Navipod Downloader Worker", docs_url=None, redoc_url=None, openapi_url=None)
jobs: dict[str, JobState] = {}
jobs_lock = threading.RLock()
executor = ThreadPoolExecutor(max_workers=max(1, int(os.getenv("CONCURRENT_DOWNLOADS", "3"))))
spotiflac_refresh_task: asyncio.Task | None = None


def _ensure_token() -> str:
    DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    if not TOKEN_FILE.exists():
        temporary = TOKEN_FILE.with_suffix(".tmp")
        temporary.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        os.chmod(temporary, 0o640)
        os.replace(temporary, TOKEN_FILE)
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def require_auth(authorization: str | None = Header(default=None)) -> None:
    expected = _ensure_token()
    supplied = ""
    if authorization and authorization.startswith("Bearer "):
        supplied = authorization[7:].strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid worker token")


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _installed_extensions() -> dict[str, str]:
    result = {}
    root = Path.home() / ".spotiflac" / "extensions"
    for manifest_path in root.glob("*/manifest.json"):
        try:
            import json

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            result[str(manifest.get("name") or manifest_path.parent.name)] = str(manifest.get("version") or "unknown")
        except Exception:
            continue
    return result


def _spotiflac_provider(provider: str) -> dict[str, str]:
    definition = SPOTIFLAC_PROVIDERS.get((provider or "").strip().lower())
    if not definition:
        raise HTTPException(status_code=404, detail="Unknown SpotiFLAC provider")
    return definition


def _spotiflac_client(provider: str):
    definition = _spotiflac_provider(provider)
    manifest_path = Path.home() / ".spotiflac" / "extensions" / definition["extension"] / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if str(manifest.get("name") or "") != definition["extension"]:
            raise ValueError("extension manifest name mismatch")
        signed_session = manifest.get("signedSession") or {}
        if signed_session.get("baseUrl") != "https://api.zarz.moe/v2":
            raise ValueError("extension signed-session endpoint is not trusted")
        from SpotiFLAC.extensions.runtime_features import signed_session_client

        client = signed_session_client(manifest)
        if client is None:
            raise ValueError("extension does not provide signed-session support")
        return client
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Could not initialize SpotiFLAC provider %s: %s", provider, exc)
        raise HTTPException(status_code=503, detail="SpotiFLAC provider runtime is unavailable") from exc


def _spotiflac_provider_status(provider: str) -> dict:
    definition = _spotiflac_provider(provider)
    try:
        client = _spotiflac_client(provider)
    except HTTPException as exc:
        return {
            "provider": provider,
            "label": definition["label"],
            "service": definition["service"],
            "status": "unavailable",
            "connected": False,
            "error": str(exc.detail),
        }
    return {
        "provider": provider,
        "label": definition["label"],
        "service": definition["service"],
        "status": "connected" if client.authenticated else "disconnected",
        "connected": bool(client.authenticated),
        "expires_at": client.expires_at,
        "refresh_after": client.refresh_after,
        "capabilities": list(client.capabilities or []),
    }


def _connected_spotiflac_services() -> list[str]:
    connected = []
    for provider, definition in SPOTIFLAC_PROVIDERS.items():
        try:
            if _spotiflac_provider_status(provider)["connected"]:
                connected.append(definition["service"])
        except Exception:
            logger.debug("Could not inspect SpotiFLAC provider %s", provider, exc_info=True)
    return connected


@asynccontextmanager
async def _spotiflac_session_guard(provider: str):
    """Coordinate async session mutation with synchronous SpotiFLAC jobs."""
    lock = SPOTIFLAC_SESSION_LOCKS[provider]
    while not lock.acquire(blocking=False):
        await asyncio.sleep(0.1)
    try:
        yield
    finally:
        lock.release()


async def _refresh_spotiflac_sessions_once() -> dict[str, str]:
    results = {}
    for provider in SPOTIFLAC_PROVIDERS:
        async with SPOTIFLAC_AUTH_LOCKS[provider]:
            async with _spotiflac_session_guard(provider):
                try:
                    client = _spotiflac_client(provider)
                except HTTPException:
                    results[provider] = "unavailable"
                    continue
                try:
                    if not client.authenticated:
                        results[provider] = "disconnected"
                        continue
                    previous_session = client.session_id
                    previous_expiry = client.expires_at
                    await client.ensure_session()
                    refreshed = client.session_id != previous_session or client.expires_at != previous_expiry
                    results[provider] = "refreshed" if refreshed else "current"
                    if refreshed:
                        logger.info("Refreshed SpotiFLAC provider session: %s", provider)
                except Exception as exc:
                    results[provider] = "refresh_failed"
                    logger.warning("Could not refresh SpotiFLAC provider session %s: %s", provider, exc)
                finally:
                    with suppress(Exception):
                        await client.aclose()
    return results


async def _spotiflac_session_refresh_loop() -> None:
    while True:
        try:
            await _refresh_spotiflac_sessions_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected SpotiFLAC session refresh failure")
        await asyncio.sleep(SPOTIFLAC_SESSION_REFRESH_INTERVAL_SECONDS)


@app.on_event("startup")
async def startup() -> None:
    global spotiflac_refresh_task
    _ensure_token()
    (DOWNLOAD_ROOT / "jobs").mkdir(parents=True, exist_ok=True)
    # A container restart invalidates any previous remote desktop session, but
    # keeps the persistent Chromium profile and provider state intact.
    auth_browser_manager.cleanup_stale()
    if spotiflac_refresh_task is None or spotiflac_refresh_task.done():
        spotiflac_refresh_task = asyncio.create_task(
            _spotiflac_session_refresh_loop(),
            name="spotiflac-session-refresh",
        )


@app.on_event("shutdown")
async def shutdown() -> None:
    global spotiflac_refresh_task
    auth_browser_manager.stop()
    if spotiflac_refresh_task is None:
        return
    spotiflac_refresh_task.cancel()
    with suppress(asyncio.CancelledError):
        await spotiflac_refresh_task
    spotiflac_refresh_task = None


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "browser": auth_browser_manager.status(),
        "versions": {
            "yt-dlp": _package_version("yt-dlp"),
            "spotdl": _package_version("spotdl"),
            "SpotiFLAC": _package_version("SpotiFLAC"),
        },
        "extensions": _installed_extensions(),
    }


@app.get("/status", dependencies=[Depends(require_auth)])
def status() -> dict:
    _prune_terminal_jobs()
    with jobs_lock:
        active = sum(1 for job in jobs.values() if job.status in {"pending", "running"})
        failed = sum(1 for job in jobs.values() if job.status == "failed")
    return {**health(), "active_jobs": active, "failed_jobs": failed}


@app.get("/providers", dependencies=[Depends(require_auth)])
def provider_statuses() -> dict:
    return {"providers": [_spotiflac_provider_status(provider) for provider in SPOTIFLAC_PROVIDERS]}


@app.post("/browser/start", dependencies=[Depends(require_auth)], status_code=202)
def start_auth_browser(request: BrowserStartRequest) -> dict:
    """Start the private browser used for manual provider verification."""
    try:
        return auth_browser_manager.start(request.provider, request.url).serialize()
    except RuntimeError as exc:
        if str(exc) == "auth_browser_already_running":
            raise HTTPException(status_code=409, detail="auth_browser_already_running") from exc
        logger.warning("Authentication browser startup failed for provider %s: %s", request.provider, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/browser/status", dependencies=[Depends(require_auth)])
def auth_browser_status() -> dict:
    return auth_browser_manager.status()


@app.post("/browser/open", dependencies=[Depends(require_auth)])
def open_auth_browser(request: BrowserOpenRequest) -> dict:
    try:
        return auth_browser_manager.open_url(request.session_id, request.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/browser/stop", dependencies=[Depends(require_auth)])
def stop_auth_browser() -> dict:
    return auth_browser_manager.stop()


@app.post("/providers/{provider}/auth/start", dependencies=[Depends(require_auth)])
async def start_provider_auth(provider: str) -> dict:
    provider = (provider or "").strip().lower()
    definition = _spotiflac_provider(provider)
    async with SPOTIFLAC_AUTH_LOCKS[provider]:
        async with _spotiflac_session_guard(provider):
            client = _spotiflac_client(provider)
            try:
                result = await client.bootstrap()
                if result is True and client.authenticated:
                    return {"status": "connected", "provider": _spotiflac_provider_status(provider)}
                challenge_id = (client.pending_challenge_id or "").strip()
                if not challenge_id:
                    raise RuntimeError("provider bootstrap returned no challenge")
                challenge_path = str(client.endpoints.get("challenge") or "/challenge")
                verification_url = f"{client.base_url}{challenge_path}?{urlencode({'id': challenge_id})}"
                return {
                    "status": "verification_required",
                    "provider": provider,
                    "label": definition["label"],
                    "verification_url": verification_url,
                    "expires_in": 300,
                }
            except HTTPException:
                raise
            except Exception as exc:
                logger.warning("SpotiFLAC provider %s bootstrap failed: %s", provider, exc)
                raise HTTPException(status_code=502, detail="Provider verification could not be started") from exc
            finally:
                await client.aclose()


@app.post("/providers/{provider}/auth/complete", dependencies=[Depends(require_auth)])
async def complete_provider_auth(provider: str, request: ProviderGrantRequest) -> dict:
    provider = (provider or "").strip().lower()
    _spotiflac_provider(provider)
    async with SPOTIFLAC_AUTH_LOCKS[provider]:
        async with _spotiflac_session_guard(provider):
            client = _spotiflac_client(provider)
            try:
                await client.exchange_grant(request.grant)
                if not client.authenticated:
                    raise RuntimeError("provider did not return an authenticated session")
                return {"status": "connected", "provider": _spotiflac_provider_status(provider)}
            except Exception as exc:
                logger.warning("SpotiFLAC provider %s grant exchange failed: %s", provider, exc)
                raise HTTPException(status_code=502, detail="Provider verification could not be completed") from exc
            finally:
                await client.aclose()


@app.delete("/providers/{provider}/auth", dependencies=[Depends(require_auth)])
async def disconnect_provider(provider: str) -> dict:
    provider = (provider or "").strip().lower()
    _spotiflac_provider(provider)
    async with SPOTIFLAC_AUTH_LOCKS[provider]:
        async with _spotiflac_session_guard(provider):
            client = _spotiflac_client(provider)
            try:
                client.clear()
                return {"status": "disconnected", "provider": _spotiflac_provider_status(provider)}
            finally:
                await client.aclose()


@app.post("/jobs", dependencies=[Depends(require_auth)], status_code=202)
def create_job(request: DownloadRequest) -> dict:
    _prune_terminal_jobs()
    with jobs_lock:
        existing = jobs.get(request.job_id)
        if existing:
            return existing.serialize()
        state = JobState(request)
        jobs[request.job_id] = state
    executor.submit(_run_job, state)
    return state.serialize()


@app.get("/jobs/{job_id}", dependencies=[Depends(require_auth)])
def get_job(job_id: str) -> dict:
    if not JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    with jobs_lock:
        state = jobs.get(job_id)
    if not state:
        raise HTTPException(status_code=404, detail="Job not found")
    return state.serialize()


@app.delete("/jobs/{job_id}", dependencies=[Depends(require_auth)])
def delete_job(job_id: str) -> dict:
    if not JOB_ID_RE.fullmatch(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    with jobs_lock:
        state = jobs.get(job_id)
        if not state:
            return {"status": "absent"}
        if state.status in {"pending", "running"}:
            state.cancel_event.set()
            state.update(message="Cancelling download")
            return {"status": "cancelling"}
        jobs.pop(job_id, None)
    shutil.rmtree(_job_dir(job_id), ignore_errors=True)
    return {"status": "deleted"}


def _job_dir(job_id: str) -> Path:
    if not JOB_ID_RE.fullmatch(job_id):
        raise ValueError("invalid worker job id")
    path = (DOWNLOAD_ROOT / "jobs" / job_id).resolve()
    path.relative_to((DOWNLOAD_ROOT / "jobs").resolve())
    return path


def _prune_terminal_jobs() -> None:
    cutoff = time.time() - TERMINAL_JOB_TTL_SECONDS
    expired_ids = []
    with jobs_lock:
        for job_id, state in list(jobs.items()):
            if state.status in {"completed", "failed", "cancelled"} and state.updated_at < cutoff:
                jobs.pop(job_id, None)
                expired_ids.append(job_id)
    for job_id in expired_ids:
        shutil.rmtree(_job_dir(job_id), ignore_errors=True)


def _audio_files(folder: Path) -> list[Path]:
    return sorted(path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS)


def _redact_output(value: str, request: DownloadRequest) -> str:
    cleaned = value or ""
    for secret in (request.spotify_client_secret, request.youtube_cookies):
        if secret:
            cleaned = cleaned.replace(secret, "***")
    return cleaned[-MAX_LOG_LENGTH:]


def _raise_if_cancelled(state: JobState) -> None:
    if state.cancel_event.is_set():
        raise JobCancelled("Download cancelled")
    if time.monotonic() >= state.deadline:
        state.cancel_event.set()
        raise JobCancelled(f"Worker job exceeded its {JOB_TIMEOUT_SECONDS} second limit")


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()
        process.wait(timeout=5)


def _run_command(state: JobState, command: list[str], timeout: int = 2400) -> tuple[bool, str]:
    safe_command = list(command)
    for flag in ("--client-secret", "--cookie-file"):
        if flag in safe_command:
            index = safe_command.index(flag)
            if index + 1 < len(safe_command):
                safe_command[index + 1] = "***"
    logger.info("Worker job %s command: %s", state.request.job_id, " ".join(safe_command))
    _raise_if_cancelled(state)
    process = None
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        command_deadline = min(state.deadline, time.monotonic() + timeout)
        while True:
            _raise_if_cancelled(state)
            remaining = command_deadline - time.monotonic()
            if remaining <= 0:
                state.cancel_event.set()
                raise JobCancelled("Downloader command timed out")
            try:
                stdout, stderr = process.communicate(timeout=min(1.0, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
    except JobCancelled:
        if process is not None:
            _stop_process(process)
        raise
    except Exception as exc:
        if process is not None:
            _stop_process(process)
        return False, str(exc)
    output = _redact_output((stderr or stdout or "").strip(), state.request)
    return process.returncode == 0, output or f"Command exited with code {process.returncode}"


def _write_cookie_file(folder: Path, request: DownloadRequest) -> Path | None:
    if not request.youtube_cookies:
        return None
    cookie_path = folder / ".cookies.txt"
    try:
        cookie_path.write_text(request.youtube_cookies, encoding="utf-8")
        os.chmod(cookie_path, 0o600)
    except Exception:
        cookie_path.unlink(missing_ok=True)
        raise
    return cookie_path


def _run_spotiflac(state: JobState, folder: Path) -> bool:
    _raise_if_cancelled(state)
    providers = _connected_spotiflac_services()
    if not providers:
        state.append_fallback("No connected SpotiFLAC lossless provider session; using non-interactive fallbacks")
        return False

    # SpotiFLAC's extension bridge creates signed-session asyncio primitives
    # per process. After one provider times out, reusing that process for the
    # next provider can bind those primitives to a closed event loop. Keep the
    # providers isolated so one unhealthy service cannot poison every fallback.
    providers_by_service = {definition["service"]: provider for provider, definition in SPOTIFLAC_PROVIDERS.items()}
    for service in providers:
        _raise_if_cancelled(state)
        provider = providers_by_service[service]
        state.update(progress=8, message=f"Trying SpotiFLAC provider {service.removeprefix('ext:')}")
        command = [
            "spotiflac",
            state.request.url,
            str(folder),
            "--service",
            service,
            "--max-concurrent",
            "1",
            "--timeout",
            "90",
            "--no-lyrics",
            "--no-enrich",
        ]
        with SPOTIFLAC_SESSION_LOCKS[provider]:
            ok, output = _run_command(state, command)
        if ok and _audio_files(folder):
            state.update(engine="spotiflac")
            return True
        reason = output if not ok else "SpotiFLAC produced no audio files"
        state.append_fallback(f"{service}: {reason}")
        lowered = reason.lower()
        if any(
            marker in lowered
            for marker in (
                "provider timed out",
                "turnstile",
                "cloudflare bypass",
                "signed session",
                "signed-session",
                "session grant",
            )
        ):
            state.append_fallback(
                "SpotiFLAC shared signed-session authentication failed; skipped equivalent provider retries"
            )
            break
    return False


def _run_spotdl(state: JobState, folder: Path, cookie_path: Path | None, *, authenticated: bool, basic: bool) -> bool:
    _raise_if_cancelled(state)
    label = "authenticated" if authenticated else ("basic" if basic else "anonymous")
    state.update(progress=25 if not basic else 45, message=f"Trying spotDL {label} mode")
    command = ["spotdl", "download", state.request.url]
    if authenticated and state.request.spotify_client_id and state.request.spotify_client_secret:
        command.extend(
            ["--client-id", state.request.spotify_client_id, "--client-secret", state.request.spotify_client_secret]
        )
    if cookie_path:
        command.extend(["--cookie-file", str(cookie_path)])
    command.extend(["--yt-dlp-args", "--remote-components ejs:github --js-runtimes deno,node"])
    command.extend(
        [
            "--output",
            f"{folder}/{{artist}} - {{title}}.{{ext}}",
            "--format",
            "mp3",
            "--bitrate",
            "128k" if basic else "256k",
            "--overwrite",
            "skip",
            "--print-errors",
        ]
    )
    ok, output = _run_command(state, command)
    if ok and _audio_files(folder):
        state.update(engine=f"spotdl-{'auth' if authenticated else ('basic' if basic else 'anonymous')}")
        return True
    state.append_fallback(output if not ok else f"spotDL {label} mode produced no audio files")
    return False


def _spotify_search_queries(request: DownloadRequest) -> list[str]:
    if not (request.spotify_client_id and request.spotify_client_secret):
        return []
    match = re.search(r"spotify\.com/track/([A-Za-z0-9]+)", request.url)
    if not match:
        return []
    try:
        raw_auth = f"{request.spotify_client_id}:{request.spotify_client_secret}".encode()
        with httpx.Client(timeout=10.0) as client:
            token_response = client.post(
                "https://accounts.spotify.com/api/token",
                headers={"Authorization": f"Basic {base64.b64encode(raw_auth).decode()}"},
                data={"grant_type": "client_credentials"},
            )
            token_response.raise_for_status()
            token = token_response.json().get("access_token")
            track_response = client.get(
                f"https://api.spotify.com/v1/tracks/{match.group(1)}",
                headers={"Authorization": f"Bearer {token}"},
            )
            track_response.raise_for_status()
            track = track_response.json()
        artists = track.get("artists") or []
        artist = artists[0].get("name", "") if artists else ""
        title = track.get("name") or ""
        base = re.sub(r"\s+", " ", f"{artist} {title}").strip()
        no_parens = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", base).strip()
        candidates = [f"{base} audio", f"{base} topic", f"{base} official audio"]
        if no_parens and no_parens != base:
            candidates.extend([f"{no_parens} audio", f"{no_parens} topic", f"{no_parens} official audio"])
        return [f"ytsearch1:{candidate}" for candidate in dict.fromkeys(candidates) if candidate.strip()]
    except Exception as exc:
        logger.warning("Spotify metadata fallback failed for worker job %s: %s", request.job_id, exc)
        return []


def _is_age_gate(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in ("confirm your age", "age-restricted", "may be inappropriate"))


def _run_ytdlp(state: JobState, url: str, folder: Path, cookie_path: Path | None) -> bool:
    client_strategies = [
        {"player_client": ["web"], "skip": ["dash", "hls"]},
        {"player_client": ["web_embedded"], "skip": ["dash", "hls"]},
        {"player_client": ["ios", "web"], "skip": ["dash", "hls"]},
        {"player_client": ["android", "web"], "skip": ["dash", "hls"]},
        {"player_client": ["android_vr"], "skip": ["dash", "hls"]},
    ]
    cookie_strategies = [
        {"player_client": ["tv", "web_safari"], "skip": []},
        {"player_client": ["tv_embedded", "web_safari"], "skip": []},
        {"player_client": ["web_embedded", "web_safari"], "skip": []},
        {"player_client": ["web", "web_safari"], "skip": []},
    ]
    last_error = "yt-dlp exhausted all strategies"

    def progress_hook(payload: dict) -> None:
        _raise_if_cancelled(state)
        if payload.get("status") != "downloading":
            return
        match = PERCENT_RE.search(str(payload.get("_percent_str") or ""))
        percent = min(85, max(5, int(float(match.group(1))))) if match else state.progress
        filename = Path(str(payload.get("filename") or "Downloading")).name
        state.update(progress=percent, message=f"Downloading: {filename}")

    def attempt(strategies: list[dict], use_cookies: bool) -> bool:
        nonlocal last_error
        for strategy in strategies:
            _raise_if_cancelled(state)
            opts = {
                "format": "bestaudio/best",
                "outtmpl": f"{folder}/%(artist)s - %(title)s.%(ext)s",
                "writethumbnail": True,
                "age_limit": 99,
                "noplaylist": "list=" not in url,
                "extract_flat": False,
                "ignoreerrors": False,
                "source_address": "0.0.0.0",
                "force_ipv4": True,
                "postprocessors": [
                    {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"},
                    {"key": "EmbedThumbnail"},
                    {"key": "FFmpegMetadata"},
                ],
                "progress_hooks": [progress_hook],
                "js_runtimes": {"deno": {}, "node": {}},
                "remote_components": ["ejs:github"],
                "sleep_interval": 5,
                "max_sleep_interval": 10,
                "retries": 2,
                "extractor_retries": 2,
                "file_access_retries": 2,
                "socket_timeout": 30,
                "extractor_args": {"youtube": strategy},
            }
            if use_cookies and cookie_path:
                opts["cookiefile"] = str(cookie_path)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.extract_info(url, download=True)
                if _audio_files(folder):
                    state.update(engine="yt-dlp")
                    return True
                last_error = "yt-dlp finished without producing an audio file"
            except JobCancelled:
                raise
            except Exception as exc:
                last_error = str(exc)
                logger.warning("Worker job %s yt-dlp strategy failed: %s", state.request.job_id, last_error[:200])
        return False

    state.update(progress=max(55, state.progress), message="Trying yt-dlp source strategies")
    prefer_cookieless = url.startswith("ytsearch")
    if prefer_cookieless:
        if attempt(client_strategies, False):
            return True
        if cookie_path and attempt(cookie_strategies if _is_age_gate(last_error) else client_strategies, True):
            return True
    else:
        initial = cookie_strategies if cookie_path else client_strategies
        if attempt(initial, bool(cookie_path)):
            return True
        if cookie_path and attempt(client_strategies, False):
            return True
    state.append_fallback(last_error)
    return False


def _run_job(state: JobState) -> None:
    folder = None
    cookie_path = None
    try:
        _raise_if_cancelled(state)
        folder = _job_dir(state.request.job_id)
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, mode=0o770)
        cookie_path = _write_cookie_file(folder, state.request)
        state.update(status="running", progress=3, message="Starting isolated downloader")
        success = False
        if "spotify.com" in state.request.url:
            success = _run_spotiflac(state, folder)
            if not success and state.request.spotify_client_id:
                success = _run_spotdl(state, folder, cookie_path, authenticated=True, basic=False)
            if not success:
                success = _run_spotdl(state, folder, cookie_path, authenticated=False, basic=False)
            if not success:
                success = _run_spotdl(state, folder, cookie_path, authenticated=False, basic=True)
            if not success:
                for query in _spotify_search_queries(state.request):
                    _raise_if_cancelled(state)
                    if _run_ytdlp(state, query, folder, cookie_path):
                        state.update(engine="yt-dlp-spotify-fallback")
                        success = True
                        break
        else:
            success = _run_ytdlp(state, state.request.url, folder, cookie_path)

        files = [str(path.relative_to(folder)) for path in _audio_files(folder)]
        if not success or not files:
            reason = state.fallback_reasons[-1] if state.fallback_reasons else "Downloader produced no audio files"
            state.update(
                status="failed", progress=100, message="Download failed", error=reason, error_type="download_failed"
            )
            return
        state.update(status="completed", progress=100, message="Audio ready for import", files=files)
    except JobCancelled as exc:
        state.update(
            status="cancelled",
            progress=100,
            message="Download cancelled",
            error=str(exc),
            error_type="cancelled",
        )
    except Exception as exc:
        logger.exception("Worker job %s failed", state.request.job_id)
        state.update(
            status="failed",
            progress=100,
            message="Download failed",
            error=str(exc)[-MAX_LOG_LENGTH:],
            error_type="worker_error",
        )
    finally:
        if cookie_path:
            cookie_path.unlink(missing_ok=True)
        # Credentials are needed only while the job is running. Do not retain
        # them in terminal in-memory job records if Concierge cannot clean up.
        state.request.spotify_client_id = None
        state.request.spotify_client_secret = None
        state.request.youtube_cookies = None
