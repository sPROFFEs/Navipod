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

This project is provided strictly for personal, private, non-commercial use. You are not allowed to sell it, sublicense it, offer it as a hosted service, bundle it into a paid product, redistribute copies, or charge third parties for access or operation.

If you need rights beyond personal use, you must obtain explicit prior written permission from the copyright holder. See [LICENSE](LICENSE) for the binding terms.

This repository has two levels:
- repository root: documentation and helper files
- `Navipod/`: the actual application, Docker Compose stack, backend, templates, and assets

## Highlights

- Isolated per-user Navidrome containers managed by a central concierge service
- Unified search across local tracks, YouTube, Spotify, Last.fm, and MusicBrainz
- Shared download pool with deduplication and metadata enrichment
- Local-library personal mixes (Repeat / Deep Cuts / Favorites / Rediscovery) plus remote recommendations
- Admin panel: user management, system monitor, rotating backups, in-app updates
- Subsonic-compatible endpoints for mobile clients (Amperfy, Tempo, Symfonium…)
- Native Android APK that wraps the web app with system media-session integration

## Architecture

Main services in `Navipod/docker-compose.yaml`:
- `concierge` — FastAPI backend and orchestration layer
- `nginx` — reverse proxy on port `80`
- `tunnel` — optional Cloudflare Tunnel connector

Persistent data lives in `/opt/saas-data` (database, cache, per-user music, shared pool, backups).

## Requirements

- Linux host with Docker Engine and the `docker compose` plugin
- Ports `80` and `8000` available
- 2+ CPU cores, 4 GB RAM, SSD-backed storage recommended
- A real domain name + Cloudflare account if you want tunnel-based remote access

## Quick Start

```bash
git clone https://github.com/sPROFFEs/Navipod
cd Navipod/Navipod
cp .env.example .env
nano .env             # set SECRET_KEY, DOMAIN, ALLOWED_HOSTS, COOKIE_SECURE
chmod +x setup.sh && ./setup.sh
```

The setup script checks Docker, creates `/opt/saas-data`, builds the stack, optionally creates the first admin user, and optionally imports an existing music library.

Then open `http://localhost` (or your configured domain) and log in.

If you skipped admin creation, run this from the directory containing `docker-compose.yaml`:

```bash
docker compose exec -T concierge python -c "
import database, auth
db = database.SessionLocal()
hashed = auth.get_password_hash('change_me_now')
new_user = database.User(username='admin', hashed_password=hashed, is_admin=True, is_active=True)
db.add(new_user); db.flush()
db.add(database.DownloadSettings(user_id=new_user.id, audio_quality='320'))
if not db.query(database.SystemSettings).first():
    db.add(database.SystemSettings(pool_limit_gb=100))
db.commit(); db.close()
"
```

Change the username and password before running.

## First-Time Configuration

In `Settings > Engine`:

