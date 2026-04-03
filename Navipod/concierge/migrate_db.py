import sqlite3
import os

DB_PATH = "/saas-data/concierge.db"

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}. Nothing to migrate.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print(f"Checking for migrations in {DB_PATH}...")

    # 1. Check for columns in 'tracks'
    try:
        cursor.execute("PRAGMA table_info(tracks)")
        columns = [column[1] for column in cursor.fetchall()]
        
        # Columns to check and add
        required_columns = {
            "duration": "INTEGER",
            "filepath": "TEXT",
            "source_id": "TEXT",
            "file_hash": "TEXT",
            "source_provider": "TEXT"
        }
        
        for col_name, col_type in required_columns.items():
            if col_name not in columns:
                print(f"Adding '{col_name}' column to 'tracks' table...")
                cursor.execute(f"ALTER TABLE tracks ADD COLUMN {col_name} {col_type}")
                print(f"Successfully added '{col_name}'.")

    except sqlite3.OperationalError as e:
        print(f"Error checking tracks table: {e}")

    # 2. Create user_favorites table if it doesn't exist (Phase 6: Liked Songs)
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_favorites (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                track_id INTEGER NOT NULL,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (track_id) REFERENCES tracks(id),
                UNIQUE(user_id, track_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_user_favorites_user_id ON user_favorites(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_user_favorites_track_id ON user_favorites(track_id)")
        print("user_favorites table ready.")
    except sqlite3.OperationalError as e:
        print(f"Error creating user_favorites table: {e}")

    # 3. Download settings new BYOK columns
    try:
        cursor.execute("PRAGMA table_info(download_settings)")
        dl_columns = [column[1] for column in cursor.fetchall()]

        required_dl_columns = {
            "lastfm_api_key": "TEXT",
            "lastfm_shared_secret": "TEXT",
            "youtube_cookies": "TEXT",
            "metadata_preferences": "TEXT DEFAULT '[\"spotify\", \"lastfm\", \"musicbrainz\"]'"
        }

        for col_name, col_type in required_dl_columns.items():
            if col_name not in dl_columns:
                print(f"Adding '{col_name}' column to 'download_settings' table...")
                cursor.execute(f"ALTER TABLE download_settings ADD COLUMN {col_name} {col_type}")
                print(f"Successfully added '{col_name}'.")
    except sqlite3.OperationalError as e:
        print(f"Error updating download_settings table: {e}")

    # 4. Create playlists table if it doesn't exist (should already exist but let's be safe)
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                owner_id INTEGER NOT NULL,
                is_public INTEGER NOT NULL DEFAULT 0,
                source_playlist_id INTEGER,
                m3u_path TEXT,
                FOREIGN KEY (owner_id) REFERENCES users(id)
            )
        """)

        cursor.execute("PRAGMA table_info(playlists)")
        playlist_columns = [column[1] for column in cursor.fetchall()]
        if "is_public" not in playlist_columns:
            cursor.execute("ALTER TABLE playlists ADD COLUMN is_public INTEGER NOT NULL DEFAULT 0")
            print("Successfully added 'is_public' to playlists.")
        if "source_playlist_id" not in playlist_columns:
            cursor.execute("ALTER TABLE playlists ADD COLUMN source_playlist_id INTEGER")
            print("Successfully added 'source_playlist_id' to playlists.")

        print("playlists table ready.")
    except sqlite3.OperationalError as e:
        print(f"Error creating playlists table: {e}")

    # 5. Create playlist_items table if it doesn't exist
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS playlist_items (
                id INTEGER PRIMARY KEY,
                playlist_id INTEGER NOT NULL,
                track_id INTEGER NOT NULL,
                position INTEGER DEFAULT 0,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (playlist_id) REFERENCES playlists(id),
                FOREIGN KEY (track_id) REFERENCES tracks(id)
            )
        """)
        print("playlist_items table ready.")
    except sqlite3.OperationalError as e:
        print(f"Error creating playlist_items table: {e}")

    conn.commit()
    conn.close()
    print("Migration complete!")

if __name__ == "__main__":
    migrate()

