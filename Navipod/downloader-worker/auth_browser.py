"""Temporary, single-user Chromium session for provider verification.

The manager deliberately owns only process lifecycle. Authorization is kept in
Concierge, which is the only public admin boundary; the worker endpoints are
protected by the existing worker bearer token.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class BrowserSession:
    session_id: str
    provider: str
    url: str
    started_at: float
    expires_at: float
    status: str = "starting"
    error: str | None = None

    def serialize(self) -> dict:
        remaining = max(0, int(self.expires_at - time.time()))
        return {
            "status": self.status,
            "provider": self.provider,
            "session_id": self.session_id,
            "started_at": self.started_at,
            "expires_at": self.expires_at,
            "remaining_seconds": remaining,
            **({"error": self.error} if self.error else {}),
        }


@dataclass
class BrowserProcess:
    name: str
    process: subprocess.Popen
    log_path: Path


class AuthBrowserManager:
    """Manage one disposable Xvfb/Chromium/VNC/noVNC process tree."""

    def __init__(self) -> None:
        self.enabled = os.getenv("AUTH_BROWSER_ENABLED", "true").strip().lower() not in {"0", "false", "no"}
        self.ttl = max(60, min(1800, int(os.getenv("AUTH_BROWSER_TTL", "600"))))
        self.display = os.getenv("AUTH_BROWSER_DISPLAY", ":99")
        self.width = max(800, min(2560, int(os.getenv("AUTH_BROWSER_WIDTH", "1440"))))
        self.height = max(600, min(1600, int(os.getenv("AUTH_BROWSER_HEIGHT", "900"))))
        self.vnc_port = int(os.getenv("AUTH_BROWSER_VNC_PORT", "5900"))
        self.websocket_port = int(os.getenv("AUTH_BROWSER_WEBSOCKET_PORT", "6080"))
        self.profile_root = Path(os.getenv("AUTH_BROWSER_PROFILE", "/home/downloader/.auth-browser"))
        self.chromium_no_sandbox = os.getenv("AUTH_BROWSER_CHROMIUM_NO_SANDBOX", "true").strip().lower() not in {
            "0",
            "false",
            "no",
        }
        self._lock = threading.RLock()
        self._session: BrowserSession | None = None
        self._processes: list[BrowserProcess] = []
        self._timer: threading.Timer | None = None

    def _validate_url(self, url: str) -> str:
        parsed = urlparse(str(url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("verification URL must be an HTTP(S) URL")
        host = (parsed.hostname or "").lower()
        if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
            raise ValueError("verification URL host is not allowed")
        return parsed.geturl()

    def _command(self, name: str) -> str:
        command = shutil.which(name)
        if not command:
            raise RuntimeError(f"required browser component is unavailable: {name}")
        return command

    def _novnc_root(self) -> str:
        for candidate in ("/usr/share/novnc", "/usr/share/noVNC", "/opt/novnc", "/opt/noVNC"):
            if Path(candidate).is_dir():
                return candidate
        raise RuntimeError("noVNC assets are unavailable in the downloader image")

    def _chromium_command(self, chromium: str, browser_profile: Path, url: str) -> list[str]:
        command = [
            chromium,
            f"--display={self.display}",
            f"--user-data-dir={browser_profile}",
            f"--window-size={self.width},{self.height}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
        ]
        # Docker remains the outer sandbox: this container has no new
        # privileges and drops all capabilities. Chromium's namespace/SUID
        # sandbox cannot initialize reliably under that policy.
        if self.chromium_no_sandbox:
            command.append("--no-sandbox")
        command.append(url)
        return command

    def _spawn(self, name: str, command: list[str], *, env: dict[str, str] | None = None) -> BrowserProcess:
        log_path = Path("/tmp") / f"navipod-auth-browser-{secrets.token_hex(6)}-{name}.log"
        log_stream = log_path.open("w", encoding="utf-8")
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log_stream,
            env=env,
            start_new_session=True,
        )
        log_stream.close()
        managed = BrowserProcess(name=name, process=process, log_path=log_path)
        self._processes.append(managed)
        return managed

    def _component_error(self, managed: BrowserProcess) -> str:
        try:
            detail = managed.log_path.read_text(encoding="utf-8", errors="replace")[-1200:].strip()
        except OSError:
            detail = ""
        detail = re.sub(r"https?://\S+", "[redacted-url]", detail)
        return f"{managed.name} exited during startup" + (f": {detail}" if detail else "")

    def _wait_ready(self, managed: BrowserProcess, predicate, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if managed.process.poll() is not None:
                raise RuntimeError(self._component_error(managed))
            if predicate():
                return
            time.sleep(0.1)
        raise RuntimeError(f"{managed.name} did not become ready within {timeout:g} seconds")

    @staticmethod
    def _port_ready(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return True
        except OSError:
            return False

    def _terminate_processes(self) -> None:
        processes = list(reversed(self._processes))
        self._processes.clear()
        for managed in processes:
            process = managed.process
            if process.poll() is not None:
                continue
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                process.terminate()
        deadline = time.monotonic() + 5
        for managed in processes:
            process = managed.process
            remaining = max(0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    process.kill()
                process.wait(timeout=2)
        for managed in processes:
            managed.log_path.unlink(missing_ok=True)

    def _expire(self, session_id: str) -> None:
        with self._lock:
            if self._session and self._session.session_id == session_id:
                self._session.status = "expired"
                self._stop_locked()

    def _stop_locked(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self._terminate_processes()
        if self._session:
            self._session.status = "stopping"
        self._session = None

    def cleanup_stale(self) -> None:
        with self._lock:
            self._stop_locked()

    def start(self, provider: str, url: str) -> BrowserSession:
        if not self.enabled:
            raise RuntimeError("authentication browser is disabled")
        provider = str(provider or "").strip().lower()
        if (
            not provider
            or len(provider) > 64
            or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in provider)
        ):
            raise ValueError("provider is malformed")
        url = self._validate_url(url)
        with self._lock:
            current = self.status()
            if current["status"] not in {"idle", "expired", "failed"}:
                raise RuntimeError("auth_browser_already_running")
            self._stop_locked()
            session = BrowserSession(
                session_id=secrets.token_urlsafe(18),
                provider=provider,
                url=url,
                started_at=time.time(),
                expires_at=time.time() + self.ttl,
            )
            self._session = session
            try:
                self._command("Xvfb")
                chromium = self._command("chromium")
                x11vnc = self._command("x11vnc")
                websockify = self._command("websockify")
                novnc = self._novnc_root()
                self.profile_root.mkdir(parents=True, exist_ok=True)
                browser_profile = self.profile_root / "chromium"
                browser_profile.mkdir(parents=True, exist_ok=True)
                display_env = os.environ.copy()
                display_env["DISPLAY"] = self.display
                display_number = self.display.removeprefix(":").split(".", 1)[0]
                xvfb = self._spawn(
                    "xvfb",
                    [
                        self._command("Xvfb"),
                        self.display,
                        "-screen",
                        "0",
                        f"{self.width}x{self.height}x24",
                        "-ac",
                        "-nolisten",
                        "tcp",
                    ],
                )
                self._wait_ready(xvfb, lambda: Path(f"/tmp/.X11-unix/X{display_number}").exists(), 5)
                chromium_command = self._chromium_command(chromium, browser_profile, url)
                chromium_process = self._spawn(
                    "chromium",
                    chromium_command,
                    env=display_env,
                )
                chromium_ready_at = time.monotonic() + 0.5
                self._wait_ready(chromium_process, lambda: time.monotonic() >= chromium_ready_at, 5)
                vnc = self._spawn(
                    "x11vnc",
                    [
                        x11vnc,
                        "-display",
                        self.display,
                        "-rfbport",
                        str(self.vnc_port),
                        "-localhost",
                        "-nopw",
                        "-forever",
                        "-shared",
                    ],
                    env=display_env,
                )
                self._wait_ready(vnc, lambda: self._port_ready(self.vnc_port), 5)
                websocket = self._spawn(
                    "websockify",
                    [
                        websockify,
                        f"--web={novnc}",
                        str(self.websocket_port),
                        f"127.0.0.1:{self.vnc_port}",
                    ],
                )
                self._wait_ready(websocket, lambda: self._port_ready(self.websocket_port), 5)
                session.status = "ready"
                self._timer = threading.Timer(self.ttl, self._expire, args=(session.session_id,))
                self._timer.daemon = True
                self._timer.start()
                return session
            except Exception as exc:
                session.status = "failed"
                session.error = str(exc)
                self._stop_locked()
                raise

    def status(self) -> dict:
        with self._lock:
            session = self._session
            if session and time.time() >= session.expires_at:
                session.status = "expired"
                self._stop_locked()
                session = None
            if not session:
                return {"status": "idle", "enabled": self.enabled}
            return {"enabled": self.enabled, **session.serialize()}

    def stop(self) -> dict:
        with self._lock:
            self._stop_locked()
            return {"status": "stopped"}

    def open_url(self, session_id: str, url: str) -> dict:
        url = self._validate_url(url)
        with self._lock:
            if not self._session or self._session.session_id != session_id:
                raise RuntimeError("auth browser session is not active")
            # Chromium's remote debugging is intentionally not enabled. Open a
            # new tab through xdotool only when available; otherwise the caller
            # must start a new session with the desired verification URL.
            xdotool = shutil.which("xdotool")
            if not xdotool:
                raise RuntimeError("browser URL cannot be changed after startup")
            env = os.environ.copy()
            env["DISPLAY"] = self.display
            subprocess.run(
                [
                    xdotool,
                    "search",
                    "--onlyvisible",
                    "--class",
                    "Chromium",
                    "windowactivate",
                    "--sync",
                    "key",
                    "ctrl+l",
                    "type",
                    url,
                    "key",
                    "Return",
                ],
                env=env,
                check=True,
                timeout=5,
            )
            self._session.url = url
            return self._session.serialize()


manager = AuthBrowserManager()
