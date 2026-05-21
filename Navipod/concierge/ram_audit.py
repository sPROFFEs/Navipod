"""
Periodic memory audit.

Appends one CSV row per tick to /workspace/ram_audit.log so the host
can read it at the repo root (the docker-compose mounts `..:/workspace`
which lands at the Navipod repo root). The file is purely additive —
it just grows over time — so you can `tail` / `awk` it after a week
and see whether RSS climbed and *what* climbed alongside it.

Requires PYTHONTRACEMALLOC to be set in the container environment
(docker-compose adds it alongside MALLOC_ARENA_MAX). Without it, the
tracemalloc columns stay 0 and the report still gives RSS / VMS /
threads / FDs, which alone is enough to tell whether glibc malloc tuning
is doing its job.

Tuning knobs (all env, all optional):
  RAM_AUDIT_PATH              — output file path (default /workspace/ram_audit.log)
  RAM_AUDIT_INTERVAL_SECONDS  — tick interval (default 1800 = 30 min)
  RAM_AUDIT_TOP_N             — how many top allocators to record (default 10)
"""

import asyncio
import logging
import os
import time
import tracemalloc

logger = logging.getLogger(__name__)

# Two candidate paths. We *prefer* /workspace because that's the host's
# repo root (mounted via docker-compose's `..:/workspace`) — convenient
# to find. But host bind mounts can be unwritable under userns-remap or
# restrictive host ACLs, so we fall back to /saas-data (always RW, the
# app already writes there) — host-visible at /opt/saas-data.
AUDIT_PATH_PRIMARY = os.getenv("RAM_AUDIT_PATH", "/workspace/ram_audit.log")
AUDIT_PATH_FALLBACK = os.getenv("RAM_AUDIT_PATH_FALLBACK", "/saas-data/ram_audit.log")
AUDIT_INTERVAL_SECONDS = int(os.getenv("RAM_AUDIT_INTERVAL_SECONDS", "1800"))
AUDIT_TOP_N = int(os.getenv("RAM_AUDIT_TOP_N", "10"))

# Resolved at startup by _resolve_audit_path(). Set to None until then.
_active_audit_path: str | None = None

# Single header line so the file is greppable / spreadsheet-importable.
# `top_allocators` is a pipe-separated list of `file:line:size_kb` entries.
CSV_HEADER = (
    "iso_utc,uptime_s,rss_kb,vms_kb,threads,open_fds,"
    "py_traced_current_kb,py_traced_peak_kb,top_allocators"
)


def _read_proc_status() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if ":" not in line:
                    continue
                k, _, v = line.partition(":")
                out[k.strip()] = v.strip()
    except Exception:
        # /proc/self/status is Linux-only; non-Linux hosts (dev macs)
        # silently get zeros — the audit is intended for the prod
        # container regardless.
        pass
    return out


def _kb_field(v: str) -> int:
    """Parse a `/proc/self/status` value like '12345 kB' into an int."""
    try:
        return int(v.split()[0])
    except Exception:
        return 0


def _count_open_fds() -> int:
    try:
        return len(os.listdir("/proc/self/fd"))
    except Exception:
        return -1


def _format_top_allocators(snap: tracemalloc.Snapshot) -> str:
    stats = snap.statistics("lineno")[:AUDIT_TOP_N]
    out: list[str] = []
    for s in stats:
        # `s.traceback[0]` is the deepest frame; format compactly and
        # strip out anything that would break the CSV row.
        frame = s.traceback[0]
        filename = (frame.filename or "?").rsplit("/", 1)[-1]
        line_no = frame.lineno or 0
        size_kb = s.size // 1024
        out.append(f"{filename}:{line_no}:{size_kb}kB".replace(",", ";").replace("|", "/"))
    return "|".join(out)


def _snapshot_row(start_time: float) -> str:
    status = _read_proc_status()
    rss = _kb_field(status.get("VmRSS", "0 kB"))
    vms = _kb_field(status.get("VmSize", "0 kB"))
    threads = int((status.get("Threads") or "0").split()[0] or 0)
    fds = _count_open_fds()

    current_kb = peak_kb = 0
    top_str = ""
    if tracemalloc.is_tracing():
        try:
            current, peak = tracemalloc.get_traced_memory()
            current_kb = current // 1024
            peak_kb = peak // 1024
            top_str = _format_top_allocators(tracemalloc.take_snapshot())
        except Exception as exc:
            logger.warning("ram_audit: tracemalloc snapshot failed: %s", exc)

    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    uptime = int(time.time() - start_time)
    return f"{iso},{uptime},{rss},{vms},{threads},{fds},{current_kb},{peak_kb},{top_str}\n"


def _try_initialize(path: str) -> bool:
    """Attempt to create the directory and write the CSV header. Returns
    True on success; False if the path is unwritable for any reason."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        needs_header = not os.path.exists(path) or os.path.getsize(path) == 0
        # Open in append so an existing file is preserved; if we don't
        # have permission this raises immediately.
        with open(path, "a") as f:
            if needs_header:
                f.write(CSV_HEADER + "\n")
        return True
    except Exception as exc:
        logger.warning("ram_audit: cannot use %s (%s)", path, exc)
        return False


def _resolve_audit_path() -> str | None:
    """Pick the first writable candidate. Returns None if both fail —
    in which case the scheduler still runs (so logs aren't full of
    repeating tracebacks) but writes nothing."""
    for candidate in (AUDIT_PATH_PRIMARY, AUDIT_PATH_FALLBACK):
        if not candidate:
            continue
        if _try_initialize(candidate):
            return candidate
    return None


async def audit_scheduler() -> None:
    global _active_audit_path
    start_time = time.time()
    _active_audit_path = _resolve_audit_path()
    if _active_audit_path:
        logger.info(
            "ram_audit: writing to %s every %ss (tracemalloc=%s)",
            _active_audit_path,
            AUDIT_INTERVAL_SECONDS,
            "on" if tracemalloc.is_tracing() else "off",
        )
    else:
        logger.warning(
            "ram_audit: no writable path found (tried %s, %s) — audit disabled",
            AUDIT_PATH_PRIMARY,
            AUDIT_PATH_FALLBACK,
        )
    while True:
        if _active_audit_path:
            try:
                row = _snapshot_row(start_time)
                with open(_active_audit_path, "a") as f:
                    f.write(row)
            except Exception as exc:
                logger.warning("ram_audit tick failed: %s", exc)
        await asyncio.sleep(AUDIT_INTERVAL_SECONDS)
