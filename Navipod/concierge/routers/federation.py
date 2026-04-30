"""
Federation router.

Three groups of endpoints:

1. PUBLISH (token-authenticated, used by REMOTE peers calling us)
   - GET  /api/federation/health
   - GET  /api/federation/stats
   - GET  /api/federation/catalog?after=&limit=
   - GET  /api/federation/stream/{track_id}    (with Range passthrough)

2. ADMIN (cookie-authenticated as admin user, manages peers we trust)
   - GET    /api/admin/federation/instances
   - POST   /api/admin/federation/instances
   - PATCH  /api/admin/federation/instances/{id}
   - DELETE /api/admin/federation/instances/{id}
   - POST   /api/admin/federation/instances/{id}/sync   (manual trigger)
   - GET    /api/admin/federation/service-account
   - POST   /api/admin/federation/service-account/rotate

3. PROXY (regular user, plays a remote track via us)
   - GET /api/federation/{instance_id}/stream/{remote_id}
     (filters out OFFLINE peers — that's the user-experience guarantee)
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from datetime import datetime, timezone

import database
import federation_service
import httpx
import path_security
from auth import get_current_user, get_password_hash
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from http_client import http_client
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .music.core import get_db

router = APIRouter()
logger = logging.getLogger(__name__)


SERVICE_ACCOUNT_USERNAME = "__federation__"
TOKEN_PREFIX_LEN = 12

# How big each chunk we relay through the stream proxy is. 64 KiB
# matches the local /api/stream chunk size and keeps memory steady.
PROXY_CHUNK_BYTES = 64 * 1024

# Throttle window for last_seen_at writes. A peer pings /health every
# minute and may issue many catalog/stream requests during sync — we
# only need ~30s freshness to compute online/idle/offline status, so
# coalesce into one write per peer per 30s and avoid SQLite's writer
# lock contention.
LAST_SEEN_WRITE_INTERVAL_S = 30.0
_last_seen_writes: dict[int, float] = {}

# Hold references to detached `asyncio.create_task` work so Python
# doesn't garbage-collect the coroutine mid-flight (asyncio docs warn
# about this explicitly).
_pending_tasks: set[asyncio.Task] = set()


def _spawn(coro):
    task = asyncio.create_task(coro)
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)
    return task


# === SSRF GUARD =============================================================
#
# The admin can register a remote `base_url` and the federation worker
# will issue authenticated GETs against it (forwarding the bearer token
# to the URL). Without a guard, `http://127.0.0.1:6379/`, AWS metadata
# (`http://169.254.169.254/...`), or other internal hosts could be
# probed via Navipod, leaking the federation token to those services.
#
# We resolve the URL's host to one or more IPs and block any of:
#   - loopback (127/8, ::1)
#   - link-local (169.254/16, fe80::/10)
#   - private RFC1918 + ULA (10/8, 172.16/12, 192.168/16, fc00::/7)
#   - 0.0.0.0 / unspecified
#
# An admin who genuinely needs to federate against a private network
# (homelab over Tailscale, dev) can opt in via env var.
import ipaddress
import os
import socket
from urllib.parse import urlparse

_FEDERATION_ALLOW_PRIVATE = os.getenv("FEDERATION_ALLOW_PRIVATE_HOSTS", "").lower() in ("1", "true", "yes")


def _validate_federation_base_url(url: str) -> str:
    """Returns a normalized URL, or raises HTTPException with a
    user-readable message. Idempotent on already-validated URLs."""
    if not url:
        raise HTTPException(status_code=400, detail="base_url is required")
    url = url.strip().rstrip("/")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="base_url must be http(s)://")

    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="base_url has no host")

    if _FEDERATION_ALLOW_PRIVATE:
        return url

    # Resolve and inspect every address — `getaddrinfo` returns AAAA
    # and A records, so a hostname that resolves to BOTH a public IPv4
    # and a private IPv6 (or vice versa) is rejected.
    try:
        addrinfos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise HTTPException(status_code=400, detail=f"Could not resolve host: {e}")

    for af, _, _, _, sockaddr in addrinfos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_loopback or ip.is_link_local or ip.is_private
            or ip.is_unspecified or ip.is_multicast or ip.is_reserved
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Refusing to federate against {host} ({ip_str}): private/loopback/"
                    "link-local addresses are blocked. Set FEDERATION_ALLOW_PRIVATE_HOSTS=1 "
                    "in the env file if you really mean this (e.g. Tailscale homelab)."
                ),
            )
    return url


# ============================================================================
# AUTH HELPERS
# ============================================================================

def _require_admin(user: database.User):
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")


def _verify_federation_token(db: Session, request: Request, authorization: str | None) -> database.FederationOutboundPeer:
    """Validate a Bearer token against the federation_outbound_peers
    table.

    Two-tier lookup to defend against the O(N) bcrypt-everything DoS:

    1. Fast path — the token's first 12 chars are stored in an indexed
       `token_prefix` column. We do a single indexed query for that
       prefix and bcrypt-verify only the matching row(s). With
       `secrets.token_urlsafe(40)` collisions on 12 chars are
       astronomically unlikely (>10^21 keyspace), so this is O(1) in
       practice.

    2. Slow path (legacy) — peers issued before migration 021 have
       NULL prefix. We fall back to bcrypting against just those rows.
       Once an admin rotates each token the slow set drains to empty.

    Side-effect: stamp last_seen_at on the matched peer so the admin
    panel can show "online <90s / idle / offline". Writes are
    throttled to one per peer per 30s — a hot peer pinging us every
    second otherwise locks SQLite's writer.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing federation token")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty federation token")

    from auth import verify_password

    prefix = token[:TOKEN_PREFIX_LEN]
    Peer = database.FederationOutboundPeer

    # Build candidate set: matches on the prefix index PLUS any legacy
    # rows with NULL prefix. The latter set is bounded by the number
    # of pre-migration peers (typically 0–few).
    base_q = db.query(Peer).filter(Peer.revoked == False)  # noqa: E712
    fast_candidates = base_q.filter(Peer.token_prefix == prefix).all()
    legacy_candidates = base_q.filter(Peer.token_prefix.is_(None)).all()

    if not fast_candidates and not legacy_candidates:
        raise HTTPException(status_code=403, detail="Federation publishing disabled")

    matched: database.FederationOutboundPeer | None = None
    for peer in (*fast_candidates, *legacy_candidates):
        try:
            if verify_password(token, peer.token_hash):
                matched = peer
                break
        except Exception:
            continue

    if not matched:
        raise HTTPException(status_code=401, detail="Invalid federation token")

    # Throttle last_seen_at writes. Status (online/idle/offline) is
    # computed against `last_seen_at` with a >=90s "online" window, so
    # 30s staleness is invisible to the UI.
    now_ts = time.time()
    last_write = _last_seen_writes.get(matched.id, 0.0)
    if now_ts - last_write >= LAST_SEEN_WRITE_INTERVAL_S:
        _last_seen_writes[matched.id] = now_ts
        fwd = request.headers.get("x-forwarded-for")
        ip = (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else None))
        matched.last_seen_at = datetime.now(timezone.utc)
        matched.last_seen_ip = ip
        matched.last_seen_user_agent = (request.headers.get("user-agent") or "")[:240]
        try:
            db.commit()
        except Exception as e:
            logger.warning("Failed to stamp last_seen_at for peer %s: %s", matched.id, e)
            db.rollback()

    return matched


