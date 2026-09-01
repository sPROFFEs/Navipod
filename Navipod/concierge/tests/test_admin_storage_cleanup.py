import os
import time
from pathlib import Path

import storage_maintenance


def _set_age(path, seconds_ago):
    timestamp = time.time() - seconds_ago
    os.utime(path, (timestamp, timestamp))


def test_storage_cleanup_removes_only_stale_navipod_residue(tmp_path):
    staging = tmp_path / "staging" / "jobs"
    users = tmp_path / "users"
    stale_job = staging / "old-job"
    fresh_job = staging / "active-job"
    stale_job.mkdir(parents=True)
    fresh_job.mkdir(parents=True)
    stale_part = stale_job / "track.part"
    fresh_part = fresh_job / "track.part"
    stale_part.write_bytes(b"old")
    fresh_part.write_bytes(b"new")
    _set_age(stale_part, 48 * 60 * 60)

    stale_user_file = users / "alice" / "music" / "failed.tmp"
    protected_user_file = users / "bob" / "music" / "active.tmp"
    ordinary_file = users / "alice" / "music" / "song.mp3"
    stale_user_file.parent.mkdir(parents=True)
    protected_user_file.parent.mkdir(parents=True)
    stale_user_file.write_bytes(b"trash")
    protected_user_file.write_bytes(b"active")
    ordinary_file.write_bytes(b"music")
    _set_age(stale_user_file, 48 * 60 * 60)
    _set_age(protected_user_file, 48 * 60 * 60)

    result = storage_maintenance.purge_stale_storage(
        staging_roots=(staging,),
        users_root=users,
        protected_usernames={"bob"},
        protected_staging_names={"active-job"},
        minimum_age_seconds=24 * 60 * 60,
    )

    assert not stale_part.exists()
    assert fresh_part.exists()
    assert not stale_user_file.exists()
    assert protected_user_file.exists()
    assert ordinary_file.exists()
    assert result["files_removed"] == 2
    assert result["bytes_freed"] == len(b"old") + len(b"trash")


def test_storage_cleanup_keeps_stale_files_for_active_download_job(tmp_path):
    staging = tmp_path / "jobs"
    active_file = staging / "42" / "still-downloading.part"
    active_file.parent.mkdir(parents=True)
    active_file.write_bytes(b"active")
    _set_age(active_file, 48 * 60 * 60)

    result = storage_maintenance.purge_stale_storage(
        staging_roots=(staging,),
        users_root=tmp_path / "users",
        protected_staging_names={"42"},
        minimum_age_seconds=24 * 60 * 60,
    )

    assert active_file.exists()
    assert result["files_removed"] == 0


def test_storage_cleanup_never_targets_global_tmp():
    source = (Path(__file__).resolve().parents[1] / "routers" / "admin.py").read_text(encoding="utf-8")

    route_start = source.index('@router.post("/system/purge-storage")')
    route_end = source.index("\n\n@router.", route_start)
    route = source[route_start:route_end]
    assert 'paths_to_clean = ["/tmp"' not in route
    assert "background_tasks.add_task(_run_storage_purge_job" in route
