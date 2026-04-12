<p align="center">
  <img src="Navipod/assets/icon.png" alt="Navipod" width="180">
</p>

<h1 align="center">Navipod</h1>

<p align="center">
  Personal music platform with isolated Navidrome user containers, a FastAPI control plane,
  multi-source remote search, and a shared download pool.
</p>

<p align="center">
  <strong>Docker-based</strong> · <strong>Multi-user</strong> · <strong>Spotify / YouTube / Last.fm / MusicBrainz</strong> · <strong>Subsonic-compatible</strong>
</p>

## Usage Notice

This project is provided strictly for personal, private, non-commercial use.

You are not allowed to:
- sell it
- sublicense it
- offer it as a hosted service
- bundle it into a paid product or service
- redistribute modified or unmodified copies
- charge third parties for access, deployment, support, customization, or operation of this software as a commercial offering

If you need rights beyond personal use, you must obtain explicit prior written permission from the copyright holder.

See [LICENSE](LICENSE) for the binding license terms.

This repository has two levels:
- repository root: documentation and helper files
- `Navipod/`: the actual application, Docker Compose stack, backend, templates, and assets

## Highlights

- Isolated per-user Navidrome containers managed by a central concierge service
- Unified search across local tracks, YouTube, Spotify, Last.fm, and MusicBrainz
- Shared download pool with deduplication and metadata enrichment
- Recommendation feeds backed by local history and remote providers
- Admin panel with user management, system monitor, backups, in-app update flow, and maintenance tools
- Subsonic-compatible endpoints for mobile clients and remote playback

## Features

- Multi-user music platform with per-user Navidrome containers
- Shared global music pool with deduplication
- Remote search across local library, YouTube, Spotify, Last.fm, and MusicBrainz
- Download queue with YouTube and Spotify-oriented fallback logic
- Download history pruning that keeps active jobs and trims old finished entries automatically
- Per-user local activity tracking in `user_activity.db` with cached personalized mixes
- Recommendations from Spotify, YouTube, Last.fm, MusicBrainz, and local history
- Four local-library personal mixes in Home: `Repeat Mix`, `Deep Cuts Mix`, `Favorites Mix`, and `Rediscovery Mix`
- Cover enrichment with provider fallbacks and persistent cache
- Admin panel for users, system monitor, RAM/cache tools, pool size limits, rotating backups, and update checks
- Subsonic-compatible access for mobile players such as Amperfy, Tempo, Symfonium, and similar apps

## Architecture

Main services in `Navipod/docker-compose.yaml`:
- `concierge`: FastAPI backend and orchestration layer
- `nginx`: reverse proxy on port `80`
- `tunnel`: optional Cloudflare Tunnel connector

Persistent host data is stored in:
- `/opt/saas-data`

Important persistent paths:
- `/opt/saas-data/concierge.db`: main application database
- `/opt/saas-data/cache/`: metadata, cover, recommendations, and token caches
- `/opt/saas-data/users/`: per-user music and Navidrome data
- `/opt/saas-data/users/<username>/cache/user_activity.db`: per-user playback activity store
- `/opt/saas-data/users/<username>/cache/personalized_mixes.json`: cached personal mixes served to Home
- `/opt/saas-data/pool/`: shared music pool

## Requirements

Minimum host requirements:
- Linux host, preferably Ubuntu or Debian
- Docker Engine installed and working
- Docker Compose plugin available as `docker compose`
- Ports `80` and `8000` available
- Write access to `/opt/saas-data`
- Enough disk space for your music pool and cache

Recommended:
- 2 CPU cores or more
- 4 GB RAM or more
- SSD-backed storage
- A real domain name if you want remote access
- A Cloudflare account if you want to use the included tunnel service

## Quick Start

This is the easiest path for a first deployment.

### 1. Clone the repository

```bash
git clone https://github.com/sPROFFEs/Navipod
cd Navipod
cd Navipod
```

You should now be inside the application directory that contains `docker-compose.yaml`.

### 2. Review the environment file

```bash
cp .env.example .env
nano .env
```

At minimum, set these values:

```env
SECRET_KEY=replace_with_a_long_random_secret
DOMAIN=localhost
ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0
COOKIE_SECURE=false
```

Optional but common:

