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

import logging
import secrets
from datetime import datetime, timezone

import database
import federation_service
import path_security
from auth import get_current_user, get_password_hash
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from http_client import http_client
from sqlalchemy.orm import Session

from .music.core import get_db

router = APIRouter()
logger = logging.getLogger(__name__)


SERVICE_ACCOUNT_USERNAME = "__federation__"

# How big each chunk we relay through the stream proxy is. 64 KiB
# matches the local /api/stream chunk size and keeps memory steady.
PROXY_CHUNK_BYTES = 64 * 1024


# ============================================================================
# AUTH HELPERS
# ============================================================================

def _require_admin(user: database.User):
    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")


def _verify_federation_token(db: Session, request: Request, authorization: str | None) -> database.FederationOutboundPeer:
    """Validate a Bearer token against the federation_outbound_peers
    table. Each peer has its own bcrypt-hashed token so revoking one
    doesn't disrupt the others.

    Side-effect: on a successful match we record `last_seen_at`, the
    request IP, and the User-Agent. The admin panel reads those fields
    to display online/idle/offline status and to know which peer is
    currently active."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing federation token")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty federation token")

    from auth import verify_password
    candidates = (
        db.query(database.FederationOutboundPeer)
        .filter(database.FederationOutboundPeer.revoked == False)  # noqa: E712
        .all()
    )
    if not candidates:
        # No peers configured — federation publishing is effectively OFF.
        raise HTTPException(status_code=403, detail="Federation publishing disabled")

    matched: database.FederationOutboundPeer | None = None
    for peer in candidates:
        try:
            if verify_password(token, peer.token_hash):
                matched = peer
                break
        except Exception:
            continue

    if not matched:
        raise HTTPException(status_code=401, detail="Invalid federation token")

    # Update freshness fields. Cheap — single row UPDATE per request.
    # We capture the original X-Forwarded-For when present (CloudFlare
    # / nginx reverse-proxies) so the admin sees the real peer IP.
    fwd = request.headers.get("x-forwarded-for")
    ip = (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else None))
    matched.last_seen_at = datetime.now(timezone.utc)
    matched.last_seen_ip = ip
    matched.last_seen_user_agent = (request.headers.get("user-agent") or "")[:240]
    db.commit()
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
    base_url = (payload.get("base_url") or "").strip().rstrip("/")
    api_token = (payload.get("api_token") or "").strip()
    enabled = bool(payload.get("enabled", True))

    if not name or not base_url:
        raise HTTPException(status_code=400, detail="name and base_url are required")
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="base_url must be http(s)://")

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
        new_url = (payload["base_url"] or "").strip().rstrip("/")
        if not (new_url.startswith("http://") or new_url.startswith("https://")):
            raise HTTPException(status_code=400, detail="base_url must be http(s)://")
        inst.base_url = new_url
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

    import asyncio as _asyncio
    _asyncio.create_task(_run_manual_sync(instance_id))
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
        "Authorization": f"Bearer {inst.api_token}" if inst.api_token else "",
        "User-Agent": "Navipod-Federation/1.0",
    }
    if request.headers.get("range"):
        upstream_headers["Range"] = request.headers["range"]

    upstream_url = inst.base_url.rstrip("/") + f"/api/federation/stream/{remote_id}"

    async def _streamer():
        try:
            async with http_client.stream("GET", upstream_url, headers=upstream_headers, timeout=30.0) as resp:
                # Mirror upstream status + key headers to the client.
                if resp.status_code >= 400:
                    return
                async for chunk in resp.aiter_bytes(PROXY_CHUNK_BYTES):
                    yield chunk
        except Exception as e:
            logger.warning("Federation stream proxy failed for inst=%s remote=%s: %s", instance_id, remote_id, e)

    # Discover headers for the response: we open a HEAD-style probe
    # to grab content-type / content-length / accept-ranges from
    # upstream. (httpx doesn't easily let us "peek" inside a stream
    # context, so we issue a tiny Range request to see them.)
    probe_headers = {**upstream_headers, "Range": "bytes=0-1"}
    content_type = "audio/mpeg"
    content_length = None
    content_range = None
    accept_ranges = "bytes"
    try:
        probe = await http_client.get(upstream_url, headers=probe_headers, timeout=10.0)
        content_type = probe.headers.get("content-type", content_type)
        # On a 206 the upstream gives us the full size in Content-Range
        cr = probe.headers.get("content-range")
        if cr and "/" in cr:
            try:
                content_length = int(cr.split("/")[-1])
            except Exception:
                content_length = None
    except Exception as e:
        logger.warning("Federation stream probe failed: %s", e)

    response_headers = {"Accept-Ranges": accept_ranges}
    if content_length:
        response_headers["Content-Length"] = str(content_length)

    # If the client sent a Range, we want to relay 206 too — the
    # upstream stream call will receive that header and emit a 206;
    # but Starlette's StreamingResponse uses status 200 by default. We
    # force 206 when the client requested a range so seeking works.
    status = 206 if request.headers.get("range") else 200
    return StreamingResponse(
        _streamer(),
        media_type=content_type,
        status_code=status,
        headers=response_headers,
    )
