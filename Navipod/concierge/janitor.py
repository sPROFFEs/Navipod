import os
import sys
import logging
import time
from sqlalchemy.orm import Session
from database import SessionLocal, Playlist, Track, User

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

from navipod_config import settings

def sync_playlists(db: Session):
    """
    Generates .m3u files for all user playlists.
    Navidrome expects .m3u files in the music folder.
    We place them in /saas-data/users/{username}/music/{PlaylistName}.m3u
    referencing files in pool/{Artist}/{Album}/{Title}.mp3
    """
    logger.info("Starting Playlist Sync...")
    
    users = db.query(User).all()
    for user in users:
        user_music_root = os.path.join(settings.MUSIC_ROOT, user.username, "music")
        if not os.path.exists(user_music_root):
            logger.warning(f"Music root not found for {user.username}, skipping.")
            continue
            
        playlists = db.query(Playlist).filter(Playlist.owner_id == user.id).all()
        for pl in playlists:
            try:
                # Sanitize filename
                safe_name = "".join(c for c in pl.name if c.isalnum() or c in (' ', '-', '_')).strip()
                if not safe_name: safe_name = f"Playlist_{pl.id}"
                
                m3u_path = os.path.join(user_music_root, f"{safe_name}.m3u")
                
                with open(m3u_path, 'w', encoding='utf-8') as f:
                    f.write("#EXTM3U\n")
                    
                    # Sort by position
                    items = sorted(pl.items, key=lambda x: x.position)
                    
                    for item in items:
                        track = item.track
                        if not track or not track.filepath: continue
                        
                        # Path Translation
                        # DB has /saas-data/pool/...
                        # Container sees /music/pool/...
                        # M3U is in /music/
                        # Relative path: pool/...
                        
                        # Assuming track.filepath starts with /saas-data/pool/
                        # We strip the prefix and prepend "pool/"
                        # Or simpler: we just need relative path from "music" directory?
                        # If pool is mounted at /music/pool
                        # And track is /saas-data/pool/Artist/Song.mp3
                        
                        # Fix: Check if track is actually in pool
                        if "/pool/" in track.filepath:
                            # Extract relative path inside pool
                            # e.g. /saas-data/pool/Artist/Song.mp3 -> Artist/Song.mp3
                            # Then prepend pool/
                            
                            # Robust split
                            rel_path = track.filepath.split("/pool/", 1)[-1]
                            container_path = f"pool/{rel_path}"
                            f.write(f"{container_path}\n")
                        else:
                            # It might be a legacy file in user folder?
                            # If so, write relative path if possible, or skip?
                            # For Phase 2/4 we assume Pool.
                            pass
                            
                logger.info(f"Synced Playlist: {pl.name} for {user.username}")
                
            except Exception as e:
                logger.error(f"Error syncing {pl.name}: {e}")

    logger.info("Playlist Sync Complete.")

def reap_orphans(db: Session):
    """
    Remove .m3u files from user music directories that have no matching Playlist
    record in the database.  Skips users whose directory doesn't exist.
    Safe to call at any time; errors on individual files are logged and skipped.
    """
    users = db.query(User).all()
    removed = 0
    for user in users:
        user_music_root = os.path.join(settings.MUSIC_ROOT, user.username, "music")
        if not os.path.isdir(user_music_root):
            continue

        # Build the set of M3U filenames that *should* exist for this user
        playlists = db.query(Playlist).filter(Playlist.owner_id == user.id).all()
        expected_names: set[str] = set()
        for pl in playlists:
            safe_name = "".join(c for c in pl.name if c.isalnum() or c in (' ', '-', '_')).strip()
            if not safe_name:
                safe_name = f"Playlist_{pl.id}"
            expected_names.add(f"{safe_name}.m3u")

        # Remove any .m3u file that has no corresponding playlist record
        try:
            for fname in os.listdir(user_music_root):
                if not fname.endswith(".m3u"):
                    continue
                if fname not in expected_names:
                    fpath = os.path.join(user_music_root, fname)
                    try:
                        os.remove(fpath)
                        logger.info("Removed orphan M3U: %s", fpath)
                        removed += 1
                    except OSError as err:
                        logger.warning("Could not remove orphan %s: %s", fpath, err)
        except OSError as err:
            logger.warning("Could not list directory %s: %s", user_music_root, err)

    logger.info("Orphan reap complete: %d file(s) removed.", removed)

def main():
    db = SessionLocal()
    try:
        sync_playlists(db)
        reap_orphans(db)
    finally:
        db.close()

if __name__ == "__main__":
    main()
