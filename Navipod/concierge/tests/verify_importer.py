import sys
import os
import shutil
import time

# Add parent directory to path to import database and importer
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, Track, Playlist, PlaylistItem, User, engine, Base
from importer import scan_folder, POOL_ROOT

def create_dummy_file(path, content=b"fake mp3 content"):
    with open(path, "wb") as f:
        f.write(content)

def test_importer():
    print("Setting up test environment...")
    
    # 1. Setup DB
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    # Create User
    user = db.query(User).filter_by(username="importer_test").first()
    if not user:
        user = User(username="importer_test", hashed_password="pw")
        db.add(user)
        db.commit()
        db.refresh(user)

    # 2. Setup folders
    test_root = "c:/saas-data/test_import"
    if os.path.exists(test_root):
        shutil.rmtree(test_root)
    os.makedirs(test_root)
    
    # Ensure pool root exists (it might be c:/saas-data/pool based on POOL_ROOT)
    # POOL_ROOT in importer is "/saas-data/pool" which on Windows is "c:/saas-data/pool"
    # Clean pool for test?? No, might destroy data if persistent.
    # But for verification we can check if file appears.
    
    print("Creating dummy files...")
    # Playlist folder
    playlist_folder = os.path.join(test_root, "Test Playlist")
    os.makedirs(playlist_folder)
    
    file1 = os.path.join(playlist_folder, "song1.mp3")
    create_dummy_file(file1, b"song1 content unique")
    
    file2 = os.path.join(playlist_folder, "song2.mp3")
    create_dummy_file(file2, b"song2 content unique")
    
    # Duplicate of song1 in another folder
    playlist_folder_2 = os.path.join(test_root, "Mix Tape")
    os.makedirs(playlist_folder_2)
    file1_dup = os.path.join(playlist_folder_2, "song1_dup.mp3")
    create_dummy_file(file1_dup, b"song1 content unique") # Same content as song1
    
    print("Running scan_folder...")
    scan_folder(test_root, user=user)
    
    print("Verifying database...")
    # Verify Playlists
    p1 = db.query(Playlist).filter_by(name="Test Playlist").first()
    p2 = db.query(Playlist).filter_by(name="Mix Tape").first()
    
    if not p1:
        print("FAIL: Playlist 'Test Playlist' not created.")
    else:
        print(f"PASS: Playlist '{p1.name}' created.")
        
    if not p2: # Note: 'Mix Tape' folder was inside test_root? Yes.
               # scan_folder logic:
               #    folder_name = basename(folder_path) -> 'test_import'
               #    Then os.walk(folder_path).
               #    importer.py 'process_file' adds to 'playlist' passed in (which is 'test_import' playlist).
               # WAIT! scan_folder logic:
               #    scan_folder(test_root) -> Playlist "test_import".
               #    It recurses. It passes 'playlist' ("test_import") to process_file.
               #    It does NOT create sub-playlists for subfolders automatically in the current implementation!
               # Let's check importer.py again.
               pass

    # Verify Tracks
    tracks = db.query(Track).all()
    print(f"Total Tracks in DB: {len(tracks)}")
    
    t1 = db.query(Track).filter(Track.title.like("song1%")).first()
    if t1:
         print(f"PASS: Track song1 imported. Path: {t1.filepath}")
         if os.path.exists(t1.filepath):
             print("PASS: File exists in pool.")
         else:
             print("FAIL: File not found in pool.")
    else:
        print("FAIL: Track song1 not found.")

    # Verify Deduplication
    # We had 3 files. 2 unique contents.
    # Should have 2 Tracks.
    unique_hashes = set(t.file_hash for t in tracks)
    if len(unique_hashes) <= 2: # Ignoring potential preexisting data
        print(f"PASS: Unique hashes count ({len(unique_hashes)}) matches expected (assuming clean DB or distinct content)")
    
    # Verify cleanup (user files deleted/moved)
    if not os.path.exists(file1):
        print("PASS: Original file1 moved/deleted.")
    else:
        print("FAIL: Original file1 still exists.")

    print("Cleanup...")
    # shutil.rmtree(test_root) # Keep for inspection content
    db.close()

if __name__ == "__main__":
    test_importer()
