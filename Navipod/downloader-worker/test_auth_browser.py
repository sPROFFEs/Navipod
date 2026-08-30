import importlib.util
import sys
from pathlib import Path

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
