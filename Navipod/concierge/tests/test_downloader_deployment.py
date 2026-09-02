import subprocess
from pathlib import Path

import pytest
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


def test_compose_change_keeps_tunnel_running():
    selected, deferred = update_service._select_services_for_update(["Navipod/docker-compose.yaml"])

    assert selected == ["concierge", "downloader", "nginx"]
    assert "tunnel" not in selected
    assert deferred == ["updater"]


def test_tunnel_uses_dynamic_network_address():
    payload = yaml.safe_load((PROJECT_ROOT / "docker-compose.yaml").read_text(encoding="utf-8"))

    assert payload["services"]["tunnel"]["networks"] == ["navidrome-net"]


def test_host_bind_compose_uses_inspected_concierge_mount_and_preserves_named_volumes(monkeypatch):
    repo_root = PROJECT_ROOT.parent
    host_repo_root = Path("/srv/navipod")
    host_concierge_root = host_repo_root / "Navipod" / "concierge"

    monkeypatch.setattr(update_service.ops, "REPO_ROOT", repo_root)
    monkeypatch.setattr(update_service.ops, "COMPOSE_PROJECT_ROOT", PROJECT_ROOT)

    def fake_mount_source(destination):
        if destination == repo_root:
            return host_repo_root
        if destination == Path("/app"):
            return host_concierge_root
        return None

    monkeypatch.setattr(update_service.ops, "_get_container_mount_source", fake_mount_source)
    generated = update_service.ops._build_host_bind_compose_file()
    try:
        payload = yaml.safe_load(generated.read_text(encoding="utf-8"))
    finally:
        generated.unlink(missing_ok=True)

    concierge_volumes = payload["services"]["concierge"]["volumes"]
    downloader_volumes = payload["services"]["downloader"]["volumes"]
    assert f"{host_concierge_root.as_posix()}:/app" in concierge_volumes
    assert f"{host_concierge_root.as_posix()}/templates:/app/templates" in concierge_volumes
    assert "downloader-state:/home/downloader/.spotiflac" in downloader_volumes


def test_mount_discovery_falls_back_to_current_container_id_when_configured_name_is_stale(monkeypatch):
    inspect_targets = []
    monkeypatch.setenv("SELF_CONTAINER_NAME", "navipod_updater")
    monkeypatch.setenv("HOSTNAME", "runtime-container-id")

    def fake_run(args, **_kwargs):
        inspect_targets.append(args[-1])
        if args[-1] == "runtime-container-id":
            return subprocess.CompletedProcess(
                args,
                0,
                stdout='[{"Type":"bind","Source":"/srv/navipod","Destination":"/workspace"}]\n',
                stderr="",
            )
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="No such container")

    monkeypatch.setattr(update_service.ops.subprocess, "run", fake_run)

    source = update_service.ops._get_container_mount_source(Path("/workspace"))

    assert source == Path("/srv/navipod")
    assert inspect_targets == ["navipod_updater", "runtime-container-id"]


def test_container_compose_recreate_fails_closed_when_host_mounts_cannot_be_resolved(monkeypatch):
    monkeypatch.setenv("SELF_CONTAINER_NAME", "navipod_updater")
    monkeypatch.setattr(update_service.ops, "_build_host_bind_compose_file", lambda: None)

    with pytest.raises(RuntimeError, match="refusing an unsafe Compose recreate"):
        update_service.ops._run_compose_command(["up", "-d", "concierge"])


def test_stale_compose_container_cleanup_uses_service_label(monkeypatch):
    calls = []

    def fake_docker(args, **_kwargs):
        calls.append(args)
        if args[0] == "ps":
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=(
                    "abc123\tnavipod-tunnel-1\tExited (1)\ttunnel\n"
                    "def456\tnavipod-concierge-1\tUp 1 minute\tconcierge\n"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(update_service.ops, "_run_docker_command", fake_docker)

    removed = update_service.ops.cleanup_stale_recreate_containers(["tunnel", "concierge"])

    assert removed == ["navipod-tunnel-1"]
    assert ["rm", "-f", "abc123"] in calls


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
