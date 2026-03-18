import httpx
from typing import Dict, List


class MusicBrainzService:
    BASE_URL = "https://musicbrainz.org/ws/2"

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=10.0,
            headers={"User-Agent": "Navipod/1.0 (metadata resolver)"},
        )

    async def search_recordings(self, query: str, limit: int = 10) -> List[Dict]:
        if not query:
            return []

        params = {
            "query": query,
            "fmt": "json",
            "limit": limit,
        }
        try:
            resp = await self.client.get(f"{self.BASE_URL}/recording", params=params)
            if resp.status_code != 200:
                return []

            data = resp.json()
            out = []
            for rec in data.get("recordings", []):
                artist = "Unknown"
                artist_credits = rec.get("artist-credit") or []
                if artist_credits:
                    artist = artist_credits[0].get("name") or "Unknown"

                release_title = ""
                release_year = ""
                cover_url = ""
                releases = rec.get("releases") or []
                if releases:
                    release_title = releases[0].get("title") or ""
                    release_date = releases[0].get("date") or ""
                    release_year = release_date.split("-")[0] if release_date else ""
                    # Cover Art Archive: use release MBID for thumbnail
                    release_id = releases[0].get("id")
                    if release_id:
                        cover_url = f"https://coverartarchive.org/release/{release_id}/front-250"

                out.append(
                    {
                        "id": rec.get("id"),
                        "name": rec.get("title") or "Unknown",
                        "artist": artist,
                        "album": release_title,
                        "year": release_year,
                        "image": cover_url,
                        "url": f"https://musicbrainz.org/recording/{rec.get('id')}",
                        "source": "musicbrainz",
                    }
                )
            return out
        except Exception:
            return []


musicbrainz_service = MusicBrainzService()
