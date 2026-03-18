"""
Track Service - Gestión de la pool de música centralizada.

Responsabilidades:
- Búsqueda híbrida (local + remoto)
- Deduplicación por source_id y content_hash
- Gestión de rutas en formato /Artist/Album/Track.ext
"""

import os
import re
import hashlib
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session

import database
from navipod_config import settings

logger = logging.getLogger(__name__)

# Constantes
AUDIO_EXTENSIONS = ('.mp3', '.m4a', '.flac', '.opus', '.ogg', '.wav')


class TrackService:
    """Servicio para gestionar la pool de canciones centralizada."""
    
    def __init__(self, db: Session):
        self.db = db
        self.pool_root = settings.MUSIC_POOL_ROOT
        os.makedirs(self.pool_root, exist_ok=True)
    
    # =========================================================================
    # BÚSQUEDA
    # =========================================================================
    
    def search_local(self, query: str, limit: int = 20) -> List[database.Track]:
        """
        Busca canciones en la pool local.
        Busca por título, artista o álbum.
        """
        query_lower = f"%{query.lower()}%"
        return self.db.query(database.Track).filter(
            (database.Track.title.ilike(query_lower)) |
            (database.Track.artist.ilike(query_lower)) |
            (database.Track.album.ilike(query_lower))
        ).limit(limit).all()
    
    def search_hybrid(self, query: str, spotify_service=None, youtube_service=None, 
                     spotify_creds: Dict = None, limit: int = 20) -> Dict[str, Any]:
        """
        Búsqueda híbrida: primero local, luego remoto.
        Retorna dict con resultados locales y remotos separados.
        """
        result = {
            "local": [],
            "remote_spotify": [],
            "remote_youtube": []
        }
        
        # 1. Búsqueda local
        local_tracks = self.search_local(query, limit)
        result["local"] = [self._track_to_dict(t) for t in local_tracks]
        
        # 2. Búsqueda remota (solo si hay servicios configurados)
        # TODO: Implementar llamadas a spotify_service y youtube_service
        
        return result
    
    # =========================================================================
    # DEDUPLICACIÓN
    # =========================================================================
    
    def find_by_source_id(self, source_id: str) -> Optional[database.Track]:
        """Busca un track por su ID de origen (spotify:xxx o yt:xxx)."""
        return self.db.query(database.Track).filter(
            database.Track.source_id == source_id
        ).first()
    
    def find_by_hash(self, file_path: str) -> Optional[database.Track]:
        """Busca un track por hash de contenido del archivo."""
        content_hash = self._compute_file_hash(file_path)
        if not content_hash:
            return None
        return self.db.query(database.Track).filter(
            database.Track.content_hash == content_hash
        ).first()
    
    def track_exists(self, source_id: str) -> bool:
        """Verifica si un track ya existe en la pool."""
        return self.find_by_source_id(source_id) is not None
    
    def compute_fingerprint(self, artist: str, title: str) -> str:
        """
        Genera un fingerprint semántico normalizado.
        Permite detectar duplicados ignorando diferencias menores.
        
        Formato: {artist_norm}::{title_base}::{version_tag}
        """
        artist_norm = self._normalize_text(artist)
        title_clean, version_tag = self._extract_version_tag(title)
        title_norm = self._normalize_text(title_clean)
        
        return f"{artist_norm}::{title_norm}::{version_tag}"
    
    def _normalize_text(self, text: str) -> str:
        """
        Normaliza texto para comparación:
        - Minúsculas
        - Sin acentos/diacríticos  
        - Sin caracteres especiales
        - Sin espacios extras
        """
        import unicodedata
        if not text:
            return ""
        
        # Minúsculas
        text = text.lower()
        
        # Remover acentos (NFD decomposition + filter)
        nfkd = unicodedata.normalize('NFKD', text)
        text = ''.join(c for c in nfkd if not unicodedata.combining(c))
        
        # Solo alfanuméricos y espacios
        text = re.sub(r'[^a-z0-9\s]', '', text)
        
        # Normalizar espacios
        text = '_'.join(text.split())
        
        return text
    
    def _extract_version_tag(self, title: str) -> tuple:
        """
        Extrae el tag de versión del título.
        Retorna (titulo_limpio, version_tag)
        
        Ejemplos:
        - "Song (Remix)" -> ("Song", "remix")
        - "Song [Live]" -> ("Song", "live")
        - "Song - Remastered" -> ("Song", "remaster")
        - "Song" -> ("Song", "original")
        """
        if not title:
            return ("", "original")
        
        # Patrones de versiones conocidas (orden importa - más específicos primero)
        VERSION_PATTERNS = [
            # Remixes
            (r'[\(\[\-]\s*(.*?remix.*?)[\)\]]?$', 'remix'),
            (r'\s+remix$', 'remix'),
            
            # Live/Acoustic
            (r'[\(\[\-]\s*(live|en vivo|directo).*?[\)\]]?$', 'live'),
            (r'[\(\[\-]\s*(acoustic|acústico).*?[\)\]]?$', 'acoustic'),
            
            # Remaster/Reissue
            (r'[\(\[\-]\s*(remaster|remasterizado|reissue).*?[\)\]]?$', 'remaster'),
            (r'\s*-\s*\d{4}\s*(remaster|remasterizado).*$', 'remaster'),
            
            # Ediciones especiales
            (r'[\(\[\-]\s*(radio edit|single edit|edit)[\)\]]?$', 'edit'),
            (r'[\(\[\-]\s*(extended|extendido).*?[\)\]]?$', 'extended'),
            (r'[\(\[\-]\s*(instrumental)[\)\]]?$', 'instrumental'),
            
            # Covers/Tributos
            (r'[\(\[\-]\s*(cover|tribute|versión de).*?[\)\]]?$', 'cover'),
            
            # Featuring (no es versión pero lo extraemos para limpiar)
            (r'[\(\[]\s*(feat\.?|ft\.?|featuring).*?[\)\]]', 'original'),
            
            # Versión genérica
            (r'[\(\[\-]\s*(version|versión).*?[\)\]]?$', 'version'),
        ]
        
        title_lower = title.lower()
        
        for pattern, tag in VERSION_PATTERNS:
            match = re.search(pattern, title_lower, re.IGNORECASE)
            if match:
                # Limpiar el título removiendo el match
                clean_title = re.sub(pattern, '', title, flags=re.IGNORECASE).strip()
                # Limpiar caracteres residuales
                clean_title = re.sub(r'\s*[-–—]\s*$', '', clean_title).strip()
                return (clean_title, tag)
        
        return (title, "original")
    
    def find_similar(self, artist: str, title: str) -> List[database.Track]:
        """
        Busca tracks similares basándose en fingerprint.
        Útil para mostrar posibles duplicados al usuario.
        """
        fingerprint = self.compute_fingerprint(artist, title)
        base_fingerprint = fingerprint.rsplit('::', 1)[0]  # Sin version tag
        
        # Buscar tracks con mismo artista+título base (cualquier versión)
        similar = self.db.query(database.Track).filter(
            database.Track.fingerprint.like(f"{base_fingerprint}::%")
        ).all()
        
        return similar
    
    def is_exact_duplicate(self, artist: str, title: str) -> Optional[database.Track]:
        """
        Verifica si existe un track con el MISMO fingerprint exacto.
        Mismo artista + mismo título + misma versión = duplicado.
        """
        fingerprint = self.compute_fingerprint(artist, title)
        return self.db.query(database.Track).filter(
            database.Track.fingerprint == fingerprint
        ).first()
    
    # =========================================================================
    # GESTIÓN DE POOL
    # =========================================================================
    
    def get_pool_path(self, artist: str, album: str, filename: str) -> str:
        """
        Genera la ruta en la pool para una canción.
        Formato: /saas-data/music/Artist/Album/filename.ext
        """
        # Sanitizar nombres para sistema de archivos
        safe_artist = self._sanitize_path(artist) or "Unknown Artist"
        safe_album = self._sanitize_path(album) or "Unknown Album"
        safe_filename = self._sanitize_path(filename) or "track.mp3"
        
        path = Path(self.pool_root) / safe_artist / safe_album / safe_filename
        return str(path)
    
    def register_track(self, source_id: str, file_path: str, 
                       title: str, artist: str, album: str = None,
                       duration_ms: int = None, cover_path: str = None) -> database.Track:
        """
        Registra un track en la base de datos.
        El archivo ya debe existir en file_path.
        
        Usa fingerprint semántico para detectar duplicados inteligentemente.
        """
        # 1. Verificar duplicado por source_id
        existing = self.find_by_source_id(source_id)
        if existing:
            logger.info(f"Track ya existe por source_id: {source_id}")
            return existing
        
        # 2. Calcular fingerprint semántico
        fingerprint = self.compute_fingerprint(artist, title)
        
        # 3. Verificar duplicado exacto por fingerprint
        # Mismo artista + mismo título + misma versión = duplicado real
        fingerprint_match = self.db.query(database.Track).filter(
            database.Track.fingerprint == fingerprint
        ).first()
        if fingerprint_match:
            logger.info(f"Track encontrado por fingerprint: {fingerprint} -> {fingerprint_match.source_id}")
            return fingerprint_match
        
        # 4. Calcular hash del archivo (backup)
        content_hash = self._compute_file_hash(file_path) if os.path.exists(file_path) else None
        
        # 5. Verificar por hash también (archivos idénticos)
        if content_hash:
            hash_match = self.db.query(database.Track).filter(
                database.Track.content_hash == content_hash
            ).first()
            if hash_match:
                logger.info(f"Track encontrado por hash: {hash_match.source_id}")
                return hash_match
        
        # 6. Crear nuevo track
        track = database.Track(
            source_id=source_id,
            content_hash=content_hash,
            fingerprint=fingerprint,
            title=title,
            artist=artist,
            album=album,
            duration_ms=duration_ms,
            file_path=file_path,
            cover_path=cover_path
        )
        self.db.add(track)
        self.db.commit()
        self.db.refresh(track)
        
        logger.info(f"Track registrado: {artist} - {title} [{fingerprint}]")
        return track
    
    # =========================================================================
    # UTILIDADES
    # =========================================================================
    
    def _track_to_dict(self, track: database.Track) -> Dict[str, Any]:
        """Convierte un Track a diccionario para API."""
        return {
            "id": track.id,
            "source_id": track.source_id,
            "title": track.title,
            "artist": track.artist,
            "album": track.album,
            "duration_ms": track.duration_ms,
            "file_path": track.file_path,
            "cover_path": track.cover_path,
            "in_pool": True  # Siempre True si viene de búsqueda local
        }
    
    def _sanitize_path(self, name: str) -> str:
        """Sanitiza un nombre para uso en sistema de archivos."""
        if not name:
            return ""
        # Eliminar caracteres peligrosos
        unsafe = '<>:"/\\|?*'
        for char in unsafe:
            name = name.replace(char, '')
        # Limitar longitud
        return name.strip()[:100]
    
    def _compute_file_hash(self, file_path: str, chunk_size: int = 8192) -> Optional[str]:
        """Calcula SHA256 de un archivo."""
        if not os.path.exists(file_path):
            return None
        try:
            sha256 = hashlib.sha256()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(chunk_size), b''):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception as e:
            logger.error(f"Error calculando hash: {e}")
            return None


# Función de conveniencia para crear instancia
def get_track_service(db: Session) -> TrackService:
    return TrackService(db)