```env
TUNNEL_TOKEN=
CHECK_INTERVAL_MINUTES=30
IDLE_THRESHOLD_MINUTES=30
HOST_DATA_ROOT=/opt/saas-data
NAVIDROME_IMAGE=deluan/navidrome:latest
CONCURRENT_DOWNLOADS=3
BACKUP_ROOT=/saas-data/backups
APP_SOURCE_ROOT=/workspace
```

Important notes:
- `SECRET_KEY` must be unique in production.
- Do not rotate `SECRET_KEY` casually after users have saved encrypted provider credentials, or those credentials will no longer decrypt.
- If you do not use Cloudflare Tunnel, leave `TUNNEL_TOKEN` empty or disable the `tunnel` service.
- Set `COOKIE_SECURE=true` for public HTTPS deployments, including Cloudflare Tunnel.
- Set `COOKIE_SECURE=false` only for local plain HTTP access such as `http://localhost`.
- The admin System Monitor now manages two rotating backup slots: `current` and `previous`.
- `BACKUP_ROOT` is the path seen by the `concierge` container, not the host path. Leave `/saas-data/backups` unless you know exactly why you need a different container-internal mount path.
- `APP_SOURCE_ROOT` is the repository root mounted inside the `concierge` container. It is used for build metadata and the upcoming update manager.

## Admin Utilities

Navipod includes several admin-side utilities that are easy to miss if you only read the deployment steps.

- `Admin > User Management`: create users, reset passwords, delete non-owner users, and inspect library duplicates
- `Admin > Library Management`: search tracks globally and remove broken or redundant entries
- `System Monitor > Updates`: check GitHub `main`, see pending commits, and apply updates from inside the UI
- `System Monitor > Backups`: manage rotating `current` and `previous` backup slots and restore them from the UI
- `System Monitor > Operations`: change the shared pool limit, purge storage residue, and flush Linux page cache
- admin login triggers a silent update refresh so the web UI can surface an update notification without manually opening the monitor

Version semantics:
- `Release version` comes from the `VERSION` file
- `Revision` is the number of commits within the current release line
- changing `VERSION` resets the revision counter for the next release line

### 3. Run the setup script

```bash
chmod +x setup.sh
./setup.sh
```

What the script does:
- checks Docker and Compose availability
- creates `/opt/saas-data`
- creates `/opt/saas-data/cache`, `/opt/saas-data/users`, `/opt/saas-data/pool`, and `/opt/saas-data/backups`
- creates `.env` from `.env.example` if needed
- builds and starts the stack
- optionally creates the first admin user
- optionally imports an existing music library

If you are deploying on a hardened host, read the script first before running it. It changes permissions on `/opt/saas-data` to avoid bind-mount permission issues.

### 4. Open the UI

Open:
- `http://localhost`
- or your configured domain if reverse proxy and DNS are already in place

### 5. Log in with the admin user you created

If you skipped admin creation in the setup script, use the manual command in the next section.

## Manual Deployment

Use this path if you do not want the helper script.

### 1. Create the data directories

```bash
sudo mkdir -p /opt/saas-data/pool
sudo mkdir -p /opt/saas-data/cache
sudo mkdir -p /opt/saas-data/users
sudo chown -R $USER:$USER /opt/saas-data
sudo chmod -R 775 /opt/saas-data
```

### 2. Configure the environment

```bash
cd Navipod
cp .env.example .env
nano .env
```

### 3. Build and start the stack

```bash
docker compose up -d --build
```

### 4. Create the first admin user

Run this from the application directory that contains `docker-compose.yaml`:

```bash
docker compose exec -T concierge python -c "
import database, auth

db = database.SessionLocal()
user = 'admin'
pw = 'change_me_now'
existing = db.query(database.User).filter(database.User.username == user).first()
if existing:
    print(f'User {user} already exists.')
else:
    hashed = auth.get_password_hash(pw)
    new_user = database.User(username=user, hashed_password=hashed, is_admin=True, is_active=True)
    db.add(new_user)
    db.flush()
    db.add(database.DownloadSettings(user_id=new_user.id, audio_quality='320'))
    if not db.query(database.SystemSettings).first():
        db.add(database.SystemSettings(pool_limit_gb=100))
    db.commit()
    print(f'Admin {user} created successfully.')
db.close()
"
```

Change the username and password before using that command.

## First-Time Configuration After Login

Open `Settings > Engine` and configure the providers you want.

### YouTube cookies

