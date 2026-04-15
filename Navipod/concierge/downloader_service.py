import os
import base64
import subprocess
import logging
import asyncio
import tempfile
import shutil
from datetime import datetime
from sqlalchemy.orm import Session
import database
import yt_dlp
from pathlib import Path
import time
import unicodedata
import re
import manager
import manager
import utils
import httpx
import mutagen
from mutagen.easyid3 import EasyID3
import track_identity
try:
    from mutagen.id3 import ID3, APIC
except ImportError:
    pass

from database import Track, Playlist, PlaylistItem, UserPlaylist

from navipod_config import settings

# Límite global de descargas simultáneas (ej. 3)
download_semaphore = asyncio.Semaphore(settings.CONCURRENT_DOWNLOADS)

# Configuración de Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DownloadManager:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id
        self.user = db.query(database.User).filter(database.User.id == user_id).first()
        
        if not self.user:
            raise ValueError(f"User {user_id} not found")

        self.music_root = f"/saas-data/users/{self.user.username}/music"
        self.settings = self.user.download_settings
        self._runtime_cookie_path = None
        self._last_download_reason = ""
        
        # --- VALIDACIÓN DE MOTOR ---
        if not shutil.which("ffmpeg"):
            logger.error("CRÍTICO: FFmpeg no está instalado en el servidor. Reconstruye el Docker.")
        # ---------------------------
        
        if not self.settings:
            self.settings = database.DownloadSettings(user_id=user_id)
            self.db.add(self.settings)
            self.db.commit()

    def _log(self, job_id, msg, progress=None):
        try:
            # Re-fetch the job to ensure we're using the latest state in this session
            job = self.db.query(database.DownloadJob).filter(database.DownloadJob.id == job_id).first()
            if job:
                if progress is not None:
                    job.progress_percent = int(float(progress))
                if msg:
                    job.current_file = msg[:200]
                self.db.commit()
                # logger.info(f"Job {job_id} Update: {progress}% - {msg}")
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to log progress: {e}")

    def _set_last_reason(self, reason: str):
        self._last_download_reason = (reason or "").strip()

    def _spotdl_should_bypass_retries(self) -> bool:
        reason = (self._last_download_reason or "").lower()
        fatal_markers = [
            "keyerror: 'genres'",
            'keyerror: "genres"',
            "song.from_url",
            "spotdl exception",
            "missing 'genres'",
            'missing "genres"',
            "metadata bug",
        ]
        return any(marker in reason for marker in fatal_markers)

    def _has_downloaded_audio(self, folder: str) -> bool:
        return any(True for _ in self._iter_downloaded_audio_files(folder))

    def _iter_downloaded_audio_files(self, folder: str):
        audio_exts = ('.mp3', '.m4a', '.flac', '.opus', '.ogg', '.wav')
        try:
            for root, _, files in os.walk(folder):
                for name in files:
                    if name.lower().endswith(audio_exts):
                        yield os.path.join(root, name)
        except Exception:
            return

    def _is_youtube_age_gate_error(self, message: str) -> bool:
        reason = (message or "").lower()
        markers = [
            "sign in to confirm your age",
            "this video may be inappropriate for some users",
            "age-restricted",
            "confirm your age",
        ]
        return any(marker in reason for marker in markers)

    def _is_youtube_bot_challenge_error(self, message: str) -> bool:
        reason = (message or "").lower()
        markers = [
            "sign in to confirm you’re not a bot",
            "sign in to confirm you're not a bot",
            "confirm you're not a bot",
            "confirm you’re not a bot",
            "not a bot",
        ]
        return any(marker in reason for marker in markers)

    def _normalize_filename(self, filename: str) -> str:
        """
        Elimina el ruido: diacríticos, emojis y caracteres no-ASCII.
        Asegura compatibilidad absoluta con el escáner de Navidrome.
        """
        import unicodedata
        import re

        # Separamos la extensión para no normalizar el punto del archivo
        name, ext = os.path.splitext(filename)
        
        # Normalización NFKD y eliminación de no-ASCII
        nfkd_form = unicodedata.normalize('NFKD', name)
        only_ascii = nfkd_form.encode('ASCII', 'ignore').decode('ASCII')
        
        # Filtro de seguridad: solo alfanuméricos, guiones y espacios
        clean_name = re.sub(r'[^a-zA-Z0-9\-\s]', '', only_ascii)
        clean_name = " ".join(clean_name.split()).strip()
        
        if not clean_name:
            clean_name = f"track_{int(time.time())}"
            
        return f"{clean_name}{ext.lower()}"

    def _extract_source_id(self, url: str) -> str:
        """
        Extracts a normalized source_id from a URL for deduplication.
        Returns the same format stored in Track.source_id.
        """
        sid = track_identity.extract_source_id_from_url(url)
        if sid:
            logger.info(f"Extracted source ID: {sid} from {url}")
            return sid

        logger.warning(f"Could not extract source_id from URL: {url}")
        return None

    def _resolve_cookie_file(self, temp_dir: str) -> str | None:
        """
        Resolve cookies source for yt-dlp/spotdl.
        Priority:
        1) Inline cookies from DB (BYOK textarea)
        2) Legacy file path stored in DB
        """
        inline_cookies = getattr(self.settings, "youtube_cookies", None)
        if inline_cookies:
            cookie_path = os.path.join(temp_dir, "cookies.txt")
            with open(cookie_path, "w", encoding="utf-8") as f:
                f.write(inline_cookies)
            return cookie_path

        if self.settings.youtube_cookies_path and os.path.exists(self.settings.youtube_cookies_path):
            return self.settings.youtube_cookies_path

        return None


    async def process_download(self, job_id: int):
        # El semáforo asegura que solo N descargas se ejecuten al mismo tiempo
        async with download_semaphore:
            # Ejecutamos la lógica síncrona en un hilo para no bloquear el loop
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._process_download_sync, job_id)

    def _process_download_sync(self, job_id: int):
        job = self.db.query(database.DownloadJob).filter(database.DownloadJob.id == job_id).first()
        if not job: return
        self._set_last_reason("")

        # --- VERIFICACIÓN DE CUOTA DE DISCO (GLOBAL POOL) ---
        used_gb, limit_gb, percent = manager.get_pool_status(self.db)
        
        # Buffer de seguridad (ej. 1GB libre necesario)
        if used_gb >= limit_gb:
            job.status = "failed"
            job.current_file = "Download failed"
            job.error_log = f"Global storage limit reached ({limit_gb}GB). Please free some space."
            self.db.commit()
            return

        job.status = "processing"
        job.progress_percent = 0
        self.db.commit()

        pool_root = "/saas-data/pool"

        # 1. Determinar Playlist Destino (Opcional en Versión Moderna)
        target_playlist = None
        target_playlist_name = None

        if job.target_playlist_id:
             # Legacy lookup mapping
             legacy_pl = self.db.query(database.UserPlaylist).filter(database.UserPlaylist.id == job.target_playlist_id).first()
             if legacy_pl: 
                 target_playlist_name = legacy_pl.name
        elif job.new_playlist_name:
             target_playlist_name = job.new_playlist_name

        # Solo creamos la playlist si el usuario lo pidió explícitamente
        if target_playlist_name:
            target_playlist = self.db.query(database.Playlist).filter(
                database.Playlist.name == target_playlist_name, 
                database.Playlist.owner_id == self.user_id
            ).first()

            if not target_playlist:
                target_playlist = database.Playlist(name=target_playlist_name, owner_id=self.user_id)
                self.db.add(target_playlist)
                self.db.commit()
                self.db.refresh(target_playlist)

        # PRE-DOWNLOAD DEDUPLICATION CHECK
        # Extract source_id from URL and check if track already exists
        source_id = self._extract_source_id(job.input_url)
        if source_id:
            logger.info(f"Checking pre-download dedup for {source_id}")
            existing_track = self.db.query(database.Track).filter(database.Track.source_id == source_id).first()
            if existing_track:
                logger.info(f"PRE-DOWNLOAD DEDUP HIT: Track {existing_track.id} found for {source_id}. Skipping.")
                
                # Link existing track to the target playlist ONLY if one was requested
                if target_playlist:
                    existing_link = self.db.query(database.PlaylistItem).filter(
                        database.PlaylistItem.playlist_id == target_playlist.id,
                        database.PlaylistItem.track_id == existing_track.id
                    ).first()
                    if not existing_link:
                        new_item = database.PlaylistItem(playlist_id=target_playlist.id, track_id=existing_track.id)
                        self.db.add(new_item)
                        self.db.commit()
                        logger.info(f"Linked existing track to playlist '{target_playlist_name}'")
                
                job.status = "finished"
                job.progress_percent = 100
                job.current_file = f"Already in library: {existing_track.title}"
                self.db.commit()
                return
            else:
                logger.info(f"Pre-download dedup miss for {source_id}. Proceeding with download.")


        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                self._runtime_cookie_path = self._resolve_cookie_file(temp_dir)
                # 2. DESCARGAR EN TEMP
                success = False
                if "spotify.com" in job.input_url:
                    self._log(job_id, "Resolving Spotify track...", 5)
                    success = self._handle_spotify_robust(job.input_url, temp_dir, job_id)
                else:
                    self._log(job_id, "Downloading source audio...", 5)
                    success = self._handle_ytdlp_robust(job.input_url, temp_dir, job_id)

                if not success:
                    raise Exception(self._last_download_reason or "Source download failed before import.")

                # 3. PROCESAR ARCHIVOS (Tagging, Deduplication, Move to Pool)
                self._log(job_id, "Processing metadata and deduplicating...", 90)
                
                processed_count = 0
                for filepath in self._iter_downloaded_audio_files(temp_dir):
                    file_name = os.path.basename(filepath)
                    
                    # A. Extraer Metadatos y Hash
                    artist = "Unknown Artist"
                    title = os.path.splitext(file_name)[0]
                    album = "Unknown Album"
                    source_id = None
                    identity = None
                    
                    try:
                        # Hashing
                        file_hash = utils.get_file_hash(filepath) if hasattr(utils, 'get_file_hash') else None
                        if not file_hash:
                            # Inline hash calculation if utility missing
                            import hashlib
                            sha = hashlib.sha256()
                            with open(filepath, 'rb') as f:
                                while True:
                                    data = f.read(65536)
                                    if not data: break
                                    sha.update(data)
                            file_hash = sha.hexdigest()

                        # Tagging & Metadata Reading
                        try:
                            audio = mutagen.File(filepath, easy=True)
                            if audio:
                                if 'artist' in audio: artist = audio['artist'][0]
                                if 'title' in audio: title = audio['title'][0]
                                if 'album' in audio: album = audio['album'][0]
                                audio.save()
                        except Exception as e:
                            logger.warning(f"Error reading tags: {e}")

                        # Intento de adivinar Source ID
                        if "spotify" in job.input_url and job.input_url.count("track") == 1:
                             # Single track download, use input URL as ID base
                             source_id = f"spotify:track:{job.input_url.split('/')[-1].split('?')[0]}"
                        elif "youtu" in job.input_url:
                             # Handle both youtube.com/watch?v= and youtu.be/
                             if "v=" in job.input_url:
                                 vid = job.input_url.split("v=")[-1].split("&")[0]
                                 source_id = f"youtube:{vid}"
                             elif "youtu.be/" in job.input_url:
                                 vid = job.input_url.split("youtu.be/")[-1].split("?")[0]
                                 if len(vid) == 11: 
                                     source_id = f"youtube:{vid}"
                                 else:
                                     source_id = f"local:{file_hash}"
                             else:
                                 # Fallback for other formats
                                 vid = job.input_url.split("/")[-1].split("?")[0]
                                 if len(vid) == 11:
                                     source_id = f"youtube:{vid}"
                                 else:
                                     source_id = f"local:{file_hash}"
                        else:
                             # Fallback: Hash-based ID
                             source_id = f"local:{file_hash}"

                        identity = track_identity.compute_track_identity(artist, title)

                    except Exception as e:
                        logger.error(f"Metadata error: {e}")
                        continue

                    # B. Deduplication Check (DB)
                    track = track_identity.find_existing_track(
                        self.db,
                        source_id=source_id,
                        file_hash=file_hash,
                        fingerprint=identity["fingerprint"] if identity else None,
                    )

                    final_path = ""
                    
                    if track:
                        logger.info(f"Deduplication: Track found (ID: {track.id}). Linking.")
                        final_path = track.filepath
                        # Si el archivo físico no existe, lo restauramos?
                        if final_path and not os.path.exists(final_path):
                             # Restore file from temp
                             pool_dir = os.path.dirname(final_path)
                             os.makedirs(pool_dir, exist_ok=True)
                             shutil.move(filepath, final_path)
                    else:
                        # C. Move to Pool
                        safe_artist = "".join(c for c in artist if c.isalnum() or c in (' ', '-', '_')).strip() or "Unknown"
                        safe_album = "".join(c for c in album if c.isalnum() or c in (' ', '-', '_')).strip() or "Unknown"
                        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).strip()
                        
                        pool_dir = os.path.join(pool_root, safe_artist, safe_album)
                        os.makedirs(pool_dir, exist_ok=True)
                        
                        new_filename = f"{safe_title}{os.path.splitext(file_name)[1]}"
                        final_path = os.path.join(pool_dir, new_filename)
                        
                        # Collision check
                        if os.path.exists(final_path):
                            new_filename = f"{safe_title}_{file_hash[:6]}{os.path.splitext(file_name)[1]}"
                            final_path = os.path.join(pool_dir, new_filename)
                            
                        shutil.move(filepath, final_path)
                        
                        # D. Create Track Record
                        track = database.Track(
                            title=title, artist=artist, album=album,
                            source_id=source_id, file_hash=file_hash,
                            artist_norm=identity["artist_norm"] if identity else None,
                            title_norm=identity["title_norm"] if identity else None,
                            version_tag=identity["version_tag"] if identity else None,
                            fingerprint=identity["fingerprint"] if identity else None,
                            filepath=final_path, source_provider="download"
                        )
                        self.db.add(track)
                        self.db.commit()
                        self.db.refresh(track)

                    # E. Link to Playlist (Only if requested)
                    if target_playlist:
                        # Check if already in playlist
                        item_exists = self.db.query(database.PlaylistItem).filter_by(
                            playlist_id=target_playlist.id, track_id=track.id
                        ).first()
                        
                        if not item_exists:
                            pos = self.db.query(database.PlaylistItem).filter_by(playlist_id=target_playlist.id).count() + 1
                            item = database.PlaylistItem(
                                playlist_id=target_playlist.id, track_id=track.id, position=pos
                            )
                            self.db.add(item)
                            self.db.commit()
                    
                    processed_count += 1

                if processed_count == 0:
                    # DEBUG: List directory contents to see what happened
                    try:
                        files_in_temp = os.listdir(temp_dir)
                        logger.error(f"No processed files. Temp dir contents: {files_in_temp}")
                    except:
                        pass
                    raise Exception("No audio files were produced by the downloader.")

                # 4. Finalizar
                # Generar M3U para Navidrome en una carpeta visible??
                # Phase 4 handles explicit M3U generation.
                # For now, we are good. Database is source of truth.
                
                job.status = "completed"
                job.progress_percent = 100
                job.current_file = "Imported to library"
                self.db.commit()

            except Exception as e:
                logger.error(f"Critical error in job {job_id}: {e}")
                job = self.db.merge(job)
                job.status = "failed"
                job.current_file = "Download failed"
                job.error_log = str(e)
                self.db.commit()
            finally:
                self._runtime_cookie_path = None

    def _handle_spotify_robust(self, url, folder, job_id):
        """
        Lógica mejorada:
        1. Intento SpotiFLAC -> audio lossless desde proveedor tercero
        2. Intento spotDL con metadata de Spotify
        3. Fallback yt-dlp
        """
        if shutil.which("spotiflac"):
            self._log(job_id, "SpotiFLAC module mode...", 8)
            if self._run_spotiflac_cmd(url, folder):
                return True
            self._log(job_id, "SpotiFLAC failed. Falling back to spotDL...", 10)
        else:
            logger.info("SpotiFLAC command not found. Falling back to spotDL.")
        
        # 1. Intento con Claves (si existen)
        if self.settings.spotify_client_id:
            self._log(job_id, "SpotDL metadata mode...", 10)
            if self._run_spotdl_cmd(url, folder, mode="full", use_auth=True):
                return True
            if self._spotdl_should_bypass_retries():
                self._log(job_id, "spotDL metadata parsing failed. Switching to yt-dlp fallback...", 20)
                return self._handle_spotify_query_fallback(url, folder, job_id)
            self._log(job_id, "spotDL API failed. Switching to anonymous mode...", 20)
        
        # 2. Intento Anónimo (320k) - AQUÍ ESTÁ EL FIX
        self._log(job_id, "spotDL anonymous mode...", 30)
        if self._run_spotdl_cmd(url, folder, mode="full", use_auth=False):
            return True
        if self._spotdl_should_bypass_retries():
            self._log(job_id, "spotDL metadata parsing failed. Switching to yt-dlp fallback...", 40)
            return self._handle_spotify_query_fallback(url, folder, job_id)
        
        # 3. Intento Básico (128k)
        self._log(job_id, "spotDL basic mode retry (128k)...", 50)
        if self._run_spotdl_cmd(url, folder, mode="basic", use_auth=False):
            return True

        # 4. Fallback: resolver metadata desde Spotify y descargar vía ytsearch + yt-dlp
        self._log(job_id, "spotDL failed. Activating yt-dlp metadata fallback...", 60)
        if self._handle_spotify_query_fallback(url, folder, job_id):
            return True

        if not self._last_download_reason:
            self._set_last_reason("SpotiFLAC, spotDL, and yt-dlp Spotify fallbacks all failed.")
        
        return False

    def _run_spotiflac_cmd(self, url, folder):
        cmd = [
            "spotiflac",
            url,
            folder,
            "--service",
            "tidal",
            "spoti",
            "qobuz",
            "amazon",
        ]
        try:
            logger.info(f"SpotiFLAC CMD: {' '.join(cmd)}")
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = process.communicate(timeout=2400)

            if process.returncode == 0 and self._has_downloaded_audio(folder):
                self._set_last_reason("")
                return True

            combined = ((stderr or "").strip()[-1500:] or (stdout or "").strip()[-1500:] or "").strip()
            if process.returncode == 0 and not self._has_downloaded_audio(folder):
                self._set_last_reason("SpotiFLAC finished without producing an audio file.")
            else:
                self._set_last_reason(combined or f"SpotiFLAC exited with code {process.returncode}.")
            if combined:
                logger.warning(f"SpotiFLAC error: {combined}")
            return False
        except Exception as e:
            self._set_last_reason(f"SpotiFLAC exception: {e}")
            logger.error(f"SpotiFLAC Ex: {e}")
            return False

    def _build_spotify_ytsearch_query(self, url: str) -> str | None:
        """
        Build a ytsearch query from a Spotify track URL.
        Used when spotDL fails due to upstream API/schema issues.
        """
        source_id = self._extract_source_id(url)
        if not source_id or not source_id.startswith("spotify:track:"):
            return None

        track_id = source_id.split(":")[-1]

        if not (self.settings.spotify_client_id and self.settings.spotify_client_secret):
            return None

        try:
            auth_raw = f"{self.settings.spotify_client_id}:{self.settings.spotify_client_secret}".encode("utf-8")
            auth_b64 = base64.b64encode(auth_raw).decode("utf-8")

            with httpx.Client(timeout=10.0) as client:
                token_resp = client.post(
                    "https://accounts.spotify.com/api/token",
                    headers={"Authorization": f"Basic {auth_b64}"},
                    data={"grant_type": "client_credentials"},
                )
                if token_resp.status_code != 200:
                    return None

                token = token_resp.json().get("access_token")
                if not token:
                    return None

                track_resp = client.get(
                    f"https://api.spotify.com/v1/tracks/{track_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if track_resp.status_code != 200:
                    return None

                track = track_resp.json()
                title = track.get("name") or ""
                artists = track.get("artists") or []
                artist = artists[0].get("name", "") if artists else ""

            query = f"ytsearch1:{artist} {title} official audio".strip()
            return query if query != "ytsearch1: official audio" else None
        except Exception as e:
            logger.warning(f"Spotify fallback metadata resolve failed: {e}")
            return None

    def _build_spotify_ytsearch_queries(self, url: str) -> list[str]:
        base_query = self._build_spotify_ytsearch_query(url)
        if not base_query:
            return []

        query_text = base_query.replace("ytsearch1:", "", 1).strip()
        if not query_text:
            return []

        artist_and_title = query_text.replace(" official audio", "").strip()
        compact = re.sub(r"\s+", " ", artist_and_title).strip()
        compact_no_parens = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", compact).strip()

        candidates = [
            f"ytsearch1:{compact} audio",
            f"ytsearch1:{compact} topic",
            f"ytsearch1:{compact} official audio",
            f"ytsearch1:{compact_no_parens} audio" if compact_no_parens else "",
            f"ytsearch1:{compact_no_parens} topic" if compact_no_parens else "",
            f"ytsearch1:{compact_no_parens} official audio" if compact_no_parens else "",
        ]

        deduped = []
        seen = set()
        for candidate in candidates:
            normalized = candidate.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                deduped.append(normalized)
        return deduped

    def _handle_spotify_query_fallback(self, url: str, folder: str, job_id: int) -> bool:
        queries = self._build_spotify_ytsearch_queries(url)
        if not queries:
            self._set_last_reason("spotDL could not resolve the Spotify track metadata for fallback.")
            return False

        for index, query in enumerate(queries, start=1):
            logger.info(f"[Job {job_id}] Spotify fallback query {index}/{len(queries)}: {query}")
            self._log(job_id, f"Spotify fallback search {index}/{len(queries)}...", 65)
            if self._handle_ytdlp_robust(query, folder, job_id):
                return True

        return False

    def _run_spotdl_cmd(self, url, folder, mode="full", use_auth=True):
        cmd = ["spotdl", "download", url]
        
        # Lógica de Auth Condicional
        if use_auth and self.settings.spotify_client_id and self.settings.spotify_client_secret:
            cmd.extend(["--client-id", self.settings.spotify_client_id, 
                        "--client-secret", self.settings.spotify_client_secret])
        
        # Cookies siempre (ayudan a evitar rate limits de YouTube)
        if self._runtime_cookie_path and os.path.exists(self._runtime_cookie_path):
            cmd.extend(["--cookie-file", self._runtime_cookie_path])
        
        # FIX: Pasar argumentos a yt-dlp interno de spotdl para EJS challenge solving
        # Nota: spotdl requiere los args separados por espacio dentro de una sola cadena
        cmd.extend(["--yt-dlp-args", "--remote-components ejs:github --js-runtimes deno,node"])

        if mode == "full":
            cmd.extend([
                "--output", f"{folder}/{{artist}} - {{title}}.{{ext}}",
                "--format", "mp3",
                "--bitrate", "256k",
                "--overwrite", "skip",
                "--print-errors"
            ])
        elif mode == "basic":
            cmd.extend([
                "--output", f"{folder}/{{artist}} - {{title}}.{{ext}}",
                "--format", "mp3",
                "--bitrate", "128k",
                "--print-errors"
            ])

        try:
            safe_cmd = []
            skip_next = False
            for i, part in enumerate(cmd):
                if skip_next:
                    skip_next = False
                    continue
                if part in ["--client-secret", "--cookie-file"] and i + 1 < len(cmd):
                    safe_cmd.extend([part, "***"])
                    skip_next = True
                else:
                    safe_cmd.append(part)

            logger.info(f"SpotDL CMD (Auth={use_auth}): {' '.join(safe_cmd)}")
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = process.communicate(timeout=2400)
            
            if process.returncode == 0: 
                self._set_last_reason("")
                return True
            else:
                stdout_snippet = (stdout or "").strip()[-1500:]
                stderr_snippet = (stderr or "").strip()[-1500:]
                combined = stderr_snippet or stdout_snippet

                # Detectamos Rate Limit para el log, pero devolvemos False para que el gestor reintente
                if "rate/request limit" in combined.lower():
                    self._set_last_reason("spotDL hit an upstream rate limit.")
                    logger.warning("SpotDL Rate Limit detectado en stderr")
                elif "keyerror: 'genres'" in combined.lower() or 'keyerror: "genres"' in combined.lower():
                    self._set_last_reason("spotDL hit the upstream Spotify metadata bug: missing 'genres'.")
                    logger.warning("SpotDL metadata parsing bug detected: missing 'genres'")
                else:
                    self._set_last_reason(combined or f"spotDL exited with code {process.returncode}.")
                    if combined:
                        logger.warning(f"SpotDL Error: {combined}")
                    else:
                        logger.warning(f"SpotDL Error: returncode={process.returncode} sin salida en stdout/stderr")
                return False
        except Exception as e:
            self._set_last_reason(f"spotDL exception: {e}")
            logger.error(f"SpotDL Ex: {e}")
            return False

    def _handle_ytdlp_robust(self, url, folder, job_id):
        def progress_hook(d):
            if d['status'] == 'downloading':
                try:
                    # Robust progress extraction
                    p_str = d.get('_percent_str', '0%').strip()
                    # Remove ANSI codes and %
                    p_clean = re.sub(r'\x1b\[[0-9;]*m', '', p_str).replace('%', '').strip()
                    p_float = float(p_clean)
                    
                    filename = d.get('filename', 'Downloading...')
                    basename = os.path.basename(filename)
                    
                    self._log(job_id, f"Downloading: {basename}", p_float)
                except Exception as e:
                    logger.debug(f"Progress hook error: {e}")

        # Si el usuario NO marcó playlist, forzamos descarga de solo el vídeo
        is_playlist_url = "list=" in url
        
        client_strategies = [
             {'player_client': ['web'], 'skip': ['dash', 'hls']},
             {'player_client': ['web_embedded'], 'skip': ['dash', 'hls']},
             {'player_client': ['ios', 'web'], 'skip': ['dash', 'hls']},
             {'player_client': ['android', 'web'], 'skip': ['dash', 'hls']},
             {'player_client': ['android_vr'], 'skip': ['dash', 'hls']}
        ]
        age_gate_cookie_strategies = [
             {'player_client': ['tv', 'web_safari'], 'skip': []},
             {'player_client': ['tv_embedded', 'web_safari'], 'skip': []},
             {'player_client': ['web_embedded', 'web_safari'], 'skip': []},
             {'player_client': ['web', 'web_safari'], 'skip': []},
        ]

        # Base options
        base_opts = {
            'format': 'bestaudio/best',
            'outtmpl': f'{folder}/%(artist)s - %(title)s.%(ext)s',
            'writethumbnail': True,
            'age_limit': 99,
            'noplaylist': not is_playlist_url, 
            'extract_flat': False,
            'ignoreerrors': False,
            'source_address': '0.0.0.0',
            'force_ipv4': True, 
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }, {'key': 'EmbedThumbnail'}, {'key': 'FFmpegMetadata'}],
            'progress_hooks': [progress_hook],
            'js_runtimes': {'deno': {}, 'node': {}},
            'remote_components': ['ejs:github'],
            'sleep_interval': 5,
            'max_sleep_interval': 10,
            'retries': 2,
            'extractor_retries': 2,
            'file_access_retries': 2,
        }

        retry_state = {"force_cookies": False}

        def run_with_cookie_mode(use_cookies: bool) -> bool:
            strategies = client_strategies
            if use_cookies and retry_state["force_cookies"]:
                strategies = age_gate_cookie_strategies
            elif use_cookies:
                strategies = [s for s in client_strategies if "android" not in s.get('player_client', [])]

            for i, strategy_config in enumerate(strategies):
                try:
                    opts = base_opts.copy()
                    if use_cookies and self._runtime_cookie_path and os.path.exists(self._runtime_cookie_path):
                        opts['cookiefile'] = self._runtime_cookie_path
                    opts['extractor_args'] = {'youtube': strategy_config}
                    if retry_state["force_cookies"] and use_cookies:
                        opts['format'] = 'bestaudio/best'
                        opts['concurrent_fragment_downloads'] = 1

                    logger.info(
                        f"[Job {job_id}] Attempting download with Strategy {i+1}"
                        f" (cookies={'on' if use_cookies else 'off'}): {strategy_config['player_client']}"
                    )

                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.extract_info(url, download=True)
                        if not self._has_downloaded_audio(folder):
                            self._set_last_reason("yt-dlp finished without producing an audio file.")
                            logger.warning(f"[Job {job_id}] Strategy {i+1} finished but produced no audio files.")
                            continue
                        self._set_last_reason("")
                        return True
                except Exception as e:
                    err_str = str(e).lower()
                    if self._is_youtube_age_gate_error(err_str):
                        if use_cookies:
                            self._set_last_reason(
                                "YouTube age-restricted content still failed with the configured cookies."
                            )
                        elif self._runtime_cookie_path:
                            self._set_last_reason(
                                "YouTube age-restricted content detected. Retrying with authenticated cookies."
                            )
                            retry_state["force_cookies"] = True
                        else:
                            self._set_last_reason(
                                "YouTube age-restricted content requires an authenticated cookies.txt file."
                            )
                        logger.warning(f"[Job {job_id}] Age-gated YouTube content detected.")
                        if not use_cookies and self._runtime_cookie_path:
                            break
                    elif self._is_youtube_bot_challenge_error(err_str):
                        if use_cookies:
                            self._set_last_reason(
                                "YouTube bot challenge persisted even with the configured cookies."
                            )
                        elif self._runtime_cookie_path:
                            self._set_last_reason(
                                "YouTube bot challenge detected. Continuing with alternate clients before a final authenticated retry."
                            )
                        else:
                            self._set_last_reason(
                                "YouTube bot challenge detected and no authenticated cookies.txt file is configured."
                            )
                        logger.warning(f"[Job {job_id}] YouTube bot challenge detected.")
                    else:
                        self._set_last_reason(str(e))
                    logger.warning(f"[Job {job_id}] Strategy {i+1} failed: {err_str[:140]}...")
                    continue
            return False

        prefer_cookieless = str(url).startswith("ytsearch")

        if prefer_cookieless:
            if run_with_cookie_mode(False):
                return True
            if self._runtime_cookie_path:
                if retry_state["force_cookies"]:
                    self._log(job_id, "Age-restricted content detected. Retrying with cookies...", 72)
                else:
                    self._log(job_id, "Cookieless search failed. Retrying with cookies...", 72)
                if run_with_cookie_mode(True):
                    return True
        else:
            if run_with_cookie_mode(True):
                return True
            if self._runtime_cookie_path:
                self._log(job_id, "Cookie-based download failed. Retrying without cookies...", 72)
                if run_with_cookie_mode(False):
                    return True
                if retry_state["force_cookies"]:
                    self._log(job_id, "Age-restricted content detected. Retrying with cookies...", 74)
                    if run_with_cookie_mode(True):
                        return True
        
        # If all strategies failed
        if not self._last_download_reason:
            self._set_last_reason("yt-dlp exhausted all download strategies.")
        logger.error(f"[Job {job_id}] All download strategies failed.")
        return False
        




    def _sync_folder_to_db(self, playlist_db):
        """Tu función original de sync"""
        if not os.path.exists(playlist_db.folder_path): return
        files = [f for f in os.listdir(playlist_db.folder_path) if f.endswith(('.mp3', '.m4a', '.flac'))]
        
        self.db.query(database.PlaylistTrack).filter(database.PlaylistTrack.playlist_id == playlist_db.id).delete()
        
        for f in files:
            path = os.path.join(playlist_db.folder_path, f)
            # Solo añadimos si tiene un tamaño decente (evitar archivos corruptos vacíos)
            if os.path.getsize(path) > 10000: 
                track = database.PlaylistTrack(playlist_id=playlist_db.id, title=f, file_path=path, source_id="local")
                self.db.add(track)
        self.db.commit()

    def _generate_m3u(self, folder, name):
        """
        Genera un M3U forzando UTF-8 sin BOM para que Navidrome no se pierda.
        """
        files = [f for f in os.listdir(folder) if f.endswith(('.mp3', '.m4a', '.flac'))]
        files.sort()
        try:
            m3u_path = os.path.join(folder, f"{name}.m3u")
            # Forzamos encoding utf-8 explícito
            with open(m3u_path, 'w', encoding='utf-8') as f:
                f.write("#EXTM3U\n")
                for file in files:
                    f.write(f"{file}\n")
            logger.info(f"M3U generado exitosamente: {m3u_path}")
        except Exception as e:
            logger.error(f"Error generando M3U: {e}")
