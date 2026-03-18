import sys
import os
import shutil
import time
from unittest.mock import MagicMock

# Mock docker module globally BEFORE importing manager or downloader_service
sys.modules['docker'] = MagicMock()

# Mock navipod_config
mock_config = MagicMock()
mock_settings = MagicMock()
mock_settings.CONCURRENT_DOWNLOADS = 3
mock_settings.MUSIC_ROOT = "/saas-data/users"
mock_settings.HOST_DATA_ROOT = "/saas-data/users"
mock_config.settings = mock_settings
sys.modules['navipod_config'] = mock_config

# Add parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, Track, Playlist, PlaylistItem, User, DownloadJob, engine, Base
from downloader_service import DownloadManager

# Mocking the actual download to avoid internet usage and speed up test
class MockDownloadManager(DownloadManager):
    def _handle_ytdlp_robust(self, url, folder, job_id):
        print(f"MOCK: Downloading {url} to {folder}")
        # Create a dummy mp3
        with open(os.path.join(folder, "Mock Artist - Mock Title.mp3"), "wb") as f:
            f.write(b"fake mp3 audio content")
        return True

    def _handle_spotify_robust(self, url, folder, job_id):
        print(f"MOCK: Downloading Spotify {url} to {folder}")
        with open(os.path.join(folder, "Spotify Artist - Spotify Title.mp3"), "wb") as f:
            f.write(b"fake mp3 audio content")
        return True

def test_downloader():
    print("Setting up test environment...")
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    # Ensure user
    user = db.query(User).filter_by(username="dl_tester").first()
    if not user:
        user = User(username="dl_tester", hashed_password="pw")
        db.add(user)
        db.commit()
        db.refresh(user)

    # Setup directories
    pool_root = "c:/saas-data/pool"
    # Ensure pool root exists (we already created c:/saas-data in Phase 1 verification)
    os.makedirs(pool_root, exist_ok=True)
    
    # 1. Create a Job (New Playlist)
    job = DownloadJob(
        user_id=user.id,
        input_url="https://youtube.com/watch?v=mock123", # Mock ID
        new_playlist_name="Mock Playlist",
        status="pending"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    print(f"Created Job ID: {job.id}")
    
    # 2. Process Job
    manager = MockDownloadManager(db, user.id)
    # We override internal music root to avoid cluttering real user folder if existing
    manager.music_root = "c:/saas-data/test_dl_root"
    os.makedirs(manager.music_root, exist_ok=True)
    
    try:
        manager._process_download_sync(job.id) # Call sync directly
        
        # 3. Verify
        job = db.query(DownloadJob).get(job.id)
        print(f"Job Status: {job.status}")
        if job.status != "completed":
            print(f"FAIL: Job failed with error: {job.error_log}")
            return

        # Check Playlist
        pl = db.query(Playlist).filter_by(name="Mock Playlist", owner_id=user.id).first()
        if pl:
            print(f"PASS: Playlist '{pl.name}' created.")
            if len(pl.items) > 0:
                print(f"PASS: Playlist has {len(pl.items)} items.")
                track = pl.items[0].track
                print(f"PASS: Linked Track: {track.title} by {track.artist}")
                print(f"      Path: {track.filepath}")
                if os.path.exists(track.filepath):
                    print("PASS: File exists in pool.")
                else:
                    print("FAIL: File not found in pool.")
                    
                # Check Deduplication
                print("Testing Deduplication...")
                # Create another job with same URL (Source ID same)
                job2 = DownloadJob(
                    user_id=user.id,
                    input_url="https://youtube.com/watch?v=mock123", # SAME ID
                    new_playlist_name="Mock Playlist 2",
                    status="pending"
                )
                db.add(job2)
                db.commit()
                
                manager._process_download_sync(job2.id)
                pl2 = db.query(Playlist).filter_by(name="Mock Playlist 2").first()
                if pl2 and len(pl2.items) > 0:
                    t2 = pl2.items[0].track
                    if t2.id == track.id:
                        print("PASS: Deduplication worked. Reused same Track ID.")
                    else:
                        print(f"FAIL: Logic created a NEW track ID {t2.id} instead of reusing {track.id}")
                else:
                    print("FAIL: Playlist 2 not created or empty.")

            else:
                print("FAIL: Playlist empty.")
        else:
            print("FAIL: Playlist not created.")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        db.close()

if __name__ == "__main__":
    test_downloader()
