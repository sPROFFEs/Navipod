"""Bounded cleanup for Navipod-owned download residue."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

TRASH_EXTENSIONS = {".part", ".ytdl", ".tmp", ".cache"}
DEFAULT_MINIMUM_AGE_SECONDS = 24 * 60 * 60


def _stale_file(path: Path, cutoff: float) -> bool:
    try:
        return not path.is_symlink() and path.is_file() and path.stat().st_mtime <= cutoff
    except OSError:
        return False


def _stale_tree(path: Path, cutoff: float) -> bool:
    try:
        for root, dirs, files in os.walk(path, followlinks=False):
            root_path = Path(root)
            dirs[:] = [name for name in dirs if not (root_path / name).is_symlink()]
            for name in files:
                if not _stale_file(root_path / name, cutoff):
                    return False
        return True
    except OSError:
        return False


def _remove_empty_directories(root: Path) -> int:
    removed = 0
    if not root.exists():
        return removed
    for current, _dirs, _files in os.walk(root, topdown=False, followlinks=False):
        path = Path(current)
        if path == root or path.is_symlink():
            continue
        try:
            path.rmdir()
            removed += 1
        except OSError:
            pass
    return removed


def purge_stale_storage(
    *,
    staging_roots: tuple[Path, ...],
    users_root: Path,
    protected_usernames: set[str] | None = None,
    protected_staging_names: set[str] | None = None,
    minimum_age_seconds: int = DEFAULT_MINIMUM_AGE_SECONDS,
    now_timestamp: float | None = None,
) -> dict[str, int]:
    """Remove old Navipod residue without touching global temp or active users."""
    cutoff = (now_timestamp if now_timestamp is not None else time.time()) - max(60, minimum_age_seconds)
    protected = {name for name in (protected_usernames or set()) if name}
    protected_staging = {name for name in (protected_staging_names or set()) if name}
    bytes_freed = 0
    files_removed = 0
    directories_removed = 0

    for staging_root in staging_roots:
        if not staging_root.exists():
            continue
        for current, dirs, files in os.walk(staging_root, followlinks=False):
            current_path = Path(current)
            try:
                relative_parts = current_path.relative_to(staging_root).parts
            except ValueError:
                continue
            if relative_parts and relative_parts[0] in protected_staging:
                dirs.clear()
                continue
            dirs[:] = [name for name in dirs if not (current_path / name).is_symlink()]
            if current_path == staging_root:
                dirs[:] = [name for name in dirs if name not in protected_staging]
            for name in files:
                path = current_path / name
                if not _stale_file(path, cutoff):
                    continue
                try:
                    size = path.stat().st_size
                    path.unlink()
                    bytes_freed += size
                    files_removed += 1
                except OSError:
                    continue
        directories_removed += _remove_empty_directories(staging_root)

    if users_root.exists():
        for current, dirs, files in os.walk(users_root, followlinks=False):
            current_path = Path(current)
            try:
                relative_parts = current_path.relative_to(users_root).parts
            except ValueError:
                continue
            if relative_parts and relative_parts[0] in protected:
                dirs.clear()
                continue

            dirs[:] = [name for name in dirs if not (current_path / name).is_symlink()]
            for name in files:
                path = current_path / name
                if path.suffix.lower() not in TRASH_EXTENSIONS or not _stale_file(path, cutoff):
                    continue
                try:
                    size = path.stat().st_size
                    path.unlink()
                    bytes_freed += size
                    files_removed += 1
                except OSError:
                    continue

            for name in list(dirs):
                if name != ".spotdl-cache":
                    continue
                cache_path = current_path / name
                if not _stale_tree(cache_path, cutoff):
                    continue
                cache_bytes = 0
                for cache_root, _cache_dirs, cache_files in os.walk(cache_path, followlinks=False):
                    for cache_file in cache_files:
                        try:
                            cache_bytes += (Path(cache_root) / cache_file).stat().st_size
                        except OSError:
                            pass
                try:
                    shutil.rmtree(cache_path)
                    dirs.remove(name)
                    bytes_freed += cache_bytes
                    directories_removed += 1
                except OSError:
                    continue

    return {
        "bytes_freed": bytes_freed,
        "files_removed": files_removed,
        "directories_removed": directories_removed,
    }
