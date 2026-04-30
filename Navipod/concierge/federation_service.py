"""
federation_service - Inter-instance federation worker

Two roles per instance:
  - PUBLISHER: lets remote peers read its catalog via API tokens bound
    to a service account. Implemented in routers/federation.py.
  - CONSUMER: subscribes to remote instances and mirrors their catalogs
    into the local `federated_tracks` table. That's most of the logic
    here.

Design goals
------------
1. **Offline tolerance** — if a remote goes silent the user must NEVER
   see its tracks. Search and stream proxy filter on `status` from
   `federated_instances`, which the health checker keeps fresh.
2. **Throttled** — both health and sync use long sleeps so a busy peer
   isn't hammered. Sync paginates 100 tracks at a time with a 2-second
   gap between pages. One peer at a time.
3. **Idempotent** — sync uses (instance_id, remote_id) as a unique key
   and UPSERTs, so re-runs don't duplicate.
4. **Crash-safe** — the cursor is stored in the DB, so a restart picks
   up where the previous sync left off.

The worker runs as a single asyncio task started from main.py's
startup event. It loops forever, waking every HEALTH_INTERVAL seconds
to ping all enabled instances, and runs a sync pass every
SYNC_INTERVAL seconds.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import database
import httpx

logger = logging.getLogger(__name__)


HEALTH_INTERVAL_S = 60          # ping every 60s
SYNC_INTERVAL_S = 30 * 60       # full re-sync pass every 30 min (delta is cheap)
SYNC_PAGE_SIZE = 100
SYNC_PAGE_DELAY_S = 2.0         # courteous gap between pages
SYNC_INSTANCE_DELAY_S = 5.0     # gap between peers in the same sweep
HEALTHY_WINDOW_S = 60           # last_seen <60s = healthy
DEGRADED_WINDOW_S = 15 * 60     # 60s..15min = degraded; >15min = offline

CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 15.0


# Async lock so we never run two sync passes for the same instance
# concurrently (e.g. user clicks "sync now" while the periodic sweep
# is mid-run).
_sync_locks: dict[int, asyncio.Lock] = {}


def _lock_for(instance_id: int) -> asyncio.Lock:
    lock = _sync_locks.get(instance_id)
    if lock is None:
        lock = asyncio.Lock()
        _sync_locks[instance_id] = lock
    return lock


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _client(token: Optional[str]) -> httpx.AsyncClient:
    headers = {"User-Agent": "Navipod-Federation/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S, write=READ_TIMEOUT_S, pool=READ_TIMEOUT_S),
        headers=headers,
    )


# === STATUS HELPERS =========================================================

def compute_status(last_seen_at: Optional[datetime]) -> str:
    if not last_seen_at:
        return "offline"
    # last_seen_at is timezone-aware after the health checker; sqlite
    # may return naive strings on read though, so coerce.
    if last_seen_at.tzinfo is None:
        last_seen_at = last_seen_at.replace(tzinfo=timezone.utc)
    delta = (_now() - last_seen_at).total_seconds()
    if delta <= HEALTHY_WINDOW_S:
        return "healthy"
    if delta <= DEGRADED_WINDOW_S:
        return "degraded"
    return "offline"


def status_is_playable(status: str) -> bool:
    """Whether streams from this instance should be served right now."""
    return status in ("healthy", "degraded")


# === HEALTH =================================================================

async def check_instance_health(db, instance: database.FederatedInstance) -> str:
    """Hit /api/federation/health on the peer. Updates `status`,
    `last_seen_at`, `last_error`. Returns the new status string."""
    url = instance.base_url.rstrip("/") + "/api/federation/health"
    try:
        async with _client(instance.api_token) as client:
            resp = await client.get(url)
        if resp.status_code == 200:
            instance.last_seen_at = _now()
            instance.status = "healthy"
            instance.last_error = None
        else:
            instance.last_error = f"HTTP {resp.status_code}"
            instance.status = compute_status(instance.last_seen_at)
    except Exception as e:
        instance.last_error = str(e)[:240]
        instance.status = compute_status(instance.last_seen_at)
    db.commit()
    return instance.status


# === SYNC ===================================================================

async def sync_instance(db, instance: database.FederatedInstance) -> dict:
    """Pull new pages of /api/federation/catalog and UPSERT into
    federated_tracks. Bounded by SYNC_PAGE_SIZE per page with a sleep
    gap. The remote returns rows ordered by id ASC; we use the highest
    id seen as the next cursor."""

    lock = _lock_for(instance.id)
    if lock.locked():
        return {"skipped": "already running"}

    async with lock:
        if not instance.enabled:
            return {"skipped": "disabled"}

        instance.sync_state = "running"
        instance.last_error = None
        db.commit()

        total_added = 0
        total_updated = 0
        cursor = int(instance.sync_cursor or 0)
        base = instance.base_url.rstrip("/")

        try:
            async with _client(instance.api_token) as client:
                # Optional: lightweight catalog stats endpoint to drive
                # the % progress bar in the admin UI. Not fatal if 404.
                try:
                    r = await client.get(f"{base}/api/federation/stats")
                    if r.status_code == 200:
                        stats = r.json() or {}
                        instance.sync_total = int(stats.get("total") or 0)
                        db.commit()
                except Exception:
                    pass

                while True:
                    url = f"{base}/api/federation/catalog"
                    params = {"after": cursor, "limit": SYNC_PAGE_SIZE}
                    resp = await client.get(url, params=params)
                    if resp.status_code != 200:
                        raise RuntimeError(f"catalog HTTP {resp.status_code}: {resp.text[:200]}")

                    payload = resp.json() or {}
                    items = payload.get("tracks") or []
                    if not items:
                        break

                    for item in items:
                        try:
                            remote_id = int(item.get("id"))
                        except Exception:
                            continue
                        if remote_id <= cursor:
                            continue

                        added, updated = _upsert_federated_track(db, instance.id, item)
                        total_added += added
                        total_updated += updated
                        cursor = max(cursor, remote_id)

                    instance.sync_cursor = cursor
                    instance.sync_done = (instance.sync_done or 0) + len(items)
                    db.commit()

                    # Heartbeat the connection — long syncs would
                    # otherwise look offline to the health checker.
                    instance.last_seen_at = _now()
                    instance.status = "healthy"
                    db.commit()

                    if len(items) < SYNC_PAGE_SIZE:
                        break

                    await asyncio.sleep(SYNC_PAGE_DELAY_S)

            instance.sync_state = "idle"
            instance.last_sync_at = _now()
            db.commit()
            return {"added": total_added, "updated": total_updated, "cursor": cursor}

        except Exception as e:
            instance.sync_state = "error"
            instance.last_error = str(e)[:240]
            db.commit()
            logger.warning("Federation sync failed for %s: %s", instance.name, e)
            return {"error": str(e), "added": total_added, "updated": total_updated}


def _upsert_federated_track(db, instance_id: int, item: dict) -> tuple[int, int]:
    remote_id = int(item.get("id"))
    title = (item.get("title") or "").strip() or "Unknown"
    artist = (item.get("artist") or "").strip() or "Unknown"
    album = (item.get("album") or "").strip()
    duration = item.get("duration") or 0
    cover_url = item.get("cover_url")

    existing = (
        db.query(database.FederatedTrack)
        .filter(database.FederatedTrack.instance_id == instance_id)
        .filter(database.FederatedTrack.remote_id == remote_id)
        .first()
    )

    if existing:
        existing.title = title
        existing.artist = artist
        existing.album = album
        existing.duration = duration
        existing.cover_url = cover_url
        existing.title_norm = _norm(title)
        existing.artist_norm = _norm(artist)
        existing.synced_at = _now()
        return (0, 1)

    row = database.FederatedTrack(
        instance_id=instance_id,
        remote_id=remote_id,
        title=title,
        artist=artist,
        album=album,
        duration=duration,
        cover_url=cover_url,
        title_norm=_norm(title),
        artist_norm=_norm(artist),
    )
    db.add(row)
    return (1, 0)


# === BACKGROUND LOOP ========================================================

_loop_task: Optional[asyncio.Task] = None
_last_sync_run: dict[int, float] = {}


async def _periodic_loop():
    """Background driver: keeps statuses fresh and triggers a sync
    pass per peer at SYNC_INTERVAL_S. Single shared task — peers are
    handled serially within each tick to keep load low."""
    while True:
        try:
            await _tick()
        except Exception as e:
            logger.exception("Federation periodic tick failed: %s", e)
        await asyncio.sleep(HEALTH_INTERVAL_S)


async def _tick():
    db = database.SessionLocal()
    try:
        instances = (
            db.query(database.FederatedInstance)
            .filter(database.FederatedInstance.enabled == True)  # noqa: E712
            .all()
        )
        now = time.time()
        for inst in instances:
            await check_instance_health(db, inst)
            # Trigger a sync if it's been long enough AND the peer is
            # reachable. Skipping when offline avoids burning retries
            # against a dead host.
            if not status_is_playable(inst.status):
                continue
            last = _last_sync_run.get(inst.id, 0)
            if now - last >= SYNC_INTERVAL_S:
                _last_sync_run[inst.id] = now
                await sync_instance(db, inst)
                await asyncio.sleep(SYNC_INSTANCE_DELAY_S)
    finally:
        db.close()


def start_background_loop():
    global _loop_task
    if _loop_task and not _loop_task.done():
        return
    try:
        _loop_task = asyncio.create_task(_periodic_loop())
        logger.info("Federation background loop started")
    except RuntimeError:
        # No running event loop yet — main.py calls us from startup
        # event so this is unusual; log and move on.
        logger.warning("Federation: no event loop, skipping start")


def stop_background_loop():
    global _loop_task
    if _loop_task:
        _loop_task.cancel()
        _loop_task = None
