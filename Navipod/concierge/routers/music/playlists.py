"""
Playlist management and Navidrome sync.
"""
import os
import asyncio
import io
import uuid
from fastapi import APIRouter, Request, Depends, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from sqlalchemy.orm import Session, aliased
from sqlalchemy import func, select
from pydantic import BaseModel as PydanticBaseModel
import httpx
from PIL import Image

import database
import manager
from navipod_config import settings

from .core import get_db, get_current_user_safe
from .favorites import schedule_navidrome_sync


router = APIRouter()


# --- PYDANTIC MODELS ---

class CreatePlaylistRequest(PydanticBaseModel):
    name: str


class PlaylistUpdateRequest(PydanticBaseModel):
    name: str


class AddToPlaylistRequest(PydanticBaseModel):
    track_id: int


class PublishPlaylistRequest(PydanticBaseModel):
    is_public: bool


class PlaylistCoverTrackRequest(PydanticBaseModel):
    track_id: int


SYSTEM_PLAYLIST_NAMES = {"music", "pool", "users", "podcasts", "downloads"}
ALLOWED_PLAYLIST_COVER_TYPES = {
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/png": [b"\x89PNG"],
    "image/webp": [b"RIFF"],
    "image/gif": [b"GIF87a", b"GIF89a"],
}
MAX_PLAYLIST_COVER_SIZE = 5 * 1024 * 1024
PLAYLIST_COVER_SIZE = (640, 640)


# --- PLAYLIST ACCESS HELPERS ---

def get_playlist_or_404(db: Session, playlist_id: int, user):
    playlist = db.query(database.Playlist).filter(database.Playlist.id == playlist_id).first()
    if not playlist:
        return None

    is_owner = playlist.owner_id == user.id
    if not is_owner and not playlist.is_public:
        return None
    return playlist


def playlist_is_editable_by_user(playlist, user) -> bool:
    return playlist.owner_id == user.id and playlist.source_playlist_id is None


def _playlist_cover_dir(username: str) -> str:
    cover_dir = f"{settings.MUSIC_ROOT}/{username}/playlists/.covers"
    os.makedirs(cover_dir, mode=0o777, exist_ok=True)
    return cover_dir


def _playlist_cover_url(playlist_id: int) -> str:
    return f"/api/playlists/{playlist_id}/cover"


def _validate_playlist_cover_bytes(file_content: bytes, content_type: str) -> bool:
    if content_type not in ALLOWED_PLAYLIST_COVER_TYPES:
        return False
    return any(file_content[:len(pattern)] == pattern for pattern in ALLOWED_PLAYLIST_COVER_TYPES[content_type])


def _remove_playlist_cover_file(playlist) -> None:
    if playlist.cover_path and os.path.exists(playlist.cover_path):
        try:
            os.remove(playlist.cover_path)
        except Exception as e:
            print(f"[PLAYLIST-COVER] Error removing cover file: {e}")


def get_playlist_thumbnail(db: Session, playlist) -> str:
    if playlist.cover_path:
        return _playlist_cover_url(playlist.id)
    if playlist.cover_track_id:
        return f"/api/cover/{playlist.cover_track_id}"
    first_item = db.query(database.PlaylistItem).filter(
        database.PlaylistItem.playlist_id == playlist.id
    ).order_by(database.PlaylistItem.position).first()
    if first_item and first_item.track:
        return f"/api/cover/{first_item.track.id}"
    return "/static/img/default_cover.png"


