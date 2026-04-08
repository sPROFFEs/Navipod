from __future__ import annotations

import os
import subprocess
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

from navipod_config import settings

REPO_ROOT = Path(settings.APP_SOURCE_ROOT)
VERSION_FILE = REPO_ROOT / "VERSION"


def _run_git(args, fallback=None):
    try:
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={REPO_ROOT}", *args],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        if completed.returncode != 0:
            return fallback
        return (completed.stdout or "").strip()
    except Exception:
        return fallback


def _normalize_version_label(raw_version: str | None):
    version = (raw_version or "").strip()
    if not version:
        return "v0.0.0"
    return version if version.startswith("v") else f"v{version}"


def _read_release_version():
    env_version = os.getenv("APP_VERSION")
    if env_version:
        return _normalize_version_label(env_version)
    if VERSION_FILE.exists():
        try:
            return _normalize_version_label(VERSION_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    return "v0.0.0"


def get_build_info():
    commit = os.getenv("APP_COMMIT") or _run_git(["rev-parse", "--short", "HEAD"], fallback="unknown")
    branch = os.getenv("APP_CHANNEL") or _run_git(["branch", "--show-current"], fallback=settings.UPDATE_SOURCE_BRANCH)
    build_date = os.getenv("APP_BUILD_DATE") or _run_git(["log", "-1", "--format=%cI"], fallback="unknown")
    revision = os.getenv("APP_REVISION") or _run_git(["rev-list", "--count", "HEAD"], fallback="unknown")
    release_version = _read_release_version()
    version = f"{release_version}+r{revision}" if revision != "unknown" else release_version
    return {
        "channel": branch,
        "commit": commit,
        "revision": revision,
        "release_version": release_version,
        "version": version,
        "build_date": build_date,
        "repo_url": settings.UPDATE_SOURCE_REPO_URL,
        "display_version": f"{version} ({commit})" if commit != "unknown" else version,
    }


def get_timezone_options():
    grouped = {}
    for tz_name in sorted(available_timezones()):
        if tz_name.startswith("Etc/"):
            continue
        if "/" in tz_name:
            group, remainder = tz_name.split("/", 1)
        else:
            group, remainder = "Other", tz_name
        label = remainder.replace("_", " / ")
        grouped.setdefault(group, []).append({"value": tz_name, "label": label})

    if "UTC" not in grouped:
        grouped["Other"] = [{"value": "UTC", "label": "UTC"}] + grouped.get("Other", [])

    ordered_groups = []
    preferred_order = ["UTC", "Europe", "America", "Asia", "Africa", "Australia", "Pacific", "Indian", "Atlantic", "Other"]
    for group in preferred_order:
        if group == "UTC":
            ordered_groups.append({"group": "UTC", "zones": [{"value": "UTC", "label": "UTC"}]})
            continue
        items = grouped.pop(group, None)
        if items:
            ordered_groups.append({"group": group, "zones": items})
    for group in sorted(grouped):
        ordered_groups.append({"group": group, "zones": grouped[group]})
    return ordered_groups


def format_bytes(size_bytes):
    if not size_bytes:
        return "0 B"
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size_bytes} B"


def format_datetime_for_display(dt_value, tzinfo=None):
    if not dt_value:
        return None
    target_tz = tzinfo or ZoneInfo("UTC")
    dt_local = dt_value
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=timezone.utc)
    try:
        dt_local = dt_local.astimezone(target_tz)
    except Exception:
        dt_local = dt_local.astimezone(ZoneInfo("UTC"))
        target_tz = ZoneInfo("UTC")
    tz_name = getattr(target_tz, "key", None) or str(target_tz)
    return f"{dt_local.strftime('%Y-%m-%d %H:%M:%S')} {tz_name}"
