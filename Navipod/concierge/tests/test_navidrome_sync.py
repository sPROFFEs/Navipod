import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

with patch("os.makedirs"), patch("docker.from_env", return_value=MagicMock()):
    from routers.music import favorites


def test_playlist_sync_runs_when_navidrome_favorites_endpoint_is_unavailable(monkeypatch):
    calls = []

    class Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class Client:
        async def get(self, url, **_kwargs):
            calls.append(url)
            if url.endswith("/getStarred"):
                return Response(503, {})
            return Response(200, {"subsonic-response": {"playlists": {"playlist": []}}})

    monkeypatch.setattr(favorites, "http_client", Client())
    monkeypatch.setattr(favorites.manager, "get_or_spawn_container", lambda _username: "navidrome", raising=False)
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []
    user = SimpleNamespace(id=1, username="listener", favorites=[])

    asyncio.run(favorites.sync_navidrome_to_local(db, user))

    assert calls == [
        "http://navidrome:4533/listener/rest/getStarred",
        "http://navidrome:4533/listener/rest/getPlaylists",
    ]
