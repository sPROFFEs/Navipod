import sqlite3
import sys
import tempfile
from pathlib import Path

from navipod_config import settings
from secrets_store import decrypt_secret, encrypt_secret
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.sql import func


def _resolve_database_url() -> str:
    configured = (getattr(settings, "DATABASE_URL", None) or "").strip()
    if configured:
        return configured

    db_path = Path(settings.HOST_DATA_ROOT).resolve() / "concierge.db"
    return f"sqlite:///{db_path.as_posix()}"


SQLALCHEMY_DATABASE_URL = _resolve_database_url()


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    if not database_url.startswith("sqlite:///") or database_url in {"sqlite://", "sqlite:///:memory:"}:
        return

    db_path_str = database_url[len("sqlite:///") :]
    if not db_path_str or db_path_str == ":memory:":
        return
    parent = Path(db_path_str).expanduser().resolve().parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Local tests often run outside container mounts (/saas-data, /opt/saas-data).
        if "pytest" in sys.modules:
            fallback = Path(tempfile.gettempdir()) / "navipod-test-db"
            fallback.mkdir(parents=True, exist_ok=True)
            globals()["SQLALCHEMY_DATABASE_URL"] = f"sqlite:///{(fallback / 'concierge.db').as_posix()}"
            return
        raise


_ensure_sqlite_parent_dir(SQLALCHEMY_DATABASE_URL)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    poolclass=NullPool,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, connection_record):
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return

    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.close()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    avatar_path = Column(String, nullable=True)
    last_access = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    download_settings = relationship(
        "DownloadSettings", uselist=False, back_populates="owner", cascade="all, delete-orphan"
    )
    downloads = relationship("DownloadJob", back_populates="owner", cascade="all, delete-orphan")
    playlists = relationship("UserPlaylist", back_populates="owner", cascade="all, delete-orphan")
    new_playlists = relationship("Playlist", back_populates="owner", cascade="all, delete-orphan")
    favorites = relationship("UserFavorite", back_populates="user", cascade="all, delete-orphan")
    playback_queue_state = relationship(
        "PlaybackQueueState", uselist=False, back_populates="user", cascade="all, delete-orphan"
    )


class DownloadSettings(Base):
    __tablename__ = "download_settings"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    _spotify_client_id = Column("spotify_client_id", String, nullable=True)
    _spotify_client_secret = Column("spotify_client_secret", String, nullable=True)
    _lastfm_api_key = Column("lastfm_api_key", String, nullable=True)
    _lastfm_shared_secret = Column("lastfm_shared_secret", String, nullable=True)
    youtube_cookies_path = Column(String, nullable=True)
    _youtube_cookies = Column("youtube_cookies", Text, nullable=True)
    metadata_preferences = Column(Text, default='["spotify", "lastfm", "musicbrainz"]')
    audio_quality = Column(String, default="320")
    owner = relationship("User", back_populates="download_settings")

    @property
    def spotify_client_id(self):
        return decrypt_secret(self._spotify_client_id)

    @spotify_client_id.setter
    def spotify_client_id(self, value):
        self._spotify_client_id = encrypt_secret(value)

    @property
    def spotify_client_secret(self):
        return decrypt_secret(self._spotify_client_secret)

    @spotify_client_secret.setter
    def spotify_client_secret(self, value):
        self._spotify_client_secret = encrypt_secret(value)

    @property
    def lastfm_api_key(self):
        return decrypt_secret(self._lastfm_api_key)

    @lastfm_api_key.setter
    def lastfm_api_key(self, value):
        self._lastfm_api_key = encrypt_secret(value)

    @property
    def lastfm_shared_secret(self):
        return decrypt_secret(self._lastfm_shared_secret)

    @lastfm_shared_secret.setter
    def lastfm_shared_secret(self, value):
        self._lastfm_shared_secret = encrypt_secret(value)

    @property
    def youtube_cookies(self):
        return decrypt_secret(self._youtube_cookies)

    @youtube_cookies.setter
    def youtube_cookies(self, value):
        self._youtube_cookies = encrypt_secret(value)


class SystemSettings(Base):
    __tablename__ = "system_settings"
    id = Column(Integer, primary_key=True, index=True)
    pool_limit_gb = Column(Integer, default=100)
    autobackup_enabled = Column(Boolean, default=True)
    autobackup_hour = Column(Integer, default=0)
    autobackup_minute = Column(Integer, default=0)
    autobackup_timezone = Column(String, default="UTC")
    update_state_json = Column(Text, nullable=True)
    wrapped_enabled = Column(Boolean, default=False)
    wrapped_visible_from = Column(String, nullable=True)
    wrapped_visible_until = Column(String, nullable=True)
    wrapped_artist_clip_message = Column(Text, nullable=True)


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    applied_at = Column(DateTime(timezone=True), server_default=func.now())


class AdminJob(Base):
    __tablename__ = "admin_jobs"
    id = Column(Integer, primary_key=True, index=True)
    job_type = Column(String, index=True)
    status = Column(String, default="pending", index=True)
    triggered_by = Column(String, nullable=True)
    message = Column(String, nullable=True)
    details_json = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)


class AdminOperationLock(Base):
    __tablename__ = "admin_operation_locks"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    job_id = Column(Integer, ForeignKey("admin_jobs.id"), nullable=True)
    acquired_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)


class BackupArtifact(Base):
    __tablename__ = "backup_artifacts"
    id = Column(Integer, primary_key=True, index=True)
    slot = Column(String, unique=True, index=True)
    filename = Column(String, nullable=True)
    file_path = Column(String, nullable=True)
    size_bytes = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), nullable=True)
    source_commit = Column(String, nullable=True)
    source_branch = Column(String, nullable=True)
    manifest_json = Column(Text, nullable=True)


