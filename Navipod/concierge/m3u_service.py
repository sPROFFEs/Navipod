"""
M3U Service - Gestión de playlists M3U para usuarios.

Responsabilidades:
- Crear/eliminar playlists para usuarios
- Añadir/quitar tracks de playlists
- Generar archivos .m3u compatibles con Navidrome
"""

import os
import logging
from pathlib import Path
from typing import List, Optional
from sqlalchemy.orm import Session

import database
from navipod_config import settings

logger = logging.getLogger(__name__)


class M3UService:
    """Servicio para gestionar playlists M3U de usuarios."""
    
    def __init__(self, db: Session, user: database.User):
        self.db = db
        self.user = user
        # Align with music.py and Docker mount: /saas-data/users/{user}/music/playlists
        self.playlists_root = f"{settings.MUSIC_ROOT}/{user.username}/music/playlists"
        os.makedirs(self.playlists_root, exist_ok=True)
    
    # =========================================================================
    # GESTIÓN DE PLAYLISTS
    # =========================================================================
    
    def create_playlist(self, name: str, source_url: str = None) -> database.UserPlaylist:
        """Crea una nueva playlist para el usuario."""
        # Sanitizar nombre
        safe_name = self._sanitize_name(name) or f"Playlist_{self.user.id}"
        m3u_path = str(Path(self.playlists_root) / f"{safe_name}.m3u")
        
        # Verificar si ya existe
        existing = self.db.query(database.UserPlaylist).filter(
            database.UserPlaylist.user_id == self.user.id,
            database.UserPlaylist.name == safe_name
        ).first()
        
        if existing:
            return existing
        
        # Crear playlist
        playlist = database.UserPlaylist(
            user_id=self.user.id,
            name=safe_name,
            m3u_path=m3u_path,
            source_url=source_url
        )
        self.db.add(playlist)
        self.db.commit()
        self.db.refresh(playlist)
        
        # Generar archivo M3U vacío
        self._write_m3u(playlist)
        
        logger.info(f"Playlist creada: {safe_name} para {self.user.username}")
        return playlist
    
    def delete_playlist(self, playlist_id: int) -> bool:
        """Elimina una playlist del usuario."""
        playlist = self.db.query(database.UserPlaylist).filter(
            database.UserPlaylist.id == playlist_id,
            database.UserPlaylist.user_id == self.user.id
        ).first()
        
        if not playlist:
            return False
        
        # Eliminar archivo M3U
        if playlist.m3u_path and os.path.exists(playlist.m3u_path):
            os.remove(playlist.m3u_path)
        
        # Eliminar de DB
        self.db.delete(playlist)
        self.db.commit()
        
        logger.info(f"Playlist eliminada: {playlist.name}")
        return True
    
    def get_user_playlists(self) -> List[database.UserPlaylist]:
        """Obtiene todas las playlists del usuario."""
        return self.db.query(database.UserPlaylist).filter(
            database.UserPlaylist.user_id == self.user.id
        ).order_by(database.UserPlaylist.name).all()
    
    # =========================================================================
    # GESTIÓN DE TRACKS EN PLAYLISTS
    # =========================================================================
    
    def add_track_to_playlist(self, playlist_id: int, track_id: int, position: int = None) -> bool:
        """Añade un track a una playlist."""
        playlist = self._get_user_playlist(playlist_id)
        if not playlist:
            return False
        
        track = self.db.query(database.Track).filter(database.Track.id == track_id).first()
        if not track:
            return False
        
        # Verificar si ya está en la playlist
        existing = self.db.query(database.PlaylistTrackLink).filter(
            database.PlaylistTrackLink.playlist_id == playlist_id,
            database.PlaylistTrackLink.track_id == track_id
        ).first()
        
        if existing:
            logger.info(f"Track {track_id} ya está en playlist {playlist_id}")
            return True
        
        # Calcular posición
        if position is None:
            max_pos = self.db.query(database.PlaylistTrackLink).filter(
                database.PlaylistTrackLink.playlist_id == playlist_id
            ).count()
            position = max_pos
        
        # Crear link
        link = database.PlaylistTrackLink(
            playlist_id=playlist_id,
            track_id=track_id,
            position=position
        )
        self.db.add(link)
        self.db.commit()
        
        # Regenerar M3U
        self._write_m3u(playlist)
        
        logger.info(f"Track {track.title} añadido a {playlist.name}")
        return True
    
    def remove_track_from_playlist(self, playlist_id: int, track_id: int) -> bool:
        """Elimina un track de una playlist."""
        playlist = self._get_user_playlist(playlist_id)
        if not playlist:
            return False
        
        link = self.db.query(database.PlaylistTrackLink).filter(
            database.PlaylistTrackLink.playlist_id == playlist_id,
            database.PlaylistTrackLink.track_id == track_id
        ).first()
        
        if not link:
            return False
        
        self.db.delete(link)
        self.db.commit()
        
        # Regenerar M3U
        self._write_m3u(playlist)
        
        return True
    
    def get_playlist_tracks(self, playlist_id: int) -> List[database.Track]:
        """Obtiene todos los tracks de una playlist ordenados por posición."""
        playlist = self._get_user_playlist(playlist_id)
        if not playlist:
            return []
        
        links = self.db.query(database.PlaylistTrackLink).filter(
            database.PlaylistTrackLink.playlist_id == playlist_id
        ).order_by(database.PlaylistTrackLink.position).all()
        
        return [link.track for link in links if link.track]
    
    # =========================================================================
    # GENERACIÓN DE M3U
    # =========================================================================
    
    def _write_m3u(self, playlist: database.UserPlaylist):
        """
        Genera el archivo M3U con rutas absolutas a la pool.
        Formato compatible con Navidrome.
        """
        if not playlist.m3u_path:
            return
        
        # Asegurar directorio
        os.makedirs(os.path.dirname(playlist.m3u_path), exist_ok=True)
        
        # Obtener tracks ordenados
        links = self.db.query(database.PlaylistTrackLink).filter(
            database.PlaylistTrackLink.playlist_id == playlist.id
        ).order_by(database.PlaylistTrackLink.position).all()
        
        try:
            with open(playlist.m3u_path, 'w', encoding='utf-8') as f:
                f.write("#EXTM3U\n")
                f.write(f"#PLAYLIST:{playlist.name}\n")
                
                for link in links:
                    track = link.track
                    if track and track.file_path and os.path.exists(track.file_path):
                        # Extended M3U con duración
                        duration = track.duration_ms // 1000 if track.duration_ms else -1
                        f.write(f"#EXTINF:{duration},{track.artist} - {track.title}\n")
                        f.write(f"{track.file_path}\n")
            
            logger.info(f"M3U generado: {playlist.m3u_path}")
        except Exception as e:
            logger.error(f"Error escribiendo M3U: {e}")
    
    def regenerate_all_m3u(self):
        """Regenera todos los archivos M3U del usuario."""
        playlists = self.get_user_playlists()
        for playlist in playlists:
            self._write_m3u(playlist)
    
    # =========================================================================
    # UTILIDADES
    # =========================================================================
    
    def _get_user_playlist(self, playlist_id: int) -> Optional[database.UserPlaylist]:
        """Obtiene una playlist verificando que pertenece al usuario."""
        return self.db.query(database.UserPlaylist).filter(
            database.UserPlaylist.id == playlist_id,
            database.UserPlaylist.user_id == self.user.id
        ).first()
    
    def _sanitize_name(self, name: str) -> str:
        """Sanitiza nombre de playlist para sistema de archivos."""
        if not name:
            return ""
        unsafe = '<>:"/\\|?*'
        for char in unsafe:
            name = name.replace(char, '')
        return name.strip()[:80]


# Función de conveniencia
def get_m3u_service(db: Session, user: database.User) -> M3UService:
    return M3UService(db, user)