- **YouTube cookies** — upload a Netscape-format `cookies.txt` to bypass age restrictions and reduce CAPTCHAs.
- **Spotify API** — create an app at [developer.spotify.com](https://developer.spotify.com/dashboard) and paste Client ID + Secret. Used for Spotify search, metadata enrichment, and download fallback.
- **Last.fm API** — create an app at [last.fm/api](https://www.last.fm/api/account/create) and paste the API key.
- **MusicBrainz** — no key required.
- **Metadata priority** — recommended: `spotify > lastfm > musicbrainz`.

Provider credentials are encrypted at rest using `SECRET_KEY`. Don't rotate `SECRET_KEY` after users have saved credentials, or those credentials won't decrypt.

## Android App

Each GitHub [release](https://github.com/sPROFFEs/Navipod/releases) ships two extra artifacts alongside the source code:

- `navipod-android.apk` — installable on any Android 5.0+ device
- `navipod-android-source.zip` — full Android Studio project sources for the wrapper

Install:
1. Download the APK to your phone. Enable "Install unknown apps" for your file manager or browser when Android prompts.
2. Open the APK → tap **Install**.
3. On first launch, enter your server URL (e.g. `https://navipod.yourdomain.com`) → tap **Connect**.
4. Log in with the same credentials as the web app.

Features:
- Native system media notification (album art, title/artist, play / pause / next / previous)
- Background playback that survives screen-off and app switching
- To change the server URL later, tap the gear icon on the login screen

## Mobile Subsonic Clients

Each user's Navidrome instance is also exposed at:

```
https://your-domain/<username>
```

Use the same Navipod credentials. Compatible with Amperfy, Tempo, Symfonium, and any Subsonic client. Enable legacy authentication in the client if it asks.

## Day-to-Day Use

The app shell lives at `/portal`:

- **Home** — recommendations, personal mixes, Wrapped
- **Search** — local + remote provider chips with preview/download
- **Library / Favorites / Radios**
- **Settings** — providers, cookies, metadata priority; admins also see Users / Operations / Updates / Backups / Monitor

Downloads progress as `pending → downloading → importing → completed`. Duplicates are reused via source/hash/fingerprint. Old finished jobs are pruned automatically.

## Updating

**From the UI:** `Admin > System Monitor > Check for Updates → Apply Update`. The in-app updater creates a backup, runs schema migrations, rebuilds containers only when needed, and runs health checks.

**From the CLI:**
```bash
git pull && cd Navipod && docker compose up -d --build
```

## Backup and Restore

The System Monitor manages two rotating slots (`current`, `previous`) — use them from the UI before risky changes.

For host-level backups, archive at least:
- `/opt/saas-data/`
- `Navipod/.env`

```bash
sudo tar -czf navipod-backup.tar.gz /opt/saas-data /path/to/repo/Navipod/.env
```

Restore by extracting back into place before restarting the containers.

## Environment Variables

Main variables in `.env.example`:

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | Yes | Auth token signing + provider secret encryption. Don't rotate after first use. |
| `DOMAIN` | Yes | Public host name, or `localhost` for local use. |
| `ALLOWED_HOSTS` | Yes | Comma-separated FastAPI trusted hosts. |
| `COOKIE_SECURE` | Yes | `true` for HTTPS / Cloudflare Tunnel, `false` only for local HTTP testing. |
| `HOST_DATA_ROOT` | No | Default: `/opt/saas-data`. |
| `CONCURRENT_DOWNLOADS` | No | Max concurrent downloads per user. |
| `IDLE_THRESHOLD_MINUTES` | No | Web inactivity threshold before a user container is reaped. |
| `TUNNEL_TOKEN` | No | Cloudflare Tunnel token. Leave empty to disable. |
| `BACKUP_ROOT` | No | Container-internal backup path. Default: `/saas-data/backups`. |
| `APP_SOURCE_ROOT` | No | Repo path inside the concierge container. Default: `/workspace`. |

## Troubleshooting

**Downloads fail** — verify `cookies.txt` is valid, Spotify credentials are correct, and the host has outbound internet. Check `docker compose logs -f concierge`.

**Age-restricted YouTube fails** — export a fresh Netscape `cookies.txt` from a logged-in browser session and re-upload it.

**Covers missing** — configure Spotify and/or Last.fm in `Settings > Engine`.

**Permission errors writing `/opt/saas-data`** —
```bash
sudo chown -R $USER:$USER /opt/saas-data && sudo chmod -R 775 /opt/saas-data
```

**Backend can't control Docker** — verify `/var/run/docker.sock` is mounted into the `concierge` container.

**Update toast not appearing** — confirm you're logged in as admin and the backend can reach the configured GitHub repository.

## Security

- Change `SECRET_KEY` before production use.
- Don't commit `.env`, `cookies.txt`, or database files.
- The backend mounts the Docker socket — treat the host as privileged infrastructure.

## Repository Layout

```text
Navipod/
├── README.md
├── LICENSE
├── resize_disk.sh
└── Navipod/
    ├── assets/
    ├── concierge/
    ├── docker-compose.yaml
    ├── nginx.conf
    ├── setup.sh
    └── .env.example
```

## License

Custom proprietary personal-use-only license. Personal private use is allowed; commercial use, redistribution, sublicensing, and hosted-service offerings are not. See [LICENSE](LICENSE) for the binding terms.
