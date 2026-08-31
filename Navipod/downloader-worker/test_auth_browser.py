import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("navipod_auth_browser", Path(__file__).with_name("auth_browser.py"))
module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules["navipod_auth_browser"] = module
SPEC.loader.exec_module(module)


def test_auth_browser_starts_idle_and_reports_enabled():
    manager = module.AuthBrowserManager()
    payload = manager.status()
    assert payload["status"] == "idle"
    assert "enabled" in payload


def test_auth_browser_rejects_local_verification_targets():
    manager = module.AuthBrowserManager()
    with pytest.raises(ValueError):
        manager._validate_url("http://127.0.0.1:8080/verification")


def test_auth_browser_rejects_non_http_urls():
    manager = module.AuthBrowserManager()
    with pytest.raises(ValueError):
        manager._validate_url("file:///tmp/provider.html")


def test_chromium_uses_container_safe_real_browser_defaults(tmp_path):
    manager = module.AuthBrowserManager()
    command = manager._chromium_command("/usr/bin/chromium", tmp_path, "https://provider.example/verify")
    assert "--no-sandbox" in command
    assert "--disable-gpu" not in command
    assert "--disable-dev-shm-usage" not in command
    assert "--disable-extensions" not in command
    assert "--window-size=1440,900" in command


def test_auth_browser_removes_stale_container_profile_locks(tmp_path, monkeypatch):
    manager = module.AuthBrowserManager()
    profile = tmp_path / "chromium"
    profile.mkdir()
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        (profile / name).symlink_to(f"stale-{name}")
    monkeypatch.setattr(manager, "_profile_in_use", lambda _profile: False)

    manager._clear_stale_profile_locks(profile)

    assert not any((profile / name).exists() or (profile / name).is_symlink() for name in manager_profile_locks())


def test_auth_browser_keeps_profile_locks_when_chromium_is_running(tmp_path, monkeypatch):
    manager = module.AuthBrowserManager()
    profile = tmp_path / "chromium"
    profile.mkdir()
    lock = profile / "SingletonLock"
    lock.symlink_to("live-lock")
    monkeypatch.setattr(manager, "_profile_in_use", lambda _profile: True)

    with pytest.raises(RuntimeError, match="profile is still in use"):
        manager._clear_stale_profile_locks(profile)

    assert lock.is_symlink()


def manager_profile_locks():
    return ("SingletonLock", "SingletonCookie", "SingletonSocket")


def test_component_startup_error_names_process_and_redacts_urls(tmp_path):
    log_path = tmp_path / "chromium.log"
    log_path.write_text("failed while opening https://provider.example/secret?id=abc", encoding="utf-8")
    managed = module.BrowserProcess(name="chromium", process=SimpleNamespace(), log_path=log_path)
    error = module.AuthBrowserManager()._component_error(managed)
    assert error.startswith("chromium exited during startup")
    assert "provider.example" not in error
    assert "[redacted-url]" in error
