"""
Favorites management and Navidrome sync.
"""
import os
import asyncio
from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
import httpx

import database
import manager
from navipod_config import settings

from .core import get_db, get_current_user_safe


router = APIRouter()


# --- M3U GENERATION ---

def generate_favorites_m3u(db: Session, user):
    """Generate Liked Songs M3U for user"""
    try:
        playlist_dir = f"{settings.MUSIC_ROOT}/{user.username}/music/playlists"
        os.makedirs(playlist_dir, mode=0o777, exist_ok=True)
        try:
            os.chmod(playlist_dir, 0o777)
        except:
            pass
        m3u_path = f"{playlist_dir}/Liked Songs.m3u"
        
        with open(m3u_path, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            for fav in user.favorites:
                track = fav.track
                if track and track.filepath:
                    if '/pool/' in track.filepath:
                        rel_path = "../Library/" + track.filepath.split('/pool/')[-1]
                    else:
                        rel_path = track.filepath
                    f.write(f"#EXTINF:{track.duration or -1},{track.artist} - {track.title}\n")
                    f.write(f"{rel_path}\n")
        
        print(f"[M3U] Favorites generated: {m3u_path}")
        return m3u_path
    except Exception as e:
        print(f"[M3U] Favorites error: {e}")
        return None


# --- NAVIDROME SYNC ---

async def sync_favorite_to_navidrome(db: Session, user, track, is_starred: bool):
    """Synchronize favorite status with Navidrome via Subsonic API"""
    try:
        target_ip = manager.get_or_spawn_container(user.username)
        base_params = {
            "u": user.username,
            "p": "enc:000000",
            "v": "1.16.1",
            "c": "navipod-concierge",
            "f": "json"
        }
        headers = {"x-navidrome-user": user.username}
        
        # 1. Search for track in Navidrome to get its ID
        search_url = f"http://{target_ip}:4533/{user.username}/rest/search3"
        
        # Clean query: sometimes artist has slashes or multiple artists
        clean_artist = track.artist.split('/')[0].split(',')[0].strip() if track.artist else ""
        query = f"{clean_artist} {track.title}".strip()
        
        search_params = {**base_params, "query": query, "songCount": 20}
        
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(search_url, params=search_params, headers=headers)
            if resp.status_code == 200:
                results = resp.json().get("subsonic-response", {}).get("searchResult3", {}).get("song", [])
                if isinstance(results, dict):
                    results = [results]
                
                navidrome_track_id = None
                track_filename = os.path.basename(track.filepath) if track.filepath else None
                
                for candidate in results:
                    if track_filename and track_filename in (candidate.get("path") or ""):
                        navidrome_track_id = candidate.get("id")
                        break
                    if candidate.get("title") == track.title and candidate.get("artist") == track.artist:
                        navidrome_track_id = candidate.get("id")
                        break
                
                # Fallback to first result
                if not navidrome_track_id and results:
                    navidrome_track_id = results[0].get("id")
                
                if navidrome_track_id:
                    # 2. Star/Unstar
                    action = "star" if is_starred else "unstar"
                    action_url = f"http://{target_ip}:4533/{user.username}/rest/{action}"
                    action_params = {**base_params, "id": navidrome_track_id}
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        await client.get(action_url, params=action_params, headers=headers)
                    print(f"[FAV-SYNC] Successfully {action}ed '{track.title}' (ID: {navidrome_track_id}) in Navidrome")
                else:
                    print(f"[FAV-SYNC] Track '{track.title}' not found in Navidrome")
    except Exception as e:
        print(f"[FAV-SYNC] Error: {e}")


async def sync_navidrome_to_local(db: Session, user):
    """
    Two-way sync: Pull Starred tracks and Playlists from Navidrome 
    and update local Navipod database.
    """
    try:
        target_ip = manager.get_or_spawn_container(user.username)
        base_params = {
            "u": user.username,
            "p": "enc:000000",
            "v": "1.16.1",
            "c": "navipod-concierge",
            "f": "json"
        }
        headers = {"x-navidrome-user": user.username}
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. SYNC FAVORITES (Starred)
            starred_url = f"http://{target_ip}:4533/{user.username}/rest/getStarred"
            resp = await client.get(starred_url, params=base_params, headers=headers)
            if resp.status_code == 200:
                starred_data = resp.json().get("subsonic-response", {}).get("starred", {}).get("song", [])
                if isinstance(starred_data, dict):
                    starred_data = [starred_data]
                
                # Get current local favorites
                local_favs = {f.track.artist + " " + f.track.title: f for f in user.favorites if f.track}
                navidrome_starred_titles = set()
                
                for song in starred_data:
                    title = song.get("title")
                    artist = song.get("artist")
                    key = f"{artist} {title}"
                    navidrome_starred_titles.add(key)
                    
                    if key not in local_favs:
                        # Try to find track locally
                        track = db.query(database.Track).filter(
                             (database.Track.title == title) & (database.Track.artist == artist)
                        ).first()
                        if track:
                            new_fav = database.UserFavorite(user_id=user.id, track_id=track.id)
                            db.add(new_fav)
                            print(f"[SYNC-BACK] Added '{title}' to local favorites (Starred in Navidrome)")
                
                db.commit()

            # 2. SYNC PLAYLISTS (API Check)
            pl_url = f"http://{target_ip}:4533/{user.username}/rest/getPlaylists"
            resp_pl = await client.get(pl_url, params=base_params, headers=headers)
            if resp_pl.status_code == 200:
                nd_playlists = resp_pl.json().get("subsonic-response", {}).get("playlists", {}).get("playlist", [])
                if isinstance(nd_playlists, dict):
                    nd_playlists = [nd_playlists]
                
                nd_playlist_names = {p.get("name") for p in nd_playlists}
                local_playlists = db.query(database.Playlist).filter(database.Playlist.owner_id == user.id).all()
                
                for pl in local_playlists:
                    if pl.name not in nd_playlist_names:
                        playlist_dir = f"{settings.MUSIC_ROOT}/{user.username}/music/playlists"
                        m3u_path = os.path.join(playlist_dir, pl.name + ".m3u")
                        
                        if not os.path.exists(m3u_path):
                            print(f"[SYNC-BACK] Playlist '{pl.name}' missing in Navidrome and disk. Deleting.")
                            db.delete(pl)
                
                db.commit()

    except Exception as e:
        print(f"[SYNC-BACK] Error: {e}")


# --- API ENDPOINTS ---

@router.get("/api/favorites")
async def list_favorites(request: Request, db: Session = Depends(get_db)):
    """Get user's favorite tracks (with 2-way sync)"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    # 2-WAY SYNC
    await sync_navidrome_to_local(db, user)
    
    try:
        favorites = db.query(database.UserFavorite).filter(
            database.UserFavorite.user_id == user.id
        ).all()
        
        return JSONResponse([{
            "id": f.track.id,
            "db_id": f.track.id,
            "title": f.track.title,
            "artist": f.track.artist,
            "album": f.track.album,
            "thumbnail": f"/api/cover/{f.track.id}",
            "added_at": str(f.added_at)
        } for f in favorites if f.track])
    except Exception as e:
        print(f"[FAVORITES] Error: {e}")
        return JSONResponse([])


@router.post("/api/favorites/{track_id}")
async def add_favorite(track_id: int, request: Request, db: Session = Depends(get_db)):
    """Like a track"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    # Check if track exists
    track = db.query(database.Track).filter(database.Track.id == track_id).first()
    if not track:
        return JSONResponse({"error": "Track not found"}, status_code=404)
    
    # Check if already favorited
    existing = db.query(database.UserFavorite).filter(
        database.UserFavorite.user_id == user.id,
        database.UserFavorite.track_id == track_id
    ).first()
    
    if existing:
        return JSONResponse({"status": "already_liked"})
    
    fav = database.UserFavorite(user_id=user.id, track_id=track_id)
    db.add(fav)
    db.commit()
    
    # Regenerate Liked Songs M3U
    generate_favorites_m3u(db, user)
    
    # Sync with Navidrome Star system (Background)
    asyncio.create_task(sync_favorite_to_navidrome(db, user, track, True))
    
    return JSONResponse({"status": "liked", "liked": True})


@router.delete("/api/favorites/{track_id}")
async def remove_favorite(track_id: int, request: Request, db: Session = Depends(get_db)):
    """Unlike a track"""
    user = get_current_user_safe(db, request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    fav = db.query(database.UserFavorite).filter(
        database.UserFavorite.user_id == user.id,
        database.UserFavorite.track_id == track_id
    ).first()
    
    if not fav:
        return JSONResponse({"error": "Not in favorites"}, status_code=404)
    
    db.delete(fav)
    db.commit()
    
    # Regenerate Liked Songs M3U
    generate_favorites_m3u(db, user)
    
    # Sync with Navidrome Star system (Background)
    track = db.query(database.Track).filter(database.Track.id == track_id).first()
    if track:
        asyncio.create_task(sync_favorite_to_navidrome(db, user, track, False))
    
    return JSONResponse({"status": "unliked", "liked": False})
