import sys
import os

# Add parent directory to path to import database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import engine, SessionLocal, Base, Track, Playlist, PlaylistItem, User, UserFavorite
from sqlalchemy.orm import Session

def test_models():
    print("Creating tables...")
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    try:
        # 1. Create a dummy Track
        print("Creating dummy Track...")
        track = Track(
            title="Test Song",
            artist="Test Artist",
            album="Test Album",
            source_id="spotify:track:123456",
            file_hash="dummyhash123",
            filepath="/saas-data/pool/Test Artist/Test Album/Test Song.mp3", # Field is filepath not file_path
            duration=300
        )
        db.add(track)
        db.commit()
        db.refresh(track)
        print(f"Track created: ID={track.id} Title={track.title}")
        
        # 2. Create a dummy Playlist
        print("Creating dummy Playlist...")
        
        # Create a dummy user first just in case
        user = db.query(User).filter_by(username="tester").first()
        if not user:
            user = User(username="tester", hashed_password="pw")
            db.add(user)
            db.commit()
            db.refresh(user)
            
        playlist = Playlist(
            name="My Favorites",
            owner_id=user.id # Field is owner_id not user_id
        )
        
        db.add(playlist)
        db.commit()
        db.refresh(playlist)
        print(f"Playlist created: ID={playlist.id} Name={playlist.name}")
        
        # 3. Add Track to Playlist
        print("Adding Track to Playlist...")
        item = PlaylistItem(
            playlist_id=playlist.id,
            track_id=track.id,
            position=1
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        print(f"PlaylistItem created: ID={item.id} Position={item.position}")
        
        # 4. test UserFavorite
        print("Testing UserFavorite...")
        fav = UserFavorite(user_id=user.id, track_id=track.id)
        db.add(fav)
        db.commit()
        db.refresh(fav)
        print(f"UserFavorite created: ID={fav.id}")

        # 5. Verify Relationships
        print("Verifying relationships...")
        retrieved_playlist = db.query(Playlist).filter_by(id=playlist.id).first()
        assert len(retrieved_playlist.items) == 1
        assert retrieved_playlist.items[0].track.title == "Test Song"
        
        retrieved_user = db.query(User).filter_by(id=user.id).first()
        assert len(retrieved_user.favorites) == 1
        print("Relationships verified!")

        # 6. Clean up
        print("Cleaning up...")
        db.delete(fav)
        db.delete(item)
        db.delete(playlist)
        db.delete(track)
        # db.delete(user) # Keep user maybe
        db.commit()
        
    except Exception as e:
        print(f"ERROR: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    test_models()