def fetch_playlist_summaries(db: Session, viewer_id: int | None = None, *, owner_id: int | None = None, public_only: bool = False):
    count_subquery = (
        db.query(
            database.PlaylistItem.playlist_id.label("playlist_id"),
            func.count(database.PlaylistItem.id).label("track_count"),
        )
        .group_by(database.PlaylistItem.playlist_id)
        .subquery()
    )

    source_playlist = aliased(database.Playlist)
    source_owner = aliased(database.User)
    thumbnail_track_id = (
        select(database.PlaylistItem.track_id)
        .where(database.PlaylistItem.playlist_id == database.Playlist.id)
        .order_by(database.PlaylistItem.position.asc(), database.PlaylistItem.id.asc())
        .limit(1)
        .scalar_subquery()
    )

    query = (
        db.query(
            database.Playlist.id.label("id"),
            database.Playlist.name.label("name"),
            database.Playlist.owner_id.label("owner_id"),
            database.Playlist.is_public.label("is_public"),
            database.Playlist.source_playlist_id.label("source_playlist_id"),
            database.Playlist.cover_path.label("cover_path"),
            database.Playlist.cover_track_id.label("cover_track_id"),
            database.User.username.label("owner_username"),
            source_owner.username.label("source_owner_username"),
            func.coalesce(count_subquery.c.track_count, 0).label("track_count"),
        )
        .join(database.User, database.User.id == database.Playlist.owner_id)
        .outerjoin(source_playlist, source_playlist.id == database.Playlist.source_playlist_id)
        .outerjoin(source_owner, source_owner.id == source_playlist.owner_id)
        .outerjoin(count_subquery, count_subquery.c.playlist_id == database.Playlist.id)
    )

    if owner_id is not None:
        query = query.filter(database.Playlist.owner_id == owner_id)
    if public_only:
        query = query.filter(database.Playlist.is_public == True)

    summaries = []
    for row in query.order_by(database.Playlist.id.desc()).all():
        if not row.name or row.name.lower() in SYSTEM_PLAYLIST_NAMES:
            continue
        owner_username = row.owner_username or "Unknown"
        source_owner_username = row.source_owner_username or owner_username
        summaries.append({
            "id": row.id,
            "name": row.name,
            "track_count": int(row.track_count or 0),
            "thumbnail": _playlist_cover_url(row.id) if (row.cover_path or row.cover_track_id or int(row.track_count or 0) > 0) else "/static/img/default_cover.png",
            "is_public": bool(row.is_public),
            "source_playlist_id": row.source_playlist_id,
            "owner_username": owner_username,
            "source_owner_username": source_owner_username,
            "is_owner": viewer_id == row.owner_id if viewer_id is not None else False,
            "is_editable": row.source_playlist_id is None and viewer_id == row.owner_id if viewer_id is not None else False,
        })
    return summaries


def serialize_playlist_summary(db: Session, playlist, viewer_id: int | None = None):
    owner_name = playlist.owner.username if playlist.owner else "Unknown"
    source_owner_name = owner_name
    if playlist.source_playlist_id:
        source_playlist = db.query(database.Playlist).filter(
            database.Playlist.id == playlist.source_playlist_id
        ).first()
        if source_playlist and source_playlist.owner:
            source_owner_name = source_playlist.owner.username

    return {
        "id": playlist.id,
        "name": playlist.name,
        "track_count": len(playlist.items),
        "thumbnail": get_playlist_thumbnail(db, playlist),
        "is_public": bool(playlist.is_public),
        "source_playlist_id": playlist.source_playlist_id,
        "owner_username": owner_name,
        "source_owner_username": source_owner_name,
        "is_owner": viewer_id == playlist.owner_id if viewer_id is not None else False,
        "is_editable": playlist.source_playlist_id is None and viewer_id == playlist.owner_id if viewer_id is not None else False,
    }


def sync_playlist_copy_contents(db: Session, source_playlist, target_playlist):
    db.query(database.PlaylistItem).filter(
        database.PlaylistItem.playlist_id == target_playlist.id
    ).delete(synchronize_session=False)

    for source_item in sorted(source_playlist.items, key=lambda x: x.position):
        db.add(database.PlaylistItem(
            playlist_id=target_playlist.id,
            track_id=source_item.track_id,
            position=source_item.position
        ))
    db.commit()
    db.refresh(target_playlist)


def build_unique_copy_name(db: Session, user_id: int, base_name: str, exclude_playlist_id: int | None = None) -> str:
    candidate = base_name.strip() or "Public Playlist"
    query = db.query(database.Playlist.name).filter(database.Playlist.owner_id == user_id)
    if exclude_playlist_id is not None:
        query = query.filter(database.Playlist.id != exclude_playlist_id)
    existing_names = {row[0] for row in query.all() if row[0]}
    if candidate not in existing_names:
        return candidate

    suffix = 2
    while f"{candidate} ({suffix})" in existing_names:
        suffix += 1
    return f"{candidate} ({suffix})"


