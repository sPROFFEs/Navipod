# Legacy Playlist Migration Map

## Status

`UserPlaylist` and `PlaylistTrack` are still active in the current product.
They cannot be deleted safely yet.

## Active Dependencies

### HTML views and legacy folder model

- `Navipod/concierge/routers/music/core.py`
  - `/downloads`
  - `/search`
  - auto-scans physical music folders into `UserPlaylist`
  - passes `UserPlaylist` rows into server-rendered templates

### Download job destination model

- `Navipod/concierge/database.py`
  - `DownloadJob.target_playlist_id` still points to `user_playlists.id`

- `Navipod/concierge/routers/music/downloads.py`
  - `/api/downloads/status` resolves `target_playlist_id` against `UserPlaylist`
  - `/api/downloads/start` still stores the selected legacy playlist id

- `Navipod/concierge/downloader_service.py`
  - maps `DownloadJob.target_playlist_id` from `UserPlaylist` into modern `Playlist`
  - still contains `_sync_folder_to_db()` using `PlaylistTrack`

### ORM ownership graph

- `Navipod/concierge/database.py`
  - `User.playlists -> UserPlaylist`
  - `UserPlaylist.tracks -> PlaylistTrack`

## Modern Playlist System

- `Navipod/concierge/database.py`
  - `Playlist`
  - `PlaylistItem`

- `Navipod/concierge/routers/music/playlists.py`
  - main API used by the SPA
  - public playlists
  - copies/sync model
  - item-based playlist operations

- `Navipod/concierge/m3u_service.py`
  - already rewritten to use modern models

## Safe Migration Plan

1. Change `DownloadJob.target_playlist_id` to target modern `playlists.id`.
2. Replace `UserPlaylist` lookups in `downloads.py` with modern playlist summaries.
3. Replace `/downloads` and `/search` server-rendered playlist dropdown data to use `Playlist`.
4. Remove physical-folder auto-discovery from `core.py` or migrate it to a separate import model.
5. Delete `_sync_folder_to_db()` and any remaining `PlaylistTrack` writes after confirming no route depends on folder-backed playlists.
6. Remove ORM relationships and tables only after steps 1-5 are fully shipped and tested.

## Mandatory Regression Checks Before Deleting Legacy Models

- Open `/downloads`
- Open `/search`
- Start a download into an existing playlist
- Start a download creating a new playlist
- Poll `/api/downloads/status`
- Complete a download and verify final playlist placement
- Open the created playlist in the SPA