Purpose:
- bypass age restrictions
- reduce CAPTCHA / sign-in challenges
- improve reliability for restricted videos

What to do:
- export `cookies.txt` from your browser using a Netscape-format exporter
- upload the file in `Settings > Engine`

Notes:
- the file is stored on disk and used by the downloader
- expired or invalid cookies will break restricted downloads again
- direct YouTube downloads usually work without cookies, but restricted or age-gated content often does not

### Spotify API

Purpose:
- Spotify search tab
- metadata enrichment
- download fallback resolution for Spotify, Last.fm, and MusicBrainz results
- cover fallback and recommendation improvements

What to do:
- create an app at `https://developer.spotify.com/dashboard`
- copy `Client ID` and `Client Secret`
- paste both in `Settings > Engine`

Notes:
- credentials are encrypted at rest in the application database
- a dummy redirect URI such as `http://localhost/callback` is enough for this use case

### Last.fm API

Purpose:
- Last.fm search tab
- Last.fm recommendation sections
- metadata enrichment and cover fallbacks

What to do:
- create an API application at `https://www.last.fm/api/account/create`
- copy the `API Key`
- optionally copy the `Shared Secret`
- save them in `Settings > Engine`

Notes:
- the shared secret is optional for Navipod's current common flows
- the API key is the important field for search and enrichment

### MusicBrainz

Purpose:
- MusicBrainz search tab
- metadata cross-matching
- recommendation enrichment
- fallback provider for cover and track resolution

What to do:
- nothing in user settings

Notes:
- MusicBrainz does not require a personal API key for the flows currently used here
- MusicBrainz becomes more useful when combined with Spotify and Last.fm in metadata priority

### Metadata provider priority

In `Settings > Engine`, choose the metadata priority order.

This affects:
- cover fallback resolution
- metadata enrichment
- cross-provider matching for some remote results

Recommended order:
1. `spotify`
2. `lastfm`
3. `musicbrainz`

Use a different order only if you have a specific reason.

## Mobile App Connection

Navipod exposes each user's Navidrome instance through a Subsonic-compatible path.

Server URL format:

```text
https://your-domain/<username>
```

Example:

```text
https://music.example.com/alice
```

Use the same username and password you use in Navipod.

Compatible apps include:
- Amperfy
- Tempo
- Symfonium
- Navidrome/Subsonic clients in general

If your mobile app asks for it, enable legacy authentication.

## Daily Operations

### Start or rebuild

```bash
cd Navipod
docker compose up -d --build
```

### Stop the stack

```bash
cd Navipod
docker compose down
```

### View logs

```bash
cd Navipod
docker compose logs -f concierge
docker compose logs -f nginx
docker compose logs -f tunnel
```

### Restart only the backend

```bash
cd Navipod
docker compose restart concierge
```

### Update to a newer version

```bash
cd /path/to/repo
git pull
cd Navipod
docker compose up -d --build
```

You can also update from the web UI:
- open `Admin > System Monitor`
- run `Check for Updates`
- use `Apply Update`

The in-app updater:
- creates a backup before applying changes
- runs schema migrations
- rebuilds containers only when the update actually touches rebuild-triggering paths
- recreates services, runs health checks, and prunes Docker cache afterward

## Personal Mixes

Navipod can generate local-library personal mixes without relying on remote playback providers.

- `Repeat Mix`: your most replayed tracks, weighted by favorites and recent full listens
- `Deep Cuts Mix`: tracks with good history that are not the obvious top repeats
- `Favorites Mix`: liked songs plus nearby tracks from the same local artists and albums
- `Rediscovery Mix`: local tracks you used to like but have not played recently

Implementation notes:
- mix learning is currently recorded only from playback inside the Navipod web UI
- playback through Navidrome itself or external Subsonic clients such as Tempo, Symfonium, or Amperfy does not yet feed this activity store
- mixes are cached per user for 12 hours in `personalized_mixes.json`
- the underlying activity data lives in `user_activity.db`
- each mix can be saved as a normal personal playlist from the UI

## Backup and Restore

Back up at least:
- `/opt/saas-data/concierge.db`
- `/opt/saas-data/cache/`
- `/opt/saas-data/users/`
- `/opt/saas-data/pool/`
- `Navipod/.env`

Simple backup example:

```bash
sudo tar -czf navipod-backup.tar.gz /opt/saas-data /path/to/repo/Navipod/.env
```

