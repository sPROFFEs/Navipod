from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, Boolean, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.sql import func
from secrets_store import encrypt_secret, decrypt_secret

# Ruta persistente (No tocar)
SQLALCHEMY_DATABASE_URL = "sqlite:////saas-data/concierge.db"

# --- MOTOR OPTIMIZADO PARA ALTA CONCURRENCIA ---
# Esto arregla el error "QueuePool limit of size 5 overflow 10 reached"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False},
    pool_size=30,       # Mantener 30 conexiones abiertas permanentemente
    max_overflow=50,    # Permitir picos de hasta 80 conexiones totales
    pool_timeout=30,    # Esperar 30s antes de dar error si todo está ocupado
    pool_recycle=1800   # Reciclar conexiones cada 30 min para evitar stale connections
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- MODELOS DE USUARIO ---

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    avatar_path = Column(String, nullable=True)  # Profile picture path
    last_access = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    download_settings = relationship("DownloadSettings", uselist=False, back_populates="owner", cascade="all, delete-orphan")
    downloads = relationship("DownloadJob", back_populates="owner", cascade="all, delete-orphan")
    playlists = relationship("UserPlaylist", back_populates="owner", cascade="all, delete-orphan")
    new_playlists = relationship("Playlist", back_populates="owner", cascade="all, delete-orphan")
    favorites = relationship("UserFavorite", back_populates="user", cascade="all, delete-orphan")

class DownloadSettings(Base):
    __tablename__ = "download_settings"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    # storage_limit_gb removed - Global Pool is now used
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
    """Tokens JWT revocados (Logout)"""
    __tablename__ = "token_blacklist"
    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True)
    blacklisted_at = Column(DateTime, default=func.now())

# --- NUEVO MODELO DE BIBLIOTECA (SPOTIFY-CLONE) ---

class Track(Base):
    """El inventario físico real en el disco (La Pool) /saas-data/pool/..."""
    __tablename__ = "tracks"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    artist = Column(String)
    album = Column(String)
    duration = Column(Integer)  # En segundos
    filepath = Column(String, unique=True, index=True)  # Ruta física absoluta
    
    # Identificadores para deduplicación
    source_id = Column(String, unique=True, index=True) # ID de Youtube/Spotify (ej: "yt:dQw4w9WgXcQ")
    file_hash = Column(String, unique=True, index=True) # SHA256 del archivo físico (Critical for Phase 1.5)
    source_provider = Column(String) # 'youtube', 'spotify', 'local'
    
    created_at = Column(DateTime, default=func.now())
    
    # Relaciones
    playlist_items = relationship("PlaylistItem", back_populates="track", cascade="all, delete-orphan")

class Playlist(Base):
    """Listas de reproducción virtuales (M3U)"""
    __tablename__ = "playlists"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    owner_id = Column(Integer, ForeignKey("users.id"))
    is_public = Column(Boolean, default=False, nullable=False)
    source_playlist_id = Column(Integer, nullable=True, index=True)
    # Ruta física donde guardaremos el .m3u para que Navidrome lo lea (calculado dinámicamente o persistido)
    m3u_path = Column(String, nullable=True) 
    
    owner = relationship("User", back_populates="new_playlists")
    items = relationship("PlaylistItem", back_populates="playlist", cascade="all, delete-orphan")

class PlaylistItem(Base):
    """Relación N:M entre Playlist y Track (Lista Ordenada)"""
    __tablename__ = "playlist_items"
    
    id = Column(Integer, primary_key=True, index=True)
    playlist_id = Column(Integer, ForeignKey("playlists.id"))
    track_id = Column(Integer, ForeignKey("tracks.id"))
    position = Column(Integer, default=0) # Para ordenar la lista (CRITICAL for experience)
    added_at = Column(DateTime, default=func.now())
    
    playlist = relationship("Playlist", back_populates="items")
    track = relationship("Track", back_populates="playlist_items")

# --- GESTIÓN DE BIBLIOTECA (LEGACY - A MIGRAR) ---

class UserPlaylist(Base):
    __tablename__ = "user_playlists"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    
    name = Column(String)              # Nombre de la carpeta/playlist (ej: "Rock 90s")
    source_url = Column(String)        # URL original (Spotify/YT) para sincronizar
    folder_path = Column(String)       # Ruta física en disco: /music/Rock 90s
    auto_sync = Column(Boolean, default=False) # Para futuros cronjobs
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    
    owner = relationship("User", back_populates="playlists")
    tracks = relationship("PlaylistTrack", back_populates="playlist", cascade="all, delete-orphan")

class PlaylistTrack(Base):
    __tablename__ = "playlist_tracks"
    id = Column(Integer, primary_key=True, index=True)
    playlist_id = Column(Integer, ForeignKey("user_playlists.id"))
    
    title = Column(String)
    file_path = Column(String)         # Ruta relativa del archivo
    source_id = Column(String)         # ID único de Spotify/YT (para detectar duplicados)
    
    playlist = relationship("UserPlaylist", back_populates="tracks")

class DownloadJob(Base):
    __tablename__ = "download_jobs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    
    input_url = Column(String)
    # Lógica de destino:
    target_playlist_id = Column(Integer, ForeignKey("user_playlists.id"), nullable=True) # Si va a una existente
    new_playlist_name = Column(String, nullable=True) # Si crea una nueva
    
    status = Column(String, default="pending")
    progress_percent = Column(Integer, default=0)
    current_file = Column(String, nullable=True)
    error_log = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    owner = relationship("User", back_populates="downloads")

# --- FAVORITOS ---

class UserFavorite(Base):
    """Canciones favoritas del usuario (Liked Songs)"""
    __tablename__ = "user_favorites"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    track_id = Column(Integer, ForeignKey("tracks.id"))
    added_at = Column(DateTime(timezone=True), server_default=func.now())
    
    user = relationship("User", back_populates="favorites")
    track = relationship("Track")

# Crea las tablas si no existen
Base.metadata.create_all(bind=engine)