# --- M3U GENERATION ---

def generate_m3u_for_playlist(db: Session, playlist, username: str):
    """Generate M3U file for a playlist so Navidrome can read it"""
    try:
        playlist_dir = f"{settings.MUSIC_ROOT}/{username}/music/playlists"
        os.makedirs(playlist_dir, mode=0o777, exist_ok=True)
        try:
            os.chmod(playlist_dir, 0o777)
        except:
            pass
        
        # Sanitize playlist name for filename
        safe_name = "".join(c for c in playlist.name if c.isalnum() or c in " -_").strip()
        m3u_path = f"{playlist_dir}/{safe_name}.m3u"
        
        with open(m3u_path, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            for item in sorted(playlist.items, key=lambda x: x.position):
                track = item.track
                if track and track.filepath:
                    if '/pool/' in track.filepath:
                        rel_path = "../Library/" + track.filepath.split('/pool/')[-1]
                    else:
                        rel_path = track.filepath
                    f.write(f"#EXTINF:{track.duration or -1},{track.artist} - {track.title}\n")
                    f.write(f"{rel_path}\n")
            f.flush()
            os.fsync(f.fileno())

        # Update playlist record
        playlist.m3u_path = m3u_path
        db.commit()
        print(f"[M3U] Generated: {m3u_path}")
        return m3u_path
    except Exception as e:
        print(f"[M3U] Error generating: {e}")
        return None


# --- NAVIDROME SYNC ---

# Global debouncer: {username: asyncio.Task}
_playlist_sync_tasks = {}


def schedule_playlist_sync(db, user, playlist_id=None, force_now=False):
    """
    Synchronously schedule a background scan in Navidrome.
    Debounced for 3 seconds to batch rapid updates.
    """
    # 1. Cancel previous pending task
    if user.username in _playlist_sync_tasks:
        try:
            task = _playlist_sync_tasks[user.username]
            task.cancel()
        except Exception as e:
            print(f"[PLAYLIST-SYNC] Cancel error: {e}")
    
    # 2. Define the async worker
    async def delayed_sync_worker():
        try:
            delay = 0.5 if force_now else 3.0
            await asyncio.sleep(delay)
            
            target_ip = manager.get_or_spawn_container(user.username)
            url = f"http://{target_ip}:4533/{user.username}/rest/startScan"
            params = {
                "u": user.username,
                "p": "enc:000000",
                "v": "1.16.1",
                "c": "navipod-concierge",
                "f": "json"
            }
            headers = {"x-navidrome-user": user.username}
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.get(url, params=params, headers=headers)
                action_type = "immediate" if force_now else "batched"
                print(f"[PLAYLIST-SYNC] Triggered {action_type} background scan for {user.username}")
                
        except asyncio.CancelledError:
            print(f"[PLAYLIST-SYNC] Scan cancelled (New update incoming)")
        except Exception as e:
            print(f"[PLAYLIST-SYNC] Error: {e}")

    # 3. Schedule and store new task
    task = asyncio.create_task(delayed_sync_worker())
    _playlist_sync_tasks[user.username] = task


async def clean_remote_playlist(username: str, playlist_name: str):
    """Explicitly delete a playlist from Navidrome via API to prevent ghosting"""
    try:
        target_ip = manager.get_or_spawn_container(username)
        base_url = f"http://{target_ip}:4533/{username}/rest"
        auth_params = {
            "u": username,
            "p": "enc:000000",
            "v": "1.16.1",
            "c": "navipod-concierge",
            "f": "json"
        }
        headers = {"x-navidrome-user": username}
        
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base_url}/getPlaylists", params=auth_params, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                remote_playlists = data.get("subsonic-response", {}).get("playlists", {}).get("playlist", [])
                
                for rp in remote_playlists:
                    if rp.get("name") == playlist_name:
                        rp_id = rp.get("id")
                        print(f"[PLAYLIST-CLEAN] Deleting remote '{playlist_name}' (ID: {rp_id})")
                        del_params = {**auth_params, "id": rp_id}
                        await client.get(f"{base_url}/deletePlaylist", params=del_params, headers=headers)
                        return True
    except Exception as e:
        print(f"[PLAYLIST-CLEAN] Error: {e}")
    return False