# ============================================================================
# PUBLISH ENDPOINTS (called BY remote peers)
# ============================================================================

@router.get("/api/federation/health")
async def federation_health(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
):
    """Lightweight probe. The remote calls this every minute; we just
    confirm we're alive and the token is good."""
    _verify_federation_token(db, request, authorization)
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


@router.get("/api/federation/stats")
async def federation_stats(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
):
    _verify_federation_token(db, request, authorization)
    total = db.query(database.Track).count()
    return {"total": total}


@router.get("/api/federation/catalog")
async def federation_catalog(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """Paginated catalog dump. The remote sends us a cursor (`after`)
    and we return tracks with id > cursor, ordered by id ASC. The peer
    persists the highest id seen and uses it as the next cursor."""
    _verify_federation_token(db, request, authorization)

    rows = (
        db.query(database.Track)
        .filter(database.Track.id > after)
        .order_by(database.Track.id.asc())
        .limit(limit)
        .all()
    )
    out = []
    for t in rows:
        out.append({
            "id": t.id,
            "title": t.title,
            "artist": t.artist,
            "album": t.album,
            "duration": t.duration,
            # Cover URL points back at us — peers fetch it on demand.
            "cover_url": f"/api/cover/{t.id}",
        })
    return {"tracks": out, "next_cursor": rows[-1].id if rows else after}


@router.get("/api/federation/stream/{track_id}")
async def federation_stream(
    track_id: int,
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
):
    """Direct file stream with Range passthrough. Authenticated by
    federation token (NOT user cookie) so peers can fetch without
    impersonating a real user."""
    _verify_federation_token(db, request, authorization)

    # Defer to the local streaming logic by importing the existing
    # helper. Range header is taken from the original request and
    # forwarded transparently.
    from routers.music.streaming import stream_track
    # `stream_track` is async and respects the request's Range header.
    return await stream_track(track_id, request, db)


# ============================================================================
# ADMIN CRUD
# ============================================================================

def _serialize_instance(inst: database.FederatedInstance, *, include_token: bool = False) -> dict:
    out = {
        "id": inst.id,
        "name": inst.name,
        "base_url": inst.base_url,
        "enabled": bool(inst.enabled),
        "status": inst.status,
        "last_seen_at": inst.last_seen_at.isoformat() if inst.last_seen_at else None,
        "last_error": inst.last_error,
        "sync_state": inst.sync_state,
        "sync_total": inst.sync_total or 0,
        "sync_done": inst.sync_done or 0,
        "sync_cursor": inst.sync_cursor or 0,
        "last_sync_at": inst.last_sync_at.isoformat() if inst.last_sync_at else None,
        "has_token": bool(inst._api_token),
    }
    if include_token:
        out["api_token"] = inst.api_token
    return out


@router.get("/api/admin/federation/instances")
async def admin_list_instances(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    _require_admin(user)
    rows = db.query(database.FederatedInstance).order_by(database.FederatedInstance.id.asc()).all()
    return [_serialize_instance(r) for r in rows]


@router.post("/api/admin/federation/instances")
async def admin_create_instance(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    _require_admin(user)

    payload = await request.json()
    name = (payload.get("name") or "").strip()
    base_url_raw = (payload.get("base_url") or "").strip()
    api_token = (payload.get("api_token") or "").strip()
    enabled = bool(payload.get("enabled", True))

    if not name or not base_url_raw:
        raise HTTPException(status_code=400, detail="name and base_url are required")
    # SSRF guard: rejects loopback / private / link-local hosts unless
    # FEDERATION_ALLOW_PRIVATE_HOSTS=1 is set.
    base_url = _validate_federation_base_url(base_url_raw)

    inst = database.FederatedInstance(
        name=name,
        base_url=base_url,
        enabled=enabled,
    )
    if api_token:
        inst.api_token = api_token
    db.add(inst)
    db.commit()
    db.refresh(inst)
    return _serialize_instance(inst)


@router.patch("/api/admin/federation/instances/{instance_id}")
async def admin_update_instance(instance_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    _require_admin(user)

    inst = db.query(database.FederatedInstance).filter(database.FederatedInstance.id == instance_id).first()
    if not inst:
        raise HTTPException(status_code=404, detail="Not found")

    payload = await request.json()
    if "name" in payload:
        inst.name = (payload["name"] or "").strip() or inst.name
    if "base_url" in payload:
        # Same SSRF guard as create — admin can't sneak a private
        # address in via PATCH after creation.
        inst.base_url = _validate_federation_base_url(payload["base_url"] or "")
    if "enabled" in payload:
        inst.enabled = bool(payload["enabled"])
    if "api_token" in payload and payload["api_token"]:
        inst.api_token = str(payload["api_token"]).strip()

    db.commit()
    return _serialize_instance(inst)


@router.delete("/api/admin/federation/instances/{instance_id}")
async def admin_delete_instance(instance_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    _require_admin(user)

    inst = db.query(database.FederatedInstance).filter(database.FederatedInstance.id == instance_id).first()
    if not inst:
        raise HTTPException(status_code=404, detail="Not found")

    # CASCADE on federated_tracks via FK. Drop the instance row.
    db.delete(inst)
    db.commit()
    return {"deleted": instance_id}


@router.post("/api/admin/federation/instances/{instance_id}/sync")
async def admin_sync_instance(instance_id: int, request: Request, db: Session = Depends(get_db)):
    """Manual sync trigger. Returns immediately — sync runs in the
    background and progress is observable via the instance's
    sync_state / sync_done fields."""
    user = get_current_user(request, db)
    _require_admin(user)

    inst = db.query(database.FederatedInstance).filter(database.FederatedInstance.id == instance_id).first()
    if not inst:
        raise HTTPException(status_code=404, detail="Not found")
    if not inst.enabled:
        raise HTTPException(status_code=400, detail="Instance is disabled")

    # Detached background work — must hold a reference to the task or
    # Python may GC the coroutine mid-flight. `_spawn` keeps it alive
    # until completion. (See PEP-3156 / asyncio docs warning on
    # `create_task` return value.)
    _spawn(_run_manual_sync(instance_id))
    return {"status": "scheduled"}


async def _run_manual_sync(instance_id: int):
    db = database.SessionLocal()
    try:
        inst = db.query(database.FederatedInstance).filter(database.FederatedInstance.id == instance_id).first()
        if not inst:
            return
        # Fresh health check before sync — gives the user immediate
        # feedback if the URL/token is wrong.
        await federation_service.check_instance_health(db, inst)
        if federation_service.status_is_playable(inst.status):
            await federation_service.sync_instance(db, inst)
    finally:
        db.close()


# === OUTBOUND PEERS (publishing side) =======================================
#
# Each row in federation_outbound_peers represents a remote instance
# we *let* federate from us. The admin creates one row per peer, gets
# back the cleartext token ONCE, and shares it with the remote admin.
# From then on every incoming federation request stamps that row with
# last_seen_at + last_seen_ip so the admin can see who's online.

def _outbound_status(peer: database.FederationOutboundPeer) -> str:
    if peer.revoked:
        return "revoked"
    if not peer.last_seen_at:
        return "never"
    last = peer.last_seen_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    delta = (datetime.now(timezone.utc) - last).total_seconds()
    if delta <= 90:
        return "online"
    if delta <= 15 * 60:
        return "idle"
    return "offline"


def _serialize_outbound(peer: database.FederationOutboundPeer) -> dict:
    return {
        "id": peer.id,
        "name": peer.name,
        "peer_url": peer.peer_url,
        "status": _outbound_status(peer),
        "revoked": bool(peer.revoked),
        "revoked_at": peer.revoked_at.isoformat() if peer.revoked_at else None,
        "last_seen_at": peer.last_seen_at.isoformat() if peer.last_seen_at else None,
        "last_seen_ip": peer.last_seen_ip,
        "last_seen_user_agent": peer.last_seen_user_agent,
        "created_at": peer.created_at.isoformat() if peer.created_at else None,
    }


@router.get("/api/admin/federation/outbound")
async def admin_list_outbound(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    _require_admin(user)
    rows = (
        db.query(database.FederationOutboundPeer)
        .order_by(database.FederationOutboundPeer.id.asc())
        .all()
    )
    return [_serialize_outbound(r) for r in rows]


@router.post("/api/admin/federation/outbound")
async def admin_create_outbound(request: Request, db: Session = Depends(get_db)):
    """Issue a new federation token bound to a named peer. Returns the
    cleartext ONCE — the admin must copy it before navigating away."""
    user = get_current_user(request, db)
    _require_admin(user)

    payload = await request.json()
    name = (payload.get("name") or "").strip()
    peer_url = (payload.get("peer_url") or "").strip().rstrip("/") or None
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if peer_url and not (peer_url.startswith("http://") or peer_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="peer_url must be http(s)://")

    token = secrets.token_urlsafe(40)
    peer = database.FederationOutboundPeer(
        name=name,
        peer_url=peer_url,
        token_hash=get_password_hash(token),
        token_prefix=token[:TOKEN_PREFIX_LEN],
    )
    db.add(peer)
    db.commit()
    db.refresh(peer)
    out = _serialize_outbound(peer)
    out["token"] = token   # plaintext, last time it's ever in the response
    return out


@router.post("/api/admin/federation/outbound/{peer_id}/revoke")
async def admin_revoke_outbound(peer_id: int, request: Request, db: Session = Depends(get_db)):
    """Soft-revoke: token stops working immediately, row stays so the
    admin can still see "this peer was online until X". Use DELETE for
    a permanent removal."""
    user = get_current_user(request, db)
    _require_admin(user)

    peer = (
        db.query(database.FederationOutboundPeer)
        .filter(database.FederationOutboundPeer.id == peer_id)
        .first()
    )
    if not peer:
        raise HTTPException(status_code=404, detail="Not found")
    peer.revoked = True
    peer.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return _serialize_outbound(peer)


@router.delete("/api/admin/federation/outbound/{peer_id}")
async def admin_delete_outbound(peer_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    _require_admin(user)

    peer = (
        db.query(database.FederationOutboundPeer)
        .filter(database.FederationOutboundPeer.id == peer_id)
        .first()
    )
    if not peer:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(peer)
    db.commit()
    return {"deleted": peer_id}


# ============================================================================
# PROXY (regular user listening to a remote track)
# ============================================================================

@router.get("/api/federation/proxy/{instance_id}/stream/{remote_id}")
async def federation_proxy_stream(
    instance_id: int,
    remote_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Stream a remote track through us. Cookie auth (regular user).
    Hard-blocks playback when the peer is offline — that's the
    user-experience guarantee: a remote track that surfaced in search
    a moment ago becomes a 503 here, never a half-played corrupted
    file."""
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    inst = db.query(database.FederatedInstance).filter(database.FederatedInstance.id == instance_id).first()
    if not inst or not inst.enabled:
        raise HTTPException(status_code=404, detail="Instance not found")
    if not federation_service.status_is_playable(inst.status):
        # 503 + Retry-After so progressive players treat it as a
        # transient outage rather than a hard 404.
        return JSONResponse(
            {"error": "Federated source is offline"},
            status_code=503,
            headers={"Retry-After": "60"},
        )

    # Forward Range header to preserve seeking.
    upstream_headers = {
        "User-Agent": "Navipod-Federation/1.0",
    }
    if inst.api_token:
        upstream_headers["Authorization"] = f"Bearer {inst.api_token}"
    if request.headers.get("range"):
        upstream_headers["Range"] = request.headers["range"]

    upstream_url = inst.base_url.rstrip("/") + f"/api/federation/stream/{remote_id}"

    # Open the upstream stream RIGHT NOW (not lazily inside the
    # generator) so we can inspect the upstream response status and
    # headers BEFORE we commit to a status code on our own response.
    # This avoids the previous bug where we returned a 200/206 wrapper
    # around an upstream 4xx — the browser saw an empty body and
    # raised a generic media error.
    upstream_client = httpx.AsyncClient(timeout=30.0)
    try:
        upstream_req = upstream_client.build_request("GET", upstream_url, headers=upstream_headers)
        upstream_resp = await upstream_client.send(upstream_req, stream=True)
    except Exception as e:
        await upstream_client.aclose()
        logger.warning("Federation upstream connect failed for inst=%s remote=%s: %s", instance_id, remote_id, e)
        return JSONResponse(
            {"error": "Could not reach federated source"},
            status_code=502,
        )

    if upstream_resp.status_code >= 400:
        # Read at most 256 bytes of the error body for the log; bound
        # the read so a malicious peer can't OOM us with a huge body.
        body_preview = b""
        try:
            async for chunk in upstream_resp.aiter_bytes(256):
                body_preview = chunk
                break
        except Exception:
            pass
        await upstream_resp.aclose()
        await upstream_client.aclose()
        logger.warning(
            "Federation upstream rejected inst=%s remote=%s: HTTP %s — %r",
            instance_id, remote_id, upstream_resp.status_code, body_preview,
        )
        return JSONResponse(
            {"error": f"Upstream returned {upstream_resp.status_code}"},
            status_code=502,
        )

    # Forward the headers the audio element actually cares about.
    forwarded_headers = {}
    for key in ("content-type", "content-length", "content-range", "accept-ranges"):
        if key in upstream_resp.headers:
            forwarded_headers[key.title()] = upstream_resp.headers[key]
    if "Accept-Ranges" not in forwarded_headers:
        forwarded_headers["Accept-Ranges"] = "bytes"

    media_type = upstream_resp.headers.get("content-type", "audio/mpeg")
    upstream_status = upstream_resp.status_code  # 200 or 206

    async def _streamer():
        # Body-only generator. Cleanup is delegated to the
        # BackgroundTask below so it ALWAYS runs — including when the
        # client disconnects before we yield the first chunk (in which
        # case this generator's `finally` would never have executed).
        try:
            async for chunk in upstream_resp.aiter_bytes(PROXY_CHUNK_BYTES):
                yield chunk
        except Exception as e:
            logger.warning("Federation stream interrupted for inst=%s remote=%s: %s", instance_id, remote_id, e)

    async def _cleanup_upstream():
        # Idempotent close — fine to call multiple times, fine if the
        # streamer already broke out. Without this, an early-disconnect
        # client leaks the httpx.AsyncClient + the authenticated peer
        # session indefinitely.
        try:
            await upstream_resp.aclose()
        except Exception:
            pass
        try:
            await upstream_client.aclose()
        except Exception:
            pass

    return StreamingResponse(
        _streamer(),
        media_type=media_type,
        status_code=upstream_status,
        headers=forwarded_headers,
        background=BackgroundTask(_cleanup_upstream),
    )
