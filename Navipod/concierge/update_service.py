from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

import database
import ops_core as ops
from build_info_service import get_build_info
from job_service import acquire_lock, create_admin_job, get_active_operation_lock, get_admin_job, get_recent_admin_jobs, release_lock, update_admin_job_progress

COMPOSE_UPDATE_TIMEOUT_SECONDS = 1000
DOCKER_PRUNE_TIMEOUT_SECONDS = 120
HEALTH_CHECK_TIMEOUT_SECONDS = 45


def get_internal_updater_token():
    return hashlib.sha256(f"navipod-updater:{ops.settings.SECRET_KEY}".encode("utf-8")).hexdigest()


def _truncate_log_text(value, limit: int = 700):
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value or "")
    text = text.strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _log_command_result(job_id: int, label: str, result, *, include_command_on_success: bool = False, include_output_on_success: bool = False):
    if isinstance(result, dict):
        ok = bool(result.get("ok"))
        command = result.get("command")
        stdout = _truncate_log_text(result.get("stdout"))
        stderr = _truncate_log_text(result.get("stderr"))
        returncode = result.get("returncode")
    else:
        ok = getattr(result, "returncode", 1) == 0
        command = None
        stdout = _truncate_log_text(getattr(result, "stdout", ""))
        stderr = _truncate_log_text(getattr(result, "stderr", ""))
        returncode = getattr(result, "returncode", None)

    status_label = "succeeded" if ok else "failed"
    suffix = f" (exit {returncode})" if returncode is not None else ""
    update_admin_job_progress(job_id, message=f"{label} {status_label}{suffix}")
    if command and (not ok or include_command_on_success):
        update_admin_job_progress(job_id, message=f"{label} command: {command}")
    if stdout and (not ok or include_output_on_success):
        update_admin_job_progress(job_id, message=f"{label} stdout: {stdout}")
    if stderr and (not ok or include_output_on_success):
        update_admin_job_progress(job_id, message=f"{label} stderr: {stderr}")


def _serialize_update_state_payload(payload):
    payload = payload or {}
    return json.dumps(payload, ensure_ascii=False)


def save_update_state(db, payload):
    settings_row = ops.ensure_system_settings_record(db)
    settings_row.update_state_json = _serialize_update_state_payload(payload)
    db.commit()


def get_update_state(db):
    settings_row = ops.ensure_system_settings_record(db)
    state = {}
    if settings_row.update_state_json:
        try:
            state = json.loads(settings_row.update_state_json)
        except Exception:
            state = {}
    current = state.get("current") or get_build_info()
    state.setdefault("current", current)
    state.setdefault("source_repo_url", ops.settings.UPDATE_SOURCE_REPO_URL)
    state.setdefault("source_branch", ops.settings.UPDATE_SOURCE_BRANCH)
    state.setdefault("message", "No update check performed yet")
    state.setdefault("status", "idle")
    state.setdefault("behind_count", 0)
    state.setdefault("ahead_count", 0)
    state.setdefault("update_available", False)
    state.setdefault("pending_commits", [])
    state.setdefault("remote", {"commit": "unknown", "full_commit": "unknown"})
    return state


def _parse_state_checked_at(payload: dict):
    checked_at = (payload or {}).get("checked_at")
    if not checked_at:
        return None
    try:
        parsed = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def is_update_state_stale(db, max_age_hours: int = 24):
    state = get_update_state(db)
    checked_at = _parse_state_checked_at(state)
    if not checked_at:
        return True
    return (ops.utcnow() - checked_at).total_seconds() >= (max_age_hours * 3600)


def _run_git(args, *, check=True, fallback=None, include_details=False):
    return ops._run_git(args, check=check, fallback=fallback, include_details=include_details)


def _fetch_update_tracking_ref():
    return _run_git(["fetch", "--prune", "--no-tags", "--no-write-fetch-head", ops.settings.UPDATE_SOURCE_REPO_URL, f"+refs/heads/{ops.settings.UPDATE_SOURCE_BRANCH}:{ops.UPDATE_TRACKING_REMOTE}"], fallback="", include_details=True)


def _get_remote_branch_sha_via_ls_remote():
    result = _run_git(["ls-remote", ops.settings.UPDATE_SOURCE_REPO_URL, f"refs/heads/{ops.settings.UPDATE_SOURCE_BRANCH}"], fallback="", include_details=True)
    if not isinstance(result, dict) or not result.get("ok"):
        return None, result
    stdout = result.get("stdout") or ""
    first_line = stdout.splitlines()[0].strip() if stdout else ""
    if not first_line:
        return None, result
    remote_sha = first_line.split()[0].strip()
    if not remote_sha:
        return None, result
    return remote_sha, result


