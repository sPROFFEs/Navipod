from pathlib import Path

import update_service
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_all_compose_variants_include_isolated_downloader():
    compose_paths = [
        PROJECT_ROOT / "docker-compose.yaml",
        PROJECT_ROOT / "deployment-templates" / "internal" / "docker-compose.yaml",
        PROJECT_ROOT / "deployment-templates" / "domain" / "docker-compose.yaml",
    ]
    for compose_path in compose_paths:
        payload = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        service = payload["services"]["downloader"]
        assert "ports" not in service
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["cap_drop"] == ["ALL"]
        assert service["shm_size"] == "1gb"
        assert any("download-staging:/downloads" in volume for volume in service["volumes"])
        concierge = payload["services"]["concierge"]
        assert "downloader" in concierge["depends_on"]
        assert concierge["environment"]


def test_worker_image_installs_spotiflac_browser_runtime():
    dockerfile = (PROJECT_ROOT / "downloader-worker" / "Dockerfile").read_text(encoding="utf-8")

    assert "chromium" in dockerfile
    assert "xvfb" in dockerfile
    assert "command -v Xvfb" in dockerfile


def test_worker_entrypoint_prepares_persistent_auth_browser_volume():
    entrypoint = (PROJECT_ROOT / "downloader-worker" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "/home/downloader/.auth-browser" in entrypoint
    assert "chown -R downloader:downloaders /home/downloader/.auth-browser" in entrypoint


def test_auth_browser_proxy_allows_only_same_origin_framing():
    nginx = (PROJECT_ROOT / "nginx.conf").read_text(encoding="utf-8")
    authorization = nginx.split("location = /_internal/auth-browser-authorize {", 1)[1].split("}", 1)[0]
    location = nginx.split("location ^~ /admin/auth-browser/ {", 1)[1].split("}", 1)[0]

    assert "proxy_set_header Cookie $http_cookie;" in authorization
    assert "X-Auth-Browser-Session" not in authorization
    assert "X-Auth-Browser-Token" not in authorization
    assert 'add_header X-Frame-Options "SAMEORIGIN" always;' in location
    assert "add_header Content-Security-Policy \"frame-ancestors 'self'\" always;" in location


def test_updater_rebuilds_worker_for_worker_changes_and_keeps_updater_alive():
    selected, deferred = update_service._select_services_for_update(
        ["Navipod/downloader-worker/worker.py", "Navipod/concierge/downloader_worker_client.py"]
    )

    assert selected == ["concierge", "downloader"]
    assert deferred == ["updater"]


def test_regular_frontend_change_does_not_recreate_worker():
    selected, deferred = update_service._select_services_for_update(["Navipod/assets/css/style.css"])

    assert selected == ["concierge"]
    assert deferred == ["updater"]


def test_compose_update_forces_runtime_recreation_for_bind_mounted_source():
    assert update_service._build_compose_update_args(False, ["concierge"]) == [
        "up",
        "-d",
        "--force-recreate",
        "--remove-orphans",
        "concierge",
    ]
    assert update_service._build_compose_update_args(True, ["concierge", "downloader"]) == [
        "up",
        "-d",
        "--force-recreate",
        "--build",
        "--remove-orphans",
        "concierge",
        "downloader",
    ]


def test_unavailable_worker_does_not_fail_legacy_compatible_update_health(monkeypatch):
    class Response:
        status_code = 200

    def fake_get(url, **_kwargs):
        if url == "http://downloader:8081/health":
            raise OSError("worker is still building")
        return Response()

    monkeypatch.setattr(update_service.httpx, "get", fake_get)

    result = update_service._run_post_update_health_check(downloader_required=False)

    assert result["ok"] is True
    assert result["downloader"]["available"] is False


def test_worker_only_update_health_requires_downloader(monkeypatch):
    class Response:
        status_code = 200

    def fake_get(url, **_kwargs):
        if url == "http://downloader:8081/health":
            raise OSError("worker is down")
        return Response()

    monkeypatch.setattr(update_service, "HEALTH_CHECK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(update_service.httpx, "get", fake_get)
    monkeypatch.setattr(update_service.time, "sleep", lambda _seconds: None)

    result = update_service._run_post_update_health_check(downloader_required=True)

    assert result["ok"] is False
    assert "worker-only mode" in result["error"]


def test_stale_updater_runtime_is_restarted_once(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "ok"}

    restarted = []
    monkeypatch.setattr(update_service.httpx, "get", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(update_service, "_restart_updater_container", lambda: restarted.append(True))

    assert update_service.refresh_stale_updater_runtime() is True
    assert restarted == [True]


def test_current_updater_runtime_is_not_rebuilt(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "ok",
                "runtime_generation": update_service.UPDATER_RUNTIME_GENERATION,
                "source_current": True,
            }

    monkeypatch.setattr(update_service.httpx, "get", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(
        update_service,
        "_restart_updater_container",
        lambda: (_ for _ in ()).throw(AssertionError("current updater was rebuilt")),
    )

    assert update_service.refresh_stale_updater_runtime() is False
