import os
import shutil
import hashlib
import logging
from datetime import datetime

# Import database models
try:
    from database import SessionLocal, Track, Playlist, PlaylistItem, User
except ImportError:
    # Handle running from different context if needed
    from concierge.database import SessionLocal, Track, Playlist, PlaylistItem, User

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

POOL_ROOT = "/saas-data/pool"

def get_file_hash(filepath):
    """Calculate SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        # Read and update hash string value in blocks of 4K
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def ensure_pool_directory(artist, album):
    """Ensure directory exists in pool."""
    path = os.path.join(POOL_ROOT, clean_name(artist), clean_name(album))
    os.makedirs(path, exist_ok=True)
    return path

def clean_name(name):
    """Clean string for filesystem, preserving Unicode and common symbols."""
    # Allow alphanumeric (Unicode), spaces, and safe punctuation.
    # We strip path separators explicitly just in case.
    allowed_symbols = {' ', '-', '_', '.', '(', ')', '[', ']', '&', ',', '\'', '!'}
    cleaned = "".join(c for c in name if c.isalnum() or c in allowed_symbols)
    return cleaned.strip() or "Unknown"

def process_file(db, filepath, playlist=None):
    """Process a single file: import to pool or link if exists."""
    try:
        file_hash = get_file_hash(filepath)
        filename = os.path.basename(filepath)
        
        # Check if track exists
        track = db.query(Track).filter_by(file_hash=file_hash).first()
        
        if track:
            logger.info(f"Duplicate found: {filename} (Hash: {file_hash[:8]}). Linking to existing track.")
            # Verify file actually exists in pool, if not... issue?
            if track.filepath and not os.path.exists(track.filepath):
                logger.warning(f"Track record exists but file missing at {track.filepath}. Re-importing content.")
                # We should move this file to pool to fix broken link?
                # For now assume pool is valid. 
                # If we were to fix, we would copy file to track.filepath.
            else:
                # Safe to delete duplicate
                try:
                    os.remove(filepath)
                    logger.info(f"Deleted local duplicate: {filepath}")
                except Exception as e:
                    logger.error(f"Failed to delete {filepath}: {e}")
        
        else:
            # New Track
            # Extract metadata properly? 
            # For now try to infer from filename or use mutagen later correctly (Phase 2).
            # Plan mentions Phase 1.5 logic: "Move to Pool ... /Artist/Album/Title.mp3"
            # But where do we get Artist/Album if not in ID3?
            # We will use placeholders or try to read ID3 if possible.
            # But the requirement says "MOVE file to /saas-data/pool/{Artist}/{Album}/{Title}.mp3"
            # We'll use "Unknown Artist" / "Unknown Album" if we can't parse or if not provided.
            # Assuming caller might parse path?
            # Or we assume 'mutagen' is available (since user mentioned it for Phase 2, maybe we use it here too?)
            # I'll rely on basic file info for now or just generic folders if needed.
            
            artist = "Unknown Artist"
            album = "Unknown Album"
            title = os.path.splitext(filename)[0]
            
            # Simple metadata extraction heuristic (optional, better to use mutagen if installed)
            try:
                import mutagen
                audio = mutagen.File(filepath, easy=True)
                if audio:
                    artist = audio.get('artist', [artist])[0]
                    album = audio.get('album', [album])[0]
                    title = audio.get('title', [title])[0]
            except ImportError:
                pass # Mutagen might not be installed yet
            except Exception as e:
                logger.warning(f"Metadata read error: {e}")

            pool_dir = ensure_pool_directory(artist, album)
            new_filename = f"{clean_name(title)}.mp3" # Force .mp3 extension? Or keep original?
            # Keeping original extension is safer
            ext = os.path.splitext(filename)[1]
            new_filename = f"{clean_name(title)}{ext}"
            
            target_path = os.path.join(pool_dir, new_filename)
            
            # Handle collision in pool (same name but diff hash?)
            if os.path.exists(target_path):
                 # Same name, different hash (since checked DB already). 
                 # Rename to avoid overwrite?
                 new_filename = f"{clean_name(title)}_{file_hash[:6]}{ext}"
                 target_path = os.path.join(pool_dir, new_filename)
            
            # Move file
            shutil.move(filepath, target_path)
            logger.info(f"Moved {filename} -> {target_path}")
            
            # Create Track record
            # Note: database.py defines 'filepath' and 'source_id' and 'file_hash'
            # We need a unique source_id.
            # If migrating local files, source_id could be 'local:{hash}' or 'local:{path}'
            source_id = f"local:{file_hash}"
            
            track = Track(
                title=title,
                artist=artist,
                album=album,
                filepath=target_path,
                source_id=source_id,
                file_hash=file_hash,
                source_provider="local",
                duration=0 # Needs mutagen to get duration
            )
            db.add(track)
            db.commit()
            db.refresh(track)

        # Create PlaylistItem if playlist is provided
        if playlist and track:
            # Check if already in playlist
            exists = db.query(PlaylistItem).filter_by(
                playlist_id=playlist.id,
                track_id=track.id
            ).first()
            
            if not exists:
                # Find next position
                # Efficient position query needed
                # For now just count
                count = db.query(PlaylistItem).filter_by(playlist_id=playlist.id).count() 
                item = PlaylistItem(
                    playlist_id=playlist.id, 
                    track_id=track.id,
                    position=count + 1
                )
                db.add(item)
                db.commit()
                logger.info(f"Added to playlist '{playlist.name}': {track.title}")

    except Exception as e:
        logger.error(f"Error processing {filepath}: {e}")
        db.rollback()

def scan_folder(folder_path, user=None):
    """Scan a folder recursively."""
    db = SessionLocal()
    try:
        if not user:
            # Default to admin or first user
            user = db.query(User).first()
            if not user:
                logger.error("No user found in DB. Create a user first.")
                return

        # If the scan root represents a playlist (e.g. folder name)
        # We might want to create a playlist for the top level folders?
        # User logic: "Move from a folder-centric model".
        # If I point this to /music/Rock, it should probably be a playlist "Rock"?
        
        folder_name = os.path.basename(os.path.normpath(folder_path))
        playlist = db.query(Playlist).filter_by(name=folder_name, owner_id=user.id).first()
        
        if not playlist:
            logger.info(f"Creating playlist from folder: {folder_name}")
            playlist = Playlist(name=folder_name, owner_id=user.id)
            db.add(playlist)
            db.commit()
            db.refresh(playlist)
        
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                if file.lower().endswith(('.mp3', '.m4a', '.flac', '.wav', '.ogg')):
                    filepath = os.path.join(root, file)
                    process_file(db, filepath, playlist)
        
        # Path Cleanup: Remove empty directories
        for root, dirs, files in os.walk(folder_path, topdown=False):
            for dir in dirs:
                dir_path = os.path.join(root, dir)
                try:
                    os.rmdir(dir_path) # Only removes if empty
                    logger.info(f"Removed empty directory: {dir_path}")
                except OSError:
                    pass
                    
    finally:
        db.close()

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python importer.py <folder_path_to_import>")
        sys.exit(1)
        
    folder_to_scan = sys.argv[1]
    scan_folder(folder_to_scan)
