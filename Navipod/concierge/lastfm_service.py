import httpx
from typing import Dict, List, Optional


class LastFmService:
    BASE_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10.0)

    async def validate_api_key(self, api_key: str) -> bool:
        if not api_key:
            return False

        params = {
            "method": "track.search",
            "track": "Daft Punk",
            "api_key": api_key,
            "format": "json",
            "limit": 1,
        }
        try:
            resp = await self.client.get(self.BASE_URL, params=params)
            if resp.status_code != 200:
                return False
            data = resp.json()
            return "error" not in data
        except Exception:
            return False

    async def search_tracks(self, api_key: str, query: str, limit: int = 10) -> List[Dict]:
        if not api_key or not query:
            return []

        params = {
            "method": "track.search",
            "track": query,
            "api_key": api_key,
            "format": "json",
            "limit": limit,
        }
        try:
            resp = await self.client.get(self.BASE_URL, params=params)
            if resp.status_code != 200:
                return []
            data = resp.json()
            tracks = data.get("results", {}).get("trackmatches", {}).get("track", [])
            if isinstance(tracks, dict):
                tracks = [tracks]

            out = []
            for t in tracks:
                title = t.get("name") or "Unknown"
                artist = t.get("artist") or "Unknown"
                # Extract best available image from Last.fm response
                image_url = ""
                images = t.get("image", [])
                if images:
                    # Prefer extralarge > large > medium > small
                    for size_pref in ["extralarge", "large", "medium", "small"]:
                        for img in images:
                            if img.get("size") == size_pref and img.get("#text"):
                                image_url = img["#text"]
                                break
                        if image_url:
                            break
                out.append(
                    {
                        "id": f"lastfm:{artist}:{title}",
                        "name": title,
                        "artist": artist,
                        "album": "",
                        "image": image_url,
                        "url": t.get("url"),
                        "source": "lastfm",
                    }
                )
            return out
        except Exception:
            return []

    async def get_track_tags(self, api_key: str, artist: str, track: str) -> List[str]:
        if not api_key or not artist or not track:
            return []

        params = {
            "method": "track.getInfo",
            "artist": artist,
            "track": track,
            "api_key": api_key,
            "format": "json",
        }
        try:
            resp = await self.client.get(self.BASE_URL, params=params)
            if resp.status_code != 200:
                return []
            data = resp.json()
            tags = data.get("track", {}).get("toptags", {}).get("tag", [])
            if isinstance(tags, dict):
                tags = [tags]
            return [t.get("name") for t in tags if t.get("name")]
        except Exception:
            return []

    async def get_track_info(self, api_key: str, artist: str, track: str) -> Dict:
        """Get full track info including album art (more reliable than search images)"""
        if not api_key or not artist or not track:
            return {}

        params = {
            "method": "track.getInfo",
            "artist": artist,
            "track": track,
            "api_key": api_key,
            "format": "json",
        }
        try:
            resp = await self.client.get(self.BASE_URL, params=params)
            if resp.status_code != 200:
                return {}
            data = resp.json()
            track_data = data.get("track", {})
            
            # Extract album image (track.getInfo includes album with images)
            album_info = track_data.get("album", {})
            image_url = ""
            images = album_info.get("image", [])
            if images:
                for size_pref in ["extralarge", "large", "medium", "small"]:
                    for img in images:
                        if img.get("size") == size_pref and img.get("#text"):
                            image_url = img["#text"]
                            break
                    if image_url:
                        break
            
            return {
                "album": album_info.get("title", ""),
                "image": image_url,
                "listeners": track_data.get("listeners", ""),
                "playcount": track_data.get("playcount", ""),
            }
        except Exception:
            return {}

    async def get_top_tracks(self, api_key: str, limit: int = 12) -> List[Dict]:
        """Get Last.fm top/popular tracks for recommendation feed"""
        if not api_key:
            return []

        params = {
            "method": "chart.getTopTracks",
            "api_key": api_key,
            "format": "json",
            "limit": limit,
        }
        try:
            resp = await self.client.get(self.BASE_URL, params=params)
            if resp.status_code != 200:
                return []
            data = resp.json()
            tracks = data.get("tracks", {}).get("track", [])
            if isinstance(tracks, dict):
                tracks = [tracks]

            out = []
            for t in tracks:
                title = t.get("name") or "Unknown"
                artist_info = t.get("artist", {})
                artist = artist_info.get("name") if isinstance(artist_info, dict) else str(artist_info) or "Unknown"
                # chart.getTopTracks has images
                image_url = ""
                images = t.get("image", [])
                if images:
                    for size_pref in ["extralarge", "large", "medium"]:
                        for img in images:
                            if img.get("size") == size_pref and img.get("#text"):
                                image_url = img["#text"]
                                break
                        if image_url:
                            break
                out.append({
                    "id": f"lastfm:{artist}:{title}",
                    "name": title,
                    "artist": artist,
                    "album": "",
                    "image": image_url,
                    "source": "lastfm",
                })
            return out
        except Exception:
            return []


lastfm_service = LastFmService()