def _parse_github_repo_slug(repo_url: str):
    parsed = urlparse(repo_url)
    path = (parsed.path or "").strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return None


async def _get_remote_release_version():
    repo_slug = _parse_github_repo_slug(ops.settings.UPDATE_SOURCE_REPO_URL)
    if not repo_slug:
        return None
    url = f"https://raw.githubusercontent.com/{repo_slug}/{ops.settings.UPDATE_SOURCE_BRANCH}/VERSION"
    try:
        response = httpx.get(url, timeout=10.0, follow_redirects=True)
        if response.status_code >= 400:
            return None
        raw = response.text.strip()
        if not raw:
            return None
        return raw if raw.startswith("v") else f"v{raw}"
    except Exception:
        return None


def _build_remote_version_display(current: dict, remote_release_version: str | None, behind_count: int, remote_commit: str):
    release_version = remote_release_version or current.get("release_version") or "v0.0.0"
    local_revision = current.get("revision")
    try:
        local_revision_num = int(local_revision)
    except Exception:
        local_revision_num = None
    if local_revision_num is not None:
        remote_revision = local_revision_num + int(max(behind_count, 0))
        return f"{release_version}+r{remote_revision}"
    return f"{release_version} ({remote_commit})"


def _get_update_details_via_fetch(local_full_commit: str, current: dict):
    remote_release_version = None
    fetch_result = _fetch_update_tracking_ref()
    if not _run_git(["rev-parse", "--verify", ops.UPDATE_TRACKING_REMOTE], fallback=None):
        return None, fetch_result

    remote_commit = _run_git(["rev-parse", "--short", ops.UPDATE_TRACKING_REMOTE], fallback="unknown")
    remote_full_commit = _run_git(["rev-parse", ops.UPDATE_TRACKING_REMOTE], fallback="unknown")
    counts = _run_git(["rev-list", "--left-right", "--count", f"HEAD...{ops.UPDATE_TRACKING_REMOTE}"], fallback="0	0")
    ahead_count = 0
    behind_count = 0
    try:
        ahead_str, behind_str = counts.split()
        ahead_count = int(ahead_str)
        behind_count = int(behind_str)
    except Exception:
        pass
    pending_commits_raw = _run_git(["log", "--oneline", f"HEAD..{ops.UPDATE_TRACKING_REMOTE}", "-n", "10"], fallback="")
    pending_commits = [line.strip() for line in pending_commits_raw.splitlines() if line.strip()]
    payload = {
        "checked_at": ops.utcnow().isoformat(),
        "status": "ok",
        "message": "Update check completed",
        "source_repo_url": ops.settings.UPDATE_SOURCE_REPO_URL,
        "source_branch": ops.settings.UPDATE_SOURCE_BRANCH,
        "current": {**current, "full_commit": local_full_commit, "dirty": _get_worktree_dirty()},
        "remote": {
            "commit": remote_commit,
            "full_commit": remote_full_commit,
            "release_version": remote_release_version,
            "version": _build_remote_version_display(current, remote_release_version, behind_count, remote_commit),
        },
        "ahead_count": ahead_count,
        "behind_count": behind_count,
        "update_available": behind_count > 0,
        "pending_commits": pending_commits,
        "fetch_result": fetch_result,
    }
    return payload, fetch_result


def _get_worktree_dirty():
    status = _run_git(["status", "--porcelain", "--untracked-files=no", "--", ".", ":(exclude).env", ":(exclude)Navipod/.env"], fallback="")
    return bool(status and status.strip())


