import httpx
import time
import json
import os
import asyncio
import base64
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor

CACHE_DIR = "/saas-data/cache"
TOKEN_CACHE_PATH = f"{CACHE_DIR}/spotify_token.json"
REQS_CACHE_PATH = f"{CACHE_DIR}/spotify_new_releases.json"
SPOTIFY_SEARCH_MAX_LIMIT = 10

# Crear un pool global para tareas de CPU
cpu_executor = ThreadPoolExecutor(max_workers=4)

def parse_spotify_html(html_text):
    """Función síncrona que realiza el trabajo pesado de CPU"""
    soup = BeautifulSoup(html_text, 'html.parser')
    script = soup.find("script", id="__NEXT_DATA__")
    if not script: return None
    data = json.loads(script.string)
    try:
        return data.get('props', {}).get('pageProps', {}).get('state', {}).get('data', {}).get('entity', {}).get('audioPreview', {}).get('url')
    except (KeyError, TypeError):
        return None

class SpotifyService:
    def __init__(self):
        os.makedirs(CACHE_DIR, exist_ok=True)
        # Usamos httpx con HTTP/2 habilitado para mejor rendimiento
        self.client = httpx.AsyncClient(timeout=10.0, http2=True)

    async def _get_access_token(self, client_id: str, client_secret: str) -> Optional[str]:
        secret_fingerprint = base64.b64encode(client_secret.encode("utf-8")).decode("utf-8")[:16]
        # 1. Check disk cache
        if os.path.exists(TOKEN_CACHE_PATH):
            try:
                with open(TOKEN_CACHE_PATH, 'r') as f:
                    cache = json.load(f)
                    if (
                        cache.get("expires_at", 0) > time.time()
                        and cache.get("client_id") == client_id
                        and cache.get("secret_fingerprint") == secret_fingerprint
                    ):
                        return cache.get("access_token")
            except:
                pass

        # 2. Fetch new token from OFFICIAL API
        url = "https://accounts.spotify.com/api/token"
        data = {"grant_type": "client_credentials"}
        
        try:
            # httpx maneja la autenticación Basic automáticamente con auth=()
            resp = await self.client.post(url, data=data, auth=(client_id, client_secret))
            
            if resp.status_code != 200:
                print(f"[SPOTIFY-SERVICE] Failed to get token: {resp.text}")
                return None
            
            res = resp.json()
            token = res["access_token"]
            expires_in = res["expires_in"]
            
            # Save to cache
            with open(TOKEN_CACHE_PATH, 'w') as f:
                json.dump({
                    "access_token": token,
                    "expires_at": time.time() + expires_in - 60,
                    "client_id": client_id,
                    "secret_fingerprint": secret_fingerprint,
                }, f)
            
            return token
        except Exception as e:
            print(f"[SPOTIFY-SERVICE] Token error: {e}")
            return None

    async def validate_credentials(self, client_id: str, client_secret: str) -> bool:
        token = await self._get_access_token(client_id, client_secret)
        if not token:
            return False
        try:
            resp = await self.client.get(
                "https://api.spotify.com/v1/search",
                headers={"Authorization": f"Bearer {token}"},
                params={"q": "Daft Punk", "type": "track", "limit": 1},
            )
            return resp.status_code == 200
        except Exception:
            return False

    async def search_item(self, client_id: str, client_secret: str, query: str, type: str = "track", limit: int = 1) -> Optional[Dict]:
        """Busca una canción o artista para obtener su ID"""
        results = await self.search_tracks(client_id, client_secret, query, type, limit)
        return results[0] if results else None

    async def get_track_by_id(self, client_id: str, client_secret: str, track_id: str) -> Optional[Dict]:
        token = await self._get_access_token(client_id, client_secret)
        if not token or not track_id:
            return None

        url = f"https://api.spotify.com/v1/tracks/{track_id}"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            resp = await self.client.get(url, headers=headers)
            if resp.status_code != 200:
                return None

            track = resp.json()
            artist_name = "Unknown"
            if track.get("artists"):
                artist_name = track["artists"][0].get("name", "Unknown")

            image = ""
            album = track.get("album") or {}
            if album.get("images"):
                image = album["images"][0].get("url", "")

            return {
                "id": track.get("id"),
                "name": track.get("name") or "Unknown",
                "artist": artist_name,
                "album": album.get("name", ""),
                "image": image,
                "url": (track.get("external_urls") or {}).get("spotify", ""),
                "release_date": album.get("release_date", ""),
                "preview_url": track.get("preview_url"),
            }
        except Exception:
            return None

    async def search_tracks(self, client_id: str, client_secret: str, query: str, type: str = "track", limit: int = 10) -> list:
        """Busca canciones y retorna lista completa (API OFICIAL)"""
        token = await self._get_access_token(client_id, client_secret)
        if not token: return []

        try:
            normalized_limit = int(limit)
        except Exception:
            normalized_limit = 10
        normalized_limit = max(1, min(normalized_limit, SPOTIFY_SEARCH_MAX_LIMIT))

        url = "https://api.spotify.com/v1/search"
        headers = {"Authorization": f"Bearer {token}"}
        params = {"q": query, "type": type, "limit": normalized_limit}

        try:
            resp = await self.client.get(url, headers=headers, params=params)
            if resp.status_code != 200: 
                print(f"[SPOTIFY-SEARCH] Error {resp.status_code}: {resp.text}")
                return []
            
            data = resp.json()
            items = []
            
            # Spotify devuelve la key en plural (tracks, artists, albums...)
            key = f"{type}s"
            
            for track in data.get(key, {}).get("items", []):
                img = ""
                artist_name = "Unknown"
                track_name = track["name"]
                
                if type == "track":
                    if track["album"]["images"]: img = track["album"]["images"][0]["url"]
                    if track["artists"]: artist_name = track["artists"][0]["name"]
                elif type == "artist":
                    if track["images"]: img = track["images"][0]["url"]
                    artist_name = track["name"]

                items.append({
                    "id": track["id"],
                    "name": track_name,
                    "artist": artist_name,
                    "album": track.get("album", {}).get("name", "") if type == "track" else "",
                    "image": img,
                    "url": track["external_urls"]["spotify"],
                    "release_date": track.get("album", {}).get("release_date", "") if type == "track" else "",
                    "preview_url": track.get("preview_url")
                })
            return items
        except Exception as e:
            print(f"[SPOTIFY-SEARCH] Exception: {e}")
            return []

    async def get_recommendations(self, client_id: str, client_secret: str, seed_tracks: list = None, seed_artists: list = None, limit: int = 12, country: str = "ES", cache_path: str = None) -> list:
        """
        Genera recomendaciones basadas en artistas usando 'Get Artist's Top Tracks'.
        NOTA: El endpoint /v1/recommendations fue deprecado por Spotify en Nov 2024.
        Esta alternativa obtiene los top tracks de cada artista y los mezcla.
        """
        
        # 1. Check Cache
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, 'r') as f:
                    cache = json.load(f)
                    if cache.get("expires_at", 0) > time.time():
                        return cache.get("items", [])
            except:
                pass

        token = await self._get_access_token(client_id, client_secret)
        if not token: return []

        headers = {"Authorization": f"Bearer {token}"}
        
        # Combinar seeds (priorizamos artistas ya que tracks individuales no tienen endpoint alternativo fácil)
        artist_ids = list(seed_artists) if seed_artists else []
        
        # Si tenemos seed_tracks pero no artistas, intentamos extraer artistas de esos tracks
        # (Esto requeriría otra llamada, así que por simplicidad usamos solo artistas)
        
        if not artist_ids:
            print("[SPOTIFY] No hay artistas seed para recomendaciones")
            return []
        
        items = []
        seen_track_ids = set()  # Evitar duplicados
        
        try:
            # Para cada artista, obtener sus top tracks
            tracks_per_artist = max(3, limit // len(artist_ids) + 1)
            
            for artist_id in artist_ids[:5]:  # Límite de 5 artistas
                url = f"https://api.spotify.com/v1/artists/{artist_id}/top-tracks"
                params = {"market": country}
                
                resp = await self.client.get(url, headers=headers, params=params)
                if resp.status_code != 200:
                    print(f"[SPOTIFY] Top tracks error for {artist_id}: {resp.status_code}")
                    continue
                
                data = resp.json()
                tracks = data.get("tracks", [])[:tracks_per_artist]
                
                for track in tracks:
                    if track["id"] in seen_track_ids:
                        continue
                    seen_track_ids.add(track["id"])
                    
                    items.append({
                        "id": track["id"],
                        "name": track["name"],
                        "artist": track["artists"][0]["name"],
                        "image": track["album"]["images"][0]["url"] if track["album"]["images"] else "",
                        "url": track["external_urls"]["spotify"],
                        "release_date": track["album"].get("release_date", ""),
                        "preview_url": track.get("preview_url")
                    })
            
            # Mezclar para variedad y limitar
            import random
            random.shuffle(items)
            items = items[:limit]
            
            # Save to cache (7 days)
            if cache_path and items:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, 'w') as f:
                    json.dump({
                        "items": items,
                        "expires_at": time.time() + (7 * 24 * 3600)
                    }, f)

            print(f"[SPOTIFY] Generadas {len(items)} recomendaciones desde {len(artist_ids)} artistas")
            return items
            
        except Exception as e:
            print(f"[SPOTIFY] Rec exception: {e}")
            return []

    async def get_new_releases(self, client_id: str, client_secret: str, country: str = "ES", limit: int = 12, cache_path: str = None) -> list:
        # 1. Use specific or global cache
        effective_cache = cache_path or REQS_CACHE_PATH
        if os.path.exists(effective_cache):
            try:
                with open(effective_cache, 'r') as f:
                    cache = json.load(f)
                    if cache.get("expires_at", 0) > time.time():
                        return cache.get("items", [])
            except:
                pass

        token = await self._get_access_token(client_id, client_secret)
        if not token: return []

        # OFFICIAL API
        url = "https://api.spotify.com/v1/browse/new-releases"
        headers = {"Authorization": f"Bearer {token}"}
        params = {"country": country, "limit": limit}
        
        try:
            resp = await self.client.get(url, headers=headers, params=params)
            if resp.status_code != 200:
                print(f"[SPOTIFY] New releases error: {resp.text}")
                return []
            
            data = resp.json()
            items = []
            for alb in data.get("albums", {}).get("items", []):
                items.append({
                    "id": alb["id"],
                    "name": alb["name"],
                    "artist": alb["artists"][0]["name"],
                    "image": alb["images"][0]["url"] if alb["images"] else "",
                    "url": alb["external_urls"]["spotify"],
                    "release_date": alb["release_date"]
                })

            expiration = (7 * 24 * 3600) if cache_path else (6 * 3600)
            os.makedirs(os.path.dirname(effective_cache), exist_ok=True)
            with open(effective_cache, 'w') as f:
                json.dump({
                    "items": items,
                    "expires_at": time.time() + expiration
                }, f)
            
            return items
        except Exception as e:
            print(f"[SPOTIFY-SERVICE] Fetch error: {e}")
            return []

    async def get_embed_preview(self, track_id: str) -> Optional[str]:
        """
        Scraping directo de open.spotify.com para evitar el bloqueo del proxy.
        """
        url = f"https://open.spotify.com/embed/track/{track_id}"
        
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            resp = await self.client.get(url, headers=headers)
            if resp.status_code != 200:
                return None

            # Delegar el parseo al executor de hilos para no bloquear el loop asíncrono
            loop = asyncio.get_event_loop()
            preview_url = await loop.run_in_executor(cpu_executor, parse_spotify_html, resp.text)
            return preview_url

        except Exception as e:
            print(f"[SPOTIFY-EMBED] Error scraping preview: {e}")
            return None

# Singleton instance
spotify_service = SpotifyService()