# --- API ENDPOINTS ---

@router.get("/api/playlists")
async def list_playlists(request: Request, db: Session = Depends(get_db)):
    """List user's playlists from the local database."""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    return JSONResponse(fetch_playlist_summaries(db, viewer_id=user.id, owner_id=user.id))


@router.get("/api/public/playlists")
async def list_public_playlists(request: Request, db: Session = Depends(get_db)):
    """List all public playlists from all users."""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    return JSONResponse(fetch_playlist_summaries(db, viewer_id=user.id, public_only=True))


@router.post("/api/playlists")
async def create_playlist(req: CreatePlaylistRequest, request: Request, db: Session = Depends(get_db)):
    """Create a new playlist"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    playlist = database.Playlist(name=req.name, owner_id=user.id)
    db.add(playlist)
    db.commit()
    db.refresh(playlist)
    
    # Generate empty M3U
    generate_m3u_for_playlist(db, playlist, user.username)
    
    # Trigger Sync
    schedule_playlist_sync(db, user)
    schedule_navidrome_sync(user.id, user.username, delay_seconds=2.0)

    return JSONResponse({"id": playlist.id, "name": playlist.name})


@router.post("/api/playlists/{playlist_id}/public")
async def set_playlist_public(playlist_id: int, payload: PublishPlaylistRequest, request: Request, db: Session = Depends(get_db)):
    """Publish or unpublish a playlist owned by the current user."""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    playlist = db.query(database.Playlist).filter(
        database.Playlist.id == playlist_id,
        database.Playlist.owner_id == user.id
    ).first()
    if not playlist:
        return JSONResponse({"error": "Playlist not found"}, status_code=404)

    if playlist.source_playlist_id is not None:
        return JSONResponse({"error": "Synced copies cannot be published directly."}, status_code=403)

    playlist.is_public = bool(payload.is_public)
    db.commit()
    schedule_navidrome_sync(user.id, user.username, delay_seconds=2.0)
    return JSONResponse({
        "id": playlist.id,
        "is_public": bool(playlist.is_public)
    })


@router.post("/api/playlists/{playlist_id}/copy")
async def copy_public_playlist(playlist_id: int, request: Request, db: Session = Depends(get_db)):
    """Create or refresh a local synced copy of a public playlist."""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    source_playlist = db.query(database.Playlist).filter(
        database.Playlist.id == playlist_id,
        database.Playlist.is_public == True
    ).first()
    if not source_playlist:
        return JSONResponse({"error": "Public playlist not found"}, status_code=404)

    if source_playlist.owner_id == user.id and source_playlist.source_playlist_id is None:
        return JSONResponse({"error": "This is already your own playlist."}, status_code=400)

    local_copy = db.query(database.Playlist).filter(
        database.Playlist.owner_id == user.id,
        database.Playlist.source_playlist_id == source_playlist.id
    ).first()
    source_owner = source_playlist.owner.username if source_playlist.owner else "Unknown"
    canonical_name = f"{source_playlist.name} - {source_owner}"

    action = "synced"
    if not local_copy:
        copy_name = build_unique_copy_name(
            db,
            user.id,
            canonical_name
        )
        local_copy = database.Playlist(
            name=copy_name,
            owner_id=user.id,
            source_playlist_id=source_playlist.id,
            is_public=False
        )
        db.add(local_copy)
        db.commit()
        db.refresh(local_copy)
        action = "copied"
    else:
        next_name = build_unique_copy_name(
            db,
            user.id,
            canonical_name,
            exclude_playlist_id=local_copy.id
        )
        if local_copy.name != next_name:
            old_name = local_copy.name
            if local_copy.m3u_path and os.path.exists(local_copy.m3u_path):
                try:
                    os.remove(local_copy.m3u_path)
                except Exception as e:
                    print(f"[PLAYLIST-SYNC] Error removing old copy file: {e}")
            await clean_remote_playlist(user.username, old_name)
            local_copy.name = next_name
            db.commit()
            db.refresh(local_copy)

    sync_playlist_copy_contents(db, source_playlist, local_copy)
    generate_m3u_for_playlist(db, local_copy, user.username)
    schedule_playlist_sync(db, user, force_now=True)
    schedule_navidrome_sync(user.id, user.username, delay_seconds=2.0)

    return JSONResponse({
        "status": action,
        "id": local_copy.id,
        "name": local_copy.name,
        "track_count": len(local_copy.items)
    })


@router.get("/api/playlists/{playlist_id}")
async def get_playlist(playlist_id: int, request: Request, db: Session = Depends(get_db)):
    """Get playlist with tracks"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    playlist = get_playlist_or_404(db, playlist_id, user)

    if not playlist:
        return JSONResponse({"error": "Playlist not found"}, status_code=404)

    source_playlist_exists = False
    source_playlist_public = False
    if playlist.source_playlist_id:
        source_playlist = db.query(database.Playlist).filter(
            database.Playlist.id == playlist.source_playlist_id
        ).first()
        source_playlist_exists = source_playlist is not None
        source_playlist_public = bool(source_playlist and source_playlist.is_public)

    thumbnail = get_playlist_thumbnail(db, playlist)
    tracks = []
    for item in sorted(playlist.items, key=lambda x: x.position):
        t = item.track
        track_data = {
            "id": t.id,
            "title": t.title,
            "artist": t.artist,
            "album": t.album,
            "thumbnail": f"/api/cover/{t.id}",
            "position": item.position
        }
        tracks.append(track_data)
        if tracks and thumbnail == "/static/img/default_cover.png":
             thumbnail = track_data["thumbnail"]
    
    return JSONResponse({
        "id": playlist.id,
        "name": playlist.name,
        "tracks": tracks,
        "thumbnail": thumbnail,
        "owner_username": playlist.owner.username if playlist.owner else "Unknown",
        "is_public": bool(playlist.is_public),
        "source_playlist_id": playlist.source_playlist_id,
        "source_playlist_exists": source_playlist_exists,
        "source_playlist_public": source_playlist_public,
        "is_owner": playlist.owner_id == user.id,
        "is_editable": playlist_is_editable_by_user(playlist, user),
        "is_read_only": not playlist_is_editable_by_user(playlist, user),
        "cover_track_id": playlist.cover_track_id,
        "has_custom_cover": bool(playlist.cover_path),
    })


