"""
Playlist management and Navidrome sync.
"""
import os
import asyncio
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel as PydanticBaseModel
import httpx

import database
import manager
from navipod_config import settings

from .core import get_db, get_current_user_safe
from .favorites import sync_navidrome_to_local


router = APIRouter()


# --- PYDANTIC MODELS ---

class CreatePlaylistRequest(PydanticBaseModel):
    name: str


class PlaylistUpdateRequest(PydanticBaseModel):
    name: str


class AddToPlaylistRequest(PydanticBaseModel):
    track_id: int


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
    """List user's playlists (with 2-way sync)"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    # 2-WAY SYNC
    await sync_navidrome_to_local(db, user)
    
    playlists = db.query(database.Playlist).filter(database.Playlist.owner_id == user.id).all()
    
    # Filter out system folders
    filtered = []
    system_names = ["music", "pool", "users", "podcasts", "downloads"]
    for p in playlists:
        if p.name.lower() not in system_names:
            thumbnail = "/static/img/default_cover.png"
            first_item = db.query(database.PlaylistItem).filter(
                database.PlaylistItem.playlist_id == p.id
            ).order_by(database.PlaylistItem.position).first()
            if first_item and first_item.track:
                thumbnail = f"/api/cover/{first_item.track.id}"

            filtered.append({
                "id": p.id,
                "name": p.name,
                "track_count": len(p.items),
                "thumbnail": thumbnail
            })
            
    return JSONResponse(filtered)


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

    return JSONResponse({"id": playlist.id, "name": playlist.name})


@router.get("/api/playlists/{playlist_id}")
async def get_playlist(playlist_id: int, request: Request, db: Session = Depends(get_db)):
    """Get playlist with tracks"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    playlist = db.query(database.Playlist).filter(
        database.Playlist.id == playlist_id,
        database.Playlist.owner_id == user.id
    ).first()
    
    if not playlist:
        return JSONResponse({"error": "Playlist not found"}, status_code=404)
    
    thumbnail = "/static/img/default_cover.png"
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
        "thumbnail": thumbnail
    })


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

    # Explicit Sync: Clean remote playlist immediately
    await clean_remote_playlist(user.username, playlist.name)
    
    db.delete(playlist)
    db.commit()

    # Trigger scan for consistency
    schedule_playlist_sync(db, user, force_now=True)
    
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
    
    return JSONResponse({"id": playlist.id, "name": playlist.name})
