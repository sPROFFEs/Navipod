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
        updater = payload["services"]["updater"]
        assert updater["build"]["context"] == "./concierge"
        assert updater["build"]["dockerfile"] == "Dockerfile.updater"


def test_worker_image_installs_spotiflac_browser_runtime():
    dockerfile = (PROJECT_ROOT / "downloader-worker" / "Dockerfile").read_text(encoding="utf-8")

    assert "chromium" in dockerfile
    assert "xvfb" in dockerfile
    assert "command -v Xvfb" in dockerfile


def test_worker_image_uses_pinned_official_deno_binary():
    dockerfile = (PROJECT_ROOT / "downloader-worker" / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG DENO_VERSION=" in dockerfile
    assert "FROM docker.io/denoland/deno:bin-${DENO_VERSION} AS deno" in dockerfile
    assert "COPY --from=deno /deno /usr/local/bin/deno" in dockerfile
    assert "https://deno.land/install.sh" not in dockerfile


def test_concierge_image_uses_pinned_deno_and_updater_has_minimal_system_runtime():
    concierge = (PROJECT_ROOT / "concierge" / "Dockerfile").read_text(encoding="utf-8")
    updater = (PROJECT_ROOT / "concierge" / "Dockerfile.updater").read_text(encoding="utf-8")

    assert "FROM docker.io/denoland/deno:bin-${DENO_VERSION} AS deno" in concierge
    assert "https://deno.land/install.sh" not in concierge
    assert "--no-install-recommends" in concierge
    assert "--no-install-recommends" in updater
    assert "ffmpeg" not in updater
    assert "nodejs" not in updater
    assert "deno" not in updater.lower()


def test_worker_entrypoint_prepares_persistent_auth_browser_volume():
    entrypoint = (PROJECT_ROOT / "downloader-worker" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "/home/downloader/.auth-browser" in entrypoint
    assert "chown -R downloader:downloaders /home/downloader/.auth-browser" in entrypoint


def test_auth_browser_proxy_supports_cookie_and_legacy_query_handoff():
    nginx = (PROJECT_ROOT / "nginx.conf").read_text(encoding="utf-8")
    authorization = nginx.split("location = /_internal/auth-browser-authorize {", 1)[1].split("}", 1)[0]
    location = nginx.split("location ^~ /admin/auth-browser/ {", 1)[1].split("}", 1)[0]

    assert "proxy_set_header Cookie $http_cookie;" in authorization
    assert "proxy_set_header X-Auth-Browser-Session $auth_browser_session;" in authorization
    assert "proxy_set_header X-Auth-Browser-Token $auth_browser_token;" in authorization
    assert "set $auth_browser_session $arg_session_id;" in location
    assert "set $auth_browser_token $arg_token;" in location
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
        "--no-deps",
        "--remove-orphans",
        "concierge",
    ]
    assert update_service._build_compose_update_args(True, ["concierge", "downloader"]) == [
        "up",
        "-d",
        "--force-recreate",
        "--build",
        "--no-deps",
        "--remove-orphans",
        "concierge",
        "downloader",
    ]


def test_bind_mounted_runtime_changes_do_not_trigger_image_rebuild():
    changed_files = ["Navipod/nginx.conf", "Navipod/concierge/auth_browser.py"]

    assert update_service.ops.should_rebuild_for_changed_files(changed_files) is False
    assert update_service.ops.matched_rebuild_targets(changed_files) == []


def test_worker_image_changes_still_trigger_image_rebuild():
    changed_files = ["Navipod/downloader-worker/Dockerfile", "Navipod/downloader-worker/worker.py"]

    assert update_service.ops.should_rebuild_for_changed_files(changed_files) is True
    assert update_service.ops.matched_rebuild_targets(changed_files) == [
        "downloader-worker/Dockerfile",
        "downloader-worker/worker.py",
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
