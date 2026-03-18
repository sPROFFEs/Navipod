import sys
import os
import shutil
from unittest.mock import MagicMock

# 1. MOCK navipod_config BEFORE importing janitor
sys.modules['navipod_config'] = MagicMock()
sys.modules['navipod_config'].settings = MagicMock()
# Mock MUSIC_ROOT to a test folder
TEST_MUSIC_ROOT = os.path.abspath("test_music_root")
sys.modules['navipod_config'].settings.MUSIC_ROOT = TEST_MUSIC_ROOT

# Add parent dir
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, Playlist, Track, PlaylistItem, User, engine, Base
import janitor

def test_janitor():
    print("Setting up Janitor Test Environment...")
    
    # Setup DB
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    # Setup Dirs
    if os.path.exists(TEST_MUSIC_ROOT): shutil.rmtree(TEST_MUSIC_ROOT)
    os.makedirs(TEST_MUSIC_ROOT)
    
    try:
        # Create User
        user = User(username="janitor_tester", hashed_password="pw")
        db.add(user)
        db.commit()
        db.refresh(user)
        
        # Create User Music Dir
        user_music_dir = os.path.join(TEST_MUSIC_ROOT, user.username, "music")
        os.makedirs(user_music_dir)
        
        # Create Track in Pool
        t1 = Track(
            title="Song A", artist="Art A", album="Alb A",
            file_hash="hashA", source_id="srcA",
            filepath="/saas-data/pool/Art A/Alb A/Song A.mp3"
        )
        db.add(t1)
        db.commit()
        
        # Create Playlist
        pl = Playlist(name="My Mix", owner_id=user.id)
        db.add(pl)
        db.commit()
        
        # Link
        item = PlaylistItem(playlist_id=pl.id, track_id=t1.id, position=1)
        db.add(item)
        db.commit()
        
        # RUN JANITOR
        print("Running sync_playlists...")
        janitor.sync_playlists(db)
        
        # VERIFY
        m3u_path = os.path.join(user_music_dir, "My Mix.m3u")
        if os.path.exists(m3u_path):
            print(f"PASS: M3U created at {m3u_path}")
            with open(m3u_path, 'r') as f:
                content = f.read()
                print("--- M3U CONTENT ---")
                print(content)
                print("-------------------")
                
                if "pool/Art A/Alb A/Song A.mp3" in content:
                    print("PASS: Path translation correct (pool/...)")
                else:
                    print("FAIL: Path translation incorrect.")
        else:
            print("FAIL: M3U not found.")
            
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()
        # Cleanup
        # shutil.rmtree(TEST_MUSIC_ROOT) # Leave for inspection if failed
        pass

if __name__ == "__main__":
    test_janitor()
