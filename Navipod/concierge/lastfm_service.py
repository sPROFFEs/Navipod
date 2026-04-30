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


    async def get_similar_artists(self, api_key: str, artist: str, limit: int = 12) -> List[Dict]:
        """artist.getSimilar — used to power 'Fans also like' on the artist
        view and to seed smart-radio when only an artist is known."""
        if not api_key or not artist:
            return []

        params = {
            "method": "artist.getSimilar",
            "artist": artist,
            "api_key": api_key,
            "format": "json",
            "limit": limit,
            "autocorrect": 1,
        }
        try:
            resp = await self.client.get(self.BASE_URL, params=params)
            if resp.status_code != 200:
                return []
            data = resp.json()
            items = data.get("similarartists", {}).get("artist", [])
            if isinstance(items, dict):
                items = [items]

            out = []
            for a in items:
                name = a.get("name") or ""
                if not name:
                    continue
                image_url = ""
                images = a.get("image", [])
                if images:
                    for size_pref in ["extralarge", "large", "medium"]:
                        for img in images:
                            if img.get("size") == size_pref and img.get("#text"):
                                image_url = img["#text"]
                                break
                        if image_url:
                            break
                try:
                    match = float(a.get("match") or 0)
                except Exception:
                    match = 0.0
                out.append({
                    "name": name,
                    "match": match,
                    "image": image_url,
                    "url": a.get("url"),
                })
            return out
        except Exception:
            return []

    async def get_artist_info(self, api_key: str, artist: str) -> Dict:
        """artist.getInfo — bio, listener count, tags."""
        if not api_key or not artist:
            return {}

        params = {
            "method": "artist.getInfo",
            "artist": artist,
            "api_key": api_key,
            "format": "json",
            "autocorrect": 1,
        }
        try:
            resp = await self.client.get(self.BASE_URL, params=params)
            if resp.status_code != 200:
                return {}
            data = resp.json().get("artist", {})
            bio = (data.get("bio") or {}).get("summary", "") or ""
            # Strip the trailing "<a ...>Read more...</a>" tail Last.fm appends.
            if "<a" in bio:
                bio = bio.split("<a")[0].strip()
            tags = (data.get("tags") or {}).get("tag", [])
            if isinstance(tags, dict):
                tags = [tags]
            return {
                "name": data.get("name", artist),
                "listeners": data.get("stats", {}).get("listeners", ""),
                "playcount": data.get("stats", {}).get("playcount", ""),
                "bio": bio,
                "tags": [t.get("name") for t in tags if t.get("name")][:6],
                "url": data.get("url", ""),
            }
        except Exception:
            return {}

    async def get_artist_top_tracks(self, api_key: str, artist: str, limit: int = 10) -> List[Dict]:
        """artist.getTopTracks — most-played tracks per Last.fm. Used as
        smart-radio fallback when track.getSimilar is empty and as the
        'Top tracks' rail on the artist view."""
        if not api_key or not artist:
            return []

        params = {
            "method": "artist.getTopTracks",
            "artist": artist,
            "api_key": api_key,
            "format": "json",
            "limit": limit,
            "autocorrect": 1,
        }
        try:
            resp = await self.client.get(self.BASE_URL, params=params)
            if resp.status_code != 200:
                return []
            data = resp.json()
            tracks = data.get("toptracks", {}).get("track", [])
            if isinstance(tracks, dict):
                tracks = [tracks]
            out = []
            for t in tracks:
                title = t.get("name") or ""
                if not title:
                    continue
                artist_info = t.get("artist", {})
                artist_name = artist_info.get("name") if isinstance(artist_info, dict) else (artist_info or artist)
                out.append({
                    "title": title,
                    "artist": artist_name,
                    "playcount": t.get("playcount", ""),
                    "url": t.get("url", ""),
                })
            return out
        except Exception:
            return []

    async def get_similar_tracks(self, api_key: str, artist: str, track: str, limit: int = 30) -> List[Dict]:
        """track.getSimilar — primary seed for smart-radio. We pick this
        over Spotify recommendations because (1) Last.fm allows 5 req/s
        without auth, (2) the corpus skews toward listening patterns
        rather than playlists, which gives more variety."""
        if not api_key or not artist or not track:
            return []

        params = {
            "method": "track.getSimilar",
            "artist": artist,
            "track": track,
            "api_key": api_key,
            "format": "json",
            "limit": limit,
            "autocorrect": 1,
        }
        try:
            resp = await self.client.get(self.BASE_URL, params=params)
            if resp.status_code != 200:
                return []
            data = resp.json()
            tracks = data.get("similartracks", {}).get("track", [])
            if isinstance(tracks, dict):
                tracks = [tracks]
            out = []
            for t in tracks:
                title = t.get("name") or ""
                if not title:
                    continue
                artist_info = t.get("artist", {})
                artist_name = artist_info.get("name") if isinstance(artist_info, dict) else (artist_info or "")
                try:
                    match = float(t.get("match") or 0)
                except Exception:
                    match = 0.0
                out.append({
                    "title": title,
                    "artist": artist_name,
                    "match": match,
                })
            return out
        except Exception:
            return []


lastfm_service = LastFmService()
