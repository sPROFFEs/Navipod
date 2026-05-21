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

AUDIT_PATH = os.getenv("RAM_AUDIT_PATH", "/workspace/ram_audit.log")
AUDIT_INTERVAL_SECONDS = int(os.getenv("RAM_AUDIT_INTERVAL_SECONDS", "1800"))
AUDIT_TOP_N = int(os.getenv("RAM_AUDIT_TOP_N", "10"))

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


def _write_header_if_missing() -> None:
    try:
        if not os.path.exists(AUDIT_PATH) or os.path.getsize(AUDIT_PATH) == 0:
            os.makedirs(os.path.dirname(AUDIT_PATH) or ".", exist_ok=True)
            with open(AUDIT_PATH, "a") as f:
                f.write(CSV_HEADER + "\n")
    except Exception as exc:
        # Header is a nice-to-have. If we can't write it (read-only mount,
        # missing dir), the rows below would have failed too — but we
        # don't want one bad mount to crash the worker.
        logger.warning("ram_audit: could not initialize %s: %s", AUDIT_PATH, exc)


async def audit_scheduler() -> None:
    start_time = time.time()
    _write_header_if_missing()
    logger.info(
        "ram_audit: writing to %s every %ss (tracemalloc=%s)",
        AUDIT_PATH,
        AUDIT_INTERVAL_SECONDS,
        "on" if tracemalloc.is_tracing() else "off",
    )
    # First row immediately so we anchor the "fresh boot" baseline at the
    # repo root — otherwise the first sample wouldn't land for 30 min.
    while True:
        try:
            row = _snapshot_row(start_time)
            with open(AUDIT_PATH, "a") as f:
                f.write(row)
        except Exception as exc:
            logger.warning("ram_audit tick failed: %s", exc)
        await asyncio.sleep(AUDIT_INTERVAL_SECONDS)