Restore by extracting the files back into place before restarting the containers.

## Important Environment Variables

Main variables from `.env.example`:

| Variable | Required | Description |
| --- | --- | --- |
| `SECRET_KEY` | Yes | Main application secret used for auth tokens and secret encryption. |
| `ALGORITHM` | No | JWT signing algorithm. Default: `HS256`. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | Login token lifetime in minutes. |
| `DOMAIN` | Yes | Public host name or `localhost` for local use. |
| `ALLOWED_HOSTS` | Yes | Allowed hosts for FastAPI CORS and trusted host middleware. |
| `CHECK_INTERVAL_MINUTES` | No | How often the idle container reaper runs. |
| `IDLE_THRESHOLD_MINUTES` | No | Web inactivity threshold before a user container can be stopped. |
| `HOST_DATA_ROOT` | No | Host path that persists Navipod data. Default: `/opt/saas-data`. |
| `NAVIDROME_IMAGE` | No | Navidrome image used for per-user containers. |
| `CONCURRENT_DOWNLOADS` | No | Maximum concurrent downloads per user. |
| `COOKIE_SECURE` | No | Set to `true` for HTTPS/public deployments. Set to `false` only for local plain HTTP testing. |
| `TUNNEL_TOKEN` | No | Cloudflare Tunnel token. Leave empty if you do not use the tunnel service. |

## Troubleshooting

### The UI starts but downloads fail

Check:
- `cookies.txt` is valid and recently exported
- Spotify credentials are correct if you rely on Spotify fallback matching
- the host can reach YouTube, Spotify, Last.fm, and MusicBrainz
- `ffmpeg`, `deno`, and `yt-dlp` were installed in the backend image correctly

Logs:

```bash
docker compose logs -f concierge
```

### Age-restricted YouTube videos fail

This usually means one of these:
- `cookies.txt` is missing
- `cookies.txt` is expired
- the browser account used to export cookies cannot access age-restricted content

Fix:
- export a fresh Netscape-format `cookies.txt`
- upload it again in `Settings > Engine`

### Covers do not appear

Check:
- Spotify API is configured if you want strong cover fallback behavior
- Last.fm API key is configured if you want Last.fm enrichment
- the backend can write to `/opt/saas-data/cache/covers`

### The stack cannot control Docker

The backend needs access to the Docker socket.

Check:
- `/var/run/docker.sock` is mounted into the `concierge` container
- the backend container started successfully
- `docker compose logs -f concierge` does not show docker permission errors

### Updates do not appear in the admin toast

Check:
- you are logged in as an admin user
- the backend can reach the configured GitHub repository
- `System Monitor > Updates` shows a recent `Last checked` timestamp
- `docker compose logs -f concierge` does not show update-check failures

Notes:
- admin login triggers a background refresh of update state
- the toast polls briefly after load to catch that background refresh
- if the repository is unreachable, no toast will appear because there is no fresh remote state to show

### Permission problems in `/opt/saas-data`

If you manually created the directories with restrictive ownership, the container may fail to write cache, database, or user files.

Fix:

```bash
sudo chown -R $USER:$USER /opt/saas-data
sudo chmod -R 775 /opt/saas-data
```

If your environment still fights permissions hard, use the helper script once and then tighten policies later.

### Cloudflare Tunnel is not needed

If you are deploying only on LAN or behind your own reverse proxy, disable the `tunnel` service in `docker-compose.yaml` or leave it stopped.

## Security Notes

- Change `SECRET_KEY` before production use.
- Do not commit `.env`, `cookies.txt`, or database files.
- Provider secrets are encrypted at rest, but they still depend on the safety of your `SECRET_KEY` and host.
- The backend mounts the Docker socket and can manage containers. Treat the host as privileged infrastructure.

## Repository Layout

```text
Navipod/
+-- README.md
+-- .gitignore
+-- resize_disk.sh
+-- Navipod/
    +-- assets/
    +-- concierge/
    +-- docker-compose.yaml
    +-- nginx.conf
    +-- setup.sh
    +-- .env.example
```

## License

This repository is released under a custom proprietary personal-use-only license.

In short:
- personal private use is allowed
- commercial use is not allowed
- redistribution is not allowed
- sublicensing is not allowed
- offering it to third parties as a paid or hosted service is not allowed

The full binding terms are in [LICENSE](C:\Users\user\Documents\Navipod\LICENSE).