async def _get_update_check_payload():
    current = get_build_info()
    local_full_commit = _run_git(["rev-parse", "HEAD"], fallback="unknown")
    remote_release_version = await _get_remote_release_version()
    remote_sha, ls_remote_result = _get_remote_branch_sha_via_ls_remote()
    if remote_sha:
        remote_short = remote_sha[:7]
        update_available = remote_sha != local_full_commit
        behind_count = 1 if update_available else 0
        payload = {
            "checked_at": ops.utcnow().isoformat(),
            "status": "ok",
            "message": "Update check completed",
            "source_repo_url": ops.settings.UPDATE_SOURCE_REPO_URL,
            "source_branch": ops.settings.UPDATE_SOURCE_BRANCH,
            "current": {**current, "full_commit": local_full_commit, "dirty": _get_worktree_dirty()},
            "remote": {
                "commit": remote_short,
                "full_commit": remote_sha,
                "release_version": remote_release_version,
                "version": _build_remote_version_display(current, remote_release_version, behind_count, remote_short),
            },
            "ahead_count": 0,
            "behind_count": behind_count,
            "update_available": update_available,
            "pending_commits": [],
            "fetch_result": ls_remote_result,
        }
        return payload

    fetch_result = _fetch_update_tracking_ref()
    if not _run_git(["rev-parse", "--verify", ops.UPDATE_TRACKING_REMOTE], fallback=None):
        fetch_error = ""
        fetch_stdout = ""
        fetch_returncode = None
        if isinstance(ls_remote_result, dict) and not ls_remote_result.get("ok"):
            fetch_error = ls_remote_result.get("stderr") or ls_remote_result.get("stdout") or ""
            fetch_returncode = ls_remote_result.get("returncode")
        elif isinstance(fetch_result, dict):
            fetch_error = fetch_result.get("stderr") or ""
            fetch_stdout = fetch_result.get("stdout") or ""
            fetch_returncode = fetch_result.get("returncode")
        error_message = "Failed to fetch update reference from GitHub main"
        if fetch_error:
            error_message = f"{error_message}: {fetch_error}"
        return {
            "checked_at": ops.utcnow().isoformat(),
            "status": "error",
            "message": error_message,
            "source_repo_url": ops.settings.UPDATE_SOURCE_REPO_URL,
            "source_branch": ops.settings.UPDATE_SOURCE_BRANCH,
            "current": {**current, "full_commit": local_full_commit, "dirty": _get_worktree_dirty()},
            "remote": {"commit": "unknown", "full_commit": "unknown"},
            "ahead_count": 0,
            "behind_count": 0,
            "update_available": False,
            "pending_commits": [],
            "fetch_result": fetch_result,
            "fetch_stdout": fetch_stdout,
            "fetch_returncode": fetch_returncode,
        }

    payload, _ = _get_update_details_via_fetch(local_full_commit, current)
    return payload


def _resolve_target_update_sha():
    remote_sha, ls_remote_result = _get_remote_branch_sha_via_ls_remote()
    if remote_sha:
        return remote_sha, ls_remote_result
    fetch_result = _fetch_update_tracking_ref()
    fetched_sha = _run_git(["rev-parse", ops.UPDATE_TRACKING_REMOTE], fallback=None)
    return fetched_sha, fetch_result


def _fetch_target_update_ref(target_sha: str | None):
    if not target_sha:
        return None
    return _run_git(["fetch", "--prune", "--no-tags", "--no-write-fetch-head", ops.settings.UPDATE_SOURCE_REPO_URL, f"+refs/heads/{ops.settings.UPDATE_SOURCE_BRANCH}:{ops.UPDATE_TRACKING_REMOTE}"], fallback=None, include_details=True)


def _run_post_update_health_check():
    urls = ["http://concierge:8000/login", "http://nginx/login"]
    timeout_seconds = HEALTH_CHECK_TIMEOUT_SECONDS
    deadline = ops.utcnow().timestamp() + timeout_seconds
    last_error = "health check did not start"

    while ops.utcnow().timestamp() < deadline:
        for url in urls:
            try:
                response = httpx.get(url, timeout=5.0, follow_redirects=True)
                if response.status_code < 500:
                    return {"ok": True, "url": url, "status_code": response.status_code}
                last_error = f"{url} returned {response.status_code}"
            except Exception as e:
                last_error = str(e)
        time.sleep(2)

    return {"ok": False, "error": last_error}


def _run_check_update_job(job_id: int, triggered_by: str | None):
    db = database.SessionLocal()
    try:
        if not acquire_lock(db, job_id):
            update_admin_job_progress(job_id, message="Another admin operation is already running", status="failed", phase="error", progress=100, finished=True)
            return
        update_admin_job_progress(job_id, message="Checking GitHub main for updates", status="running", phase="check", progress=25)
        payload = asyncio.run(_get_update_check_payload())
        save_update_state(db, payload)
        if payload.get("status") != "ok":
            message = payload.get("message") or "Update check failed"
            status = "failed"
        elif payload.get("update_available"):
            message = f"Update available: {payload['behind_count']} commit(s) behind"
            status = "completed"
        else:
            message = "Already up to date"
            status = "completed"
        update_admin_job_progress(job_id, message=message, status=status, phase="done", progress=100, extra=payload, finished=True)
    except Exception as e:
        update_admin_job_progress(job_id, message=f"Update check failed: {e}", status="failed", phase="error", progress=100, finished=True)
    finally:
        try:
            release_lock(db)
        finally:
            db.close()


