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


def test_chromium_uses_container_safe_mode_by_default(tmp_path):
    manager = module.AuthBrowserManager()
    command = manager._chromium_command("/usr/bin/chromium", tmp_path, "https://provider.example/verify")
    assert "--no-sandbox" in command
    assert "--disable-gpu" in command


def test_component_startup_error_names_process_and_redacts_urls(tmp_path):
    log_path = tmp_path / "chromium.log"
    log_path.write_text("failed while opening https://provider.example/secret?id=abc", encoding="utf-8")
    managed = module.BrowserProcess(name="chromium", process=SimpleNamespace(), log_path=log_path)
    error = module.AuthBrowserManager()._component_error(managed)
    assert error.startswith("chromium exited during startup")
    assert "provider.example" not in error
    assert "[redacted-url]" in error