@router.get("/api/playlists/{playlist_id}/cover")
async def get_playlist_cover(playlist_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return RedirectResponse("/static/img/default_cover.png")

    playlist = get_playlist_or_404(db, playlist_id, user)
    if not playlist:
        return RedirectResponse("/static/img/default_cover.png")

    if playlist.cover_path and os.path.exists(playlist.cover_path):
        return FileResponse(
            playlist.cover_path,
            media_type="image/webp",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    if playlist.cover_track_id:
        return RedirectResponse(f"/api/cover/{playlist.cover_track_id}")

    first_item = db.query(database.PlaylistItem).filter(
        database.PlaylistItem.playlist_id == playlist.id
    ).order_by(database.PlaylistItem.position).first()
    if first_item and first_item.track:
        return RedirectResponse(f"/api/cover/{first_item.track.id}")

    return RedirectResponse("/static/img/default_cover.png")


@router.post("/api/playlists/{playlist_id}/cover/upload")
async def upload_playlist_cover(
    playlist_id: int,
    request: Request,
    cover_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    playlist = db.query(database.Playlist).filter(
        database.Playlist.id == playlist_id,
        database.Playlist.owner_id == user.id,
    ).first()
    if not playlist:
        return JSONResponse({"error": "Playlist not found"}, status_code=404)

    content = await cover_file.read()
    if len(content) > MAX_PLAYLIST_COVER_SIZE:
        return JSONResponse({"error": f"Image too large. Max size: {MAX_PLAYLIST_COVER_SIZE // (1024 * 1024)}MB"}, status_code=400)

    filename = cover_file.filename or ""
    ext = filename.lower().split(".")[-1] if "." in filename else ""
    if ext not in {"jpg", "jpeg", "png", "webp", "gif"}:
        return JSONResponse({"error": "Invalid file type. Allowed: JPG, PNG, WEBP, GIF"}, status_code=400)

    content_type = cover_file.content_type or ""
    if not _validate_playlist_cover_bytes(content, content_type):
        return JSONResponse({"error": "File content does not match declared type"}, status_code=400)

    try:
        img = Image.open(io.BytesIO(content))
        img = img.convert("RGB")
        img.thumbnail(PLAYLIST_COVER_SIZE, Image.Resampling.LANCZOS)
    except Exception:
        return JSONResponse({"error": "Invalid or corrupt image"}, status_code=400)

    cover_dir = _playlist_cover_dir(user.username)
    cover_filename = f"playlist_{playlist.id}_{uuid.uuid4().hex[:10]}.webp"
    cover_path = os.path.join(cover_dir, cover_filename)

    _remove_playlist_cover_file(playlist)
    img.save(cover_path, "WEBP", quality=86)
    playlist.cover_path = cover_path
    playlist.cover_track_id = None
    db.commit()

    return JSONResponse({"status": "ok", "thumbnail": _playlist_cover_url(playlist.id)})


@router.post("/api/playlists/{playlist_id}/cover/track")
async def set_playlist_cover_from_track(
    playlist_id: int,
    payload: PlaylistCoverTrackRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    playlist = db.query(database.Playlist).filter(
        database.Playlist.id == playlist_id,
        database.Playlist.owner_id == user.id,
    ).first()
    if not playlist:
        return JSONResponse({"error": "Playlist not found"}, status_code=404)

    item = db.query(database.PlaylistItem).filter(
        database.PlaylistItem.playlist_id == playlist.id,
        database.PlaylistItem.track_id == payload.track_id,
    ).first()
    if not item:
        return JSONResponse({"error": "Track is not part of this playlist"}, status_code=400)

    _remove_playlist_cover_file(playlist)
    playlist.cover_path = None
    playlist.cover_track_id = payload.track_id
    db.commit()

    return JSONResponse({"status": "ok", "thumbnail": _playlist_cover_url(playlist.id)})


@router.delete("/api/playlists/{playlist_id}/cover")
async def reset_playlist_cover(playlist_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    playlist = db.query(database.Playlist).filter(
        database.Playlist.id == playlist_id,
        database.Playlist.owner_id == user.id,
    ).first()
    if not playlist:
        return JSONResponse({"error": "Playlist not found"}, status_code=404)

    _remove_playlist_cover_file(playlist)
    playlist.cover_path = None
    playlist.cover_track_id = None
    db.commit()

    return JSONResponse({"status": "ok", "thumbnail": _playlist_cover_url(playlist.id)})


@router.post("/api/playlists/{playlist_id}/add")
async def add_to_playlist(playlist_id: int, req: AddToPlaylistRequest, request: Request, db: Session = Depends(get_db)):
    """Add track to playlist"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    playlist = db.query(database.Playlist).filter(
        database.Playlist.id == playlist_id,
        database.Playlist.owner_id == user.id
    ).first()
    
    if not playlist:
        return JSONResponse({"error": "Playlist not found"}, status_code=404)

    if not playlist_is_editable_by_user(playlist, user):
        return JSONResponse({"error": "This playlist is read-only. Sync it from the public source instead."}, status_code=403)
    
    # Check if track exists
    track = db.query(database.Track).filter(database.Track.id == req.track_id).first()
    if not track:
        return JSONResponse({"error": "Track not found"}, status_code=404)
    
    # Check if already in playlist
    existing = db.query(database.PlaylistItem).filter(
        database.PlaylistItem.playlist_id == playlist_id,
        database.PlaylistItem.track_id == req.track_id
    ).first()
    
    if existing:
        return JSONResponse({"error": "Track already in playlist"}, status_code=400)
    
    # Add at end
    max_pos = db.query(func.max(database.PlaylistItem.position)).filter(
        database.PlaylistItem.playlist_id == playlist_id
    ).scalar() or 0
    
    item = database.PlaylistItem(
        playlist_id=playlist_id,
        track_id=req.track_id,
        position=max_pos + 1
    )
    db.add(item)
    db.commit()
    
    # Regenerate M3U
    generate_m3u_for_playlist(db, playlist, user.username)
    schedule_playlist_sync(db, user)
    schedule_navidrome_sync(user.id, user.username, delay_seconds=2.0)
    
    return JSONResponse({"status": "added", "position": max_pos + 1})


@router.delete("/api/playlists/{playlist_id}/remove/{track_id}")
async def remove_from_playlist(playlist_id: int, track_id: int, request: Request, db: Session = Depends(get_db)):
    """Remove track from playlist"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    playlist = db.query(database.Playlist).filter(
        database.Playlist.id == playlist_id,
        database.Playlist.owner_id == user.id
    ).first()
    
    if not playlist:
        return JSONResponse({"error": "Playlist not found"}, status_code=404)

    if not playlist_is_editable_by_user(playlist, user):
        return JSONResponse({"error": "This playlist is read-only. Sync it from the public source instead."}, status_code=403)
    
    item = db.query(database.PlaylistItem).filter(
        database.PlaylistItem.playlist_id == playlist_id,
        database.PlaylistItem.track_id == track_id
    ).first()
    
    if not item:
        return JSONResponse({"error": "Track not in playlist"}, status_code=404)
    
    db.delete(item)
    db.commit()
    
    # Regenerate M3U
    generate_m3u_for_playlist(db, playlist, user.username)
    schedule_playlist_sync(db, user)
    schedule_navidrome_sync(user.id, user.username, delay_seconds=2.0)
    
    return JSONResponse({"status": "removed"})


@router.delete("/api/playlists/{playlist_id}")
async def delete_playlist(playlist_id: int, request: Request, db: Session = Depends(get_db)):
    """Delete playlist"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    playlist = db.query(database.Playlist).filter(
        database.Playlist.id == playlist_id,
        database.Playlist.owner_id == user.id
    ).first()
    
    if not playlist:
        return JSONResponse({"error": "Playlist not found"}, status_code=404)
    
    # Delete M3U file
    if playlist.m3u_path and os.path.exists(playlist.m3u_path):
        try:
            os.remove(playlist.m3u_path)
        except Exception as e:
            print(f"[PLAYLIST-DEL] Error removing file: {e}")
    _remove_playlist_cover_file(playlist)

    # Explicit Sync: Clean remote playlist immediately
    await clean_remote_playlist(user.username, playlist.name)
    
    db.delete(playlist)
    db.commit()

    # Trigger scan for consistency
    schedule_playlist_sync(db, user, force_now=True)
    schedule_navidrome_sync(user.id, user.username, delay_seconds=2.0)
    
    return JSONResponse({"status": "deleted"})


@router.put("/api/playlists/{playlist_id}")
async def update_playlist(playlist_id: int, payload: PlaylistUpdateRequest, request: Request, db: Session = Depends(get_db)):
    """Update playlist name"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    playlist = db.query(database.Playlist).filter(
        database.Playlist.id == playlist_id,
        database.Playlist.owner_id == user.id
    ).first()
    
    if not playlist:
        return JSONResponse({"error": "Playlist not found"}, status_code=404)

    if not playlist_is_editable_by_user(playlist, user):
        return JSONResponse({"error": "Synced copies cannot be renamed manually."}, status_code=403)
    
    old_name = playlist.name
    
    # 1. Delete old M3U file
    if playlist.m3u_path and os.path.exists(playlist.m3u_path):
        try:
            os.remove(playlist.m3u_path)
        except:
            pass
    
    # 2. Clean old remote playlist
    await clean_remote_playlist(user.username, old_name)
    
    # 3. Update playlist name
    playlist.name = payload.name
    db.commit()
    
    # 4. Generate new M3U
    generate_m3u_for_playlist(db, playlist, user.username)
    
    # 5. Trigger scan
    schedule_playlist_sync(db, user, force_now=True)
    schedule_navidrome_sync(user.id, user.username, delay_seconds=2.0)
    
    return JSONResponse({"id": playlist.id, "name": playlist.name})