def _run_compose_update(job_id: int, changed_files: list[str]):
    services = [svc.strip() for svc in ops.settings.UPDATE_MANAGED_SERVICES.split() if svc.strip()]
    rebuild_required = any(path in ops.REBUILD_REQUIRED_PATHS for path in changed_files)
    compose_args = ["up", "-d"]
    compose_phase = "build" if rebuild_required else "recreate"
    compose_progress = 80 if rebuild_required else 85
    compose_message = (
        f"Building updated images and recreating services: {' '.join(services)}"
        if rebuild_required
        else f"Recreating services without image rebuild: {' '.join(services)}"
    )
    if rebuild_required:
        compose_args.append("--build")
    compose_args.extend(["--remove-orphans", *services])
    update_admin_job_progress(
        job_id,
        message=compose_message,
        phase=compose_phase,
        progress=compose_progress,
        status="running",
        extra={"changed_files": changed_files[:100], "rebuild_required": rebuild_required},
    )
    completed = ops._run_compose_command(
        compose_args,
        check=False,
        timeout_seconds=COMPOSE_UPDATE_TIMEOUT_SECONDS,
    )
    _log_command_result(job_id, "Compose update", completed)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "docker compose up failed").strip())

    update_admin_job_progress(job_id, message="Services restarted. Waiting for health check", phase="health", progress=90, status="running")
    health_result = _run_post_update_health_check()
    if health_result.get("ok"):
        update_admin_job_progress(job_id, message="Health check passed", phase="cleanup", progress=95, status="running")
    else:
        update_admin_job_progress(job_id, message=f"Health check failed: {health_result.get('error', 'unknown error')}", phase="health", progress=95, status="running")
        raise RuntimeError(f"health check failed: {health_result.get('error', 'unknown error')}")

    update_admin_job_progress(job_id, message="Cleaning Docker cache", phase="cleanup", progress=97, status="running")
    try:
        image_prune = subprocess.run(
            ["docker", "image", "prune", "-f"],
            capture_output=True,
            text=True,
            check=False,
            timeout=DOCKER_PRUNE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        stdout_text = e.stdout.decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        image_prune = subprocess.CompletedProcess(
            e.cmd,
            124,
            stdout=stdout_text,
            stderr=f"Command timed out after {DOCKER_PRUNE_TIMEOUT_SECONDS}s",
        )
    try:
        builder_prune = subprocess.run(
            ["docker", "builder", "prune", "-f"],
            capture_output=True,
            text=True,
            check=False,
            timeout=DOCKER_PRUNE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        stdout_text = e.stdout.decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        builder_prune = subprocess.CompletedProcess(
            e.cmd,
            124,
            stdout=stdout_text,
            stderr=f"Command timed out after {DOCKER_PRUNE_TIMEOUT_SECONDS}s",
        )
    _log_command_result(job_id, "Docker image prune", image_prune)
    _log_command_result(job_id, "Docker builder prune", builder_prune)
    return rebuild_required, health_result


def run_apply_update_job_from_updater(job_id: int, triggered_by: str | None):
    db = database.SessionLocal()
    temp_path = None
    try:
        if not acquire_lock(db, job_id):
            update_admin_job_progress(job_id, message="Another admin operation is already running", status="failed", progress=100, finished=True)
            return

        if _get_worktree_dirty():
            update_admin_job_progress(job_id, message="Repository has local tracked changes. Refusing to apply update.", status="failed", phase="preflight", progress=100, extra={"dirty": True}, finished=True)
            return

        update_admin_job_progress(job_id, message="Checking GitHub main for updates", status="running", phase="check", progress=10)
        payload = asyncio.run(_get_update_check_payload())
        save_update_state(db, payload)
        if payload.get("status") != "ok":
            update_admin_job_progress(job_id, message=payload.get("message") or "Update check failed", status="failed", phase="check", progress=100, extra=payload, finished=True)
            return
        if not payload.get("update_available"):
            update_admin_job_progress(job_id, message="Already up to date", status="completed", phase="done", progress=100, extra=payload, finished=True)
            return

        target_sha, target_result = _resolve_target_update_sha()
        if not target_sha:
            update_admin_job_progress(job_id, message="Failed to resolve remote update target", status="failed", phase="check", progress=100, extra={"precheck": payload, "target_result": target_result}, finished=True)
            return

        from backup_service import _build_backup_manifest, _rotate_current_to_previous, _upsert_backup_artifact, _write_backup_zip

        update_admin_job_progress(job_id, message="Creating pre-update backup", status="running", phase="backup", progress=25)
        ops.ensure_runtime_dirs()
        manifest = _build_backup_manifest(triggered_by, "pre-update")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir=ops.BACKUP_ROOT) as tmp:
            temp_path = Path(tmp.name)
        database.engine.dispose()
        _write_backup_zip(temp_path, manifest)
        _rotate_current_to_previous(db)
        current_path = ops.BACKUP_ROOT / ops.CURRENT_BACKUP_NAME
        shutil.move(str(temp_path), current_path)
        temp_path = None
        _upsert_backup_artifact(db, "current", filename=ops.CURRENT_BACKUP_NAME, file_path=str(current_path), size_bytes=current_path.stat().st_size, created_at=ops.utcnow(), manifest=manifest)

        update_admin_job_progress(job_id, message="Fetching target revision from remote", status="running", phase="fetch", progress=40)
        fetch_result = _fetch_target_update_ref(target_sha)
        _log_command_result(job_id, "Git fetch", fetch_result)
        if not fetch_result or not fetch_result.get("ok"):
            raise RuntimeError((fetch_result or {}).get("stderr") or "git fetch failed")

        changed_files_raw = _run_git(["diff", "--name-only", f"HEAD..{target_sha}"], fallback="") or ""
        changed_files = [line.strip() for line in changed_files_raw.splitlines() if line.strip()]

        update_admin_job_progress(job_id, message="Applying Git update to workspace", status="running", phase="workspace", progress=50)
        reset_result = _run_git(["reset", "--hard", target_sha], fallback=None, include_details=True)
        _log_command_result(job_id, "Git reset", reset_result)
        if not reset_result or not reset_result.get("ok"):
            raise RuntimeError("git reset --hard failed")

        update_admin_job_progress(job_id, message="Running schema migrations", status="running", phase="migrate", progress=70)
        applied_migrations = ops.apply_schema_migrations()
        if applied_migrations:
            update_admin_job_progress(job_id, message=f"Migrations applied: {', '.join(applied_migrations)}")
        else:
            update_admin_job_progress(job_id, message="No schema migrations needed")
        rebuild_required, health_result = _run_compose_update(job_id, changed_files)

        post_payload = asyncio.run(_get_update_check_payload())
        save_update_state(db, post_payload)
        details = {"before": payload, "after": post_payload, "changed_files": changed_files[:100], "rebuild_required": rebuild_required, "applied_migrations": applied_migrations, "health_check": health_result, "target_sha": target_sha}
        update_admin_job_progress(job_id, message="Update applied and services recreated successfully", status="completed", phase="done", progress=100, extra=details, finished=True)
    except Exception as e:
        update_admin_job_progress(job_id, message=f"Apply update failed: {e}", status="failed", phase="error", progress=100, finished=True)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        try:
            release_lock(db)
        finally:
            db.close()


def queue_check_update(triggered_by: str | None):
    job_id = create_admin_job("check_update", triggered_by, "Update check queued", {"source_branch": ops.settings.UPDATE_SOURCE_BRANCH})
    asyncio.create_task(asyncio.to_thread(_run_check_update_job, job_id, triggered_by))
    return job_id


def queue_apply_update(triggered_by: str | None):
    job_id = create_admin_job("apply_update", triggered_by, "Apply update queued", {"source_branch": ops.settings.UPDATE_SOURCE_BRANCH, "progress": 0, "phase": "queued", "logs": [{"at": ops.utcnow().isoformat(), "message": "Apply update queued"}]})
    try:
        response = httpx.post(ops.UPDATER_INTERNAL_URL, headers={"Authorization": f"Bearer {get_internal_updater_token()}"}, json={"job_id": job_id, "triggered_by": triggered_by}, timeout=10.0)
        response.raise_for_status()
    except Exception as e:
        update_admin_job_progress(job_id, message=f"Failed to contact internal updater: {e}", status="failed", phase="error", progress=100, finished=True)
    return job_id


def run_silent_update_refresh():
    db = database.SessionLocal()
    try:
        if not acquire_lock(db, None):
            return False
        payload = asyncio.run(_get_update_check_payload())
        save_update_state(db, payload)
        return True
    except Exception:
        return False
    finally:
        try:
            release_lock(db)
        except Exception:
            pass
        db.close()


def queue_silent_update_refresh_if_stale(max_age_hours: int = 24):
    db = database.SessionLocal()
    try:
        if not is_update_state_stale(db, max_age_hours=max_age_hours):
            return False
        if get_active_operation_lock(db):
            return False
    finally:
        db.close()
    asyncio.create_task(asyncio.to_thread(run_silent_update_refresh))
    return True