class TokenBlacklist(Base):
    __tablename__ = "token_blacklist"
    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True)
    blacklisted_at = Column(DateTime, default=func.now())


class Track(Base):
    __tablename__ = "tracks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    artist = Column(String)
    album = Column(String)
    duration = Column(Integer)
    filepath = Column(String, unique=True, index=True)
    source_id = Column(String, unique=True, index=True)
    file_hash = Column(String, unique=True, index=True)
    artist_norm = Column(String, index=True)
    title_norm = Column(String, index=True)
    version_tag = Column(String, index=True)
    fingerprint = Column(String, index=True)
    source_provider = Column(String)
    created_at = Column(DateTime, default=func.now())

    playlist_items = relationship("PlaylistItem", back_populates="track", cascade="all, delete-orphan")


class Playlist(Base):
    __tablename__ = "playlists"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    owner_id = Column(Integer, ForeignKey("users.id"))
    is_public = Column(Boolean, default=False, nullable=False)
    source_playlist_id = Column(Integer, nullable=True, index=True)
    m3u_path = Column(String, nullable=True)
    cover_path = Column(String, nullable=True)
    cover_track_id = Column(Integer, ForeignKey("tracks.id"), nullable=True)

    owner = relationship("User", back_populates="new_playlists")
    items = relationship("PlaylistItem", back_populates="playlist", cascade="all, delete-orphan")


class PlaylistItem(Base):
    __tablename__ = "playlist_items"

    id = Column(Integer, primary_key=True, index=True)
    playlist_id = Column(Integer, ForeignKey("playlists.id"))
    track_id = Column(Integer, ForeignKey("tracks.id"))
    position = Column(Integer, default=0)
    added_at = Column(DateTime, default=func.now())

    playlist = relationship("Playlist", back_populates="items")
    track = relationship("Track", back_populates="playlist_items")


class UserPlaylist(Base):
    __tablename__ = "user_playlists"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String)
    source_url = Column(String)
    folder_path = Column(String)
    auto_sync = Column(Boolean, default=False)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)

    owner = relationship("User", back_populates="playlists")
    tracks = relationship("PlaylistTrack", back_populates="playlist", cascade="all, delete-orphan")


class PlaylistTrack(Base):
    __tablename__ = "playlist_tracks"
    id = Column(Integer, primary_key=True, index=True)
    playlist_id = Column(Integer, ForeignKey("user_playlists.id"))
    title = Column(String)
    file_path = Column(String)
    source_id = Column(String)

    playlist = relationship("UserPlaylist", back_populates="tracks")


class DownloadJob(Base):
    __tablename__ = "download_jobs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    input_url = Column(String)
    original_input_url = Column(String, nullable=True)
    requested_title = Column(String, nullable=True)
    requested_artist = Column(String, nullable=True)
    requested_album = Column(String, nullable=True)
    requested_source = Column(String, nullable=True)
    resolution_mode = Column(String, nullable=True)
    resolved_title = Column(String, nullable=True)
    resolved_artist = Column(String, nullable=True)
    resolved_album = Column(String, nullable=True)
    resolved_track_id = Column(Integer, ForeignKey("tracks.id"), nullable=True)
    resolved_track_count = Column(Integer, default=0)
    engine_used = Column(String, nullable=True)
    fallback_reason = Column(Text, nullable=True)
    error_type = Column(String, nullable=True)
    target_playlist_id = Column(Integer, ForeignKey("user_playlists.id"), nullable=True)
    target_modern_playlist_id = Column(Integer, ForeignKey("playlists.id"), nullable=True)
    new_playlist_name = Column(String, nullable=True)
    status = Column(String, default="pending")
    progress_percent = Column(Integer, default=0)
    current_file = Column(String, nullable=True)
    error_log = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    owner = relationship("User", back_populates="downloads")
    target_modern_playlist = relationship("Playlist")
    resolved_track = relationship("Track")


class TrackDeleteRequest(Base):
    __tablename__ = "track_delete_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    track_id = Column(Integer, ForeignKey("tracks.id"), nullable=True, index=True)
    track_title = Column(String, nullable=True)
    track_artist = Column(String, nullable=True)
    reason = Column(Text, nullable=False)
    status = Column(String, default="pending", index=True)
    review_note = Column(Text, nullable=True)
    reviewed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    requested_at = Column(DateTime(timezone=True), server_default=func.now())
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    user_seen_at = Column(DateTime(timezone=True), nullable=True)


class PlaybackQueueState(Base):
    __tablename__ = "playback_queue_states"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, index=True)
    manual_queue_json = Column(Text, nullable=True)
    context_queue_json = Column(Text, nullable=True)
    original_context_queue_json = Column(Text, nullable=True)
    current_track_json = Column(Text, nullable=True)
    current_view_name = Column(String, nullable=True)
    current_view_param_json = Column(Text, nullable=True)
    context_index = Column(Integer, default=-1)
    shuffle_mode = Column(Boolean, default=False)
    repeat_mode = Column(String, default="off")
    current_time = Column(Integer, default=0)
    duration = Column(Integer, default=0)
    was_playing = Column(Boolean, default=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="playback_queue_state")


class UserFavorite(Base):
    __tablename__ = "user_favorites"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    track_id = Column(Integer, ForeignKey("tracks.id"))
    added_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="favorites")
    track = relationship("Track")
