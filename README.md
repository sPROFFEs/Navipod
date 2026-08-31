<p align="center">
  <img src="Navipod/assets/icon.png" alt="Navipod" width="180">
</p>

# Navipod

**Your self-hosted music universe.**  
One place for your library, discovery, shared listening and personal Navidrome instances.

![Quality](https://github.com/sPROFFEs/Navipod/actions/workflows/quality.yml/badge.svg)![Version](https://img.shields.io/badge/version-1.1.0-1ed760)![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)![License](https://img.shields.io/badge/license-personal%20use%20only-555555)

[**Get started**](#quick-start) · [Documentation](#documentation) · [Deployment](docs/DEPLOYMENT.md) · [Android APK](https://github.com/sPROFFEs/Navipod/releases)

<img width="3400" height="2048" alt="image" src="https://github.com/user-attachments/assets/6a96a9a3-881b-4881-af96-324baa46afc1" />

Navipod is a personal, self-hosted music platform built around isolated **Navidrome instances per user**, a central **FastAPI concierge**, an isolated **downloader worker**, and a shared music pool. It brings your local library together with discovery from **YouTube, Spotify, Last.fm and MusicBrainz**, while keeping the server under your control.

> \[!IMPORTANT\]  
> Navipod is licensed for **private, personal, non-commercial use only**. It is not an open-source license. See [LICENSE](LICENSE) for the binding terms.

## Why Navipod?

<div class="joplin-table-wrapper"><table style="min-width: 50px"><tbody><tr><td colspan="1" rowspan="1"><h3 data-id="nglscjldgfgs" id="nglscjldgfgs">🎧 Search beyond your library</h3><p data-id="uwgwmprvgphw">Search local music and remote providers from one interface, preview results, download tracks and enrich metadata without jumping between services.</p></td><td colspan="1" rowspan="1"><h3 data-id="csqfuhvbbphn" id="csqfuhvbbphn">👤 Isolated multi-user streaming</h3><p data-id="mznrunftvyag">Each user gets an isolated Navidrome container while Navipod handles orchestration, shared storage and the surrounding experience.</p></td></tr><tr><td colspan="1" rowspan="1"><h3 data-id="qnyylvhddoah" id="qnyylvhddoah">🎉 Party Rooms</h3><p data-id="wrzmsxaiwdwy">Create synchronized listening rooms with a shared queue, host controls, playlist seeding and optional guest additions from the local library.</p></td><td colspan="1" rowspan="1"><h3 data-id="pnhkgcsyxxjv" id="pnhkgcsyxxjv">🧠 Personal mixes</h3><p data-id="uobqoavdeegy">Repeat, Deep Cuts, Favorites and Rediscovery mixes sit alongside recommendations and smart playlists that react to your library.</p></td></tr><tr><td colspan="1" rowspan="1"><h3 data-id="jmqytkxyvxio" id="jmqytkxyvxio">📱 Web, Android &amp; Subsonic</h3><p data-id="mecveitxkuhc">Use the web app, the native Android wrapper, or connect compatible Subsonic clients such as Amperfy, Tempo and Symfonium.</p></td><td colspan="1" rowspan="1"><h3 data-id="uqervmhthzaz" id="uqervmhthzaz">🛠️ Built-in operations</h3><p data-id="bqfthbdtrofh">Admin tools cover users, library health, metadata rescans, rotating backups, monitoring and in-app updates.</p></td></tr></tbody></table></div>

## See it in action

<table>
  <tr>
    <td width="50%" align="center">
      <img
        src="https://github.com/user-attachments/assets/ed3afb15-256a-46a1-a2b9-04fe12ceeb9a"
        alt="Navipod Public playlists"
        width="100%"
      />
      <br />
      <strong>Public</strong>
      <br />
      <sub>Share playlists with other users on the server.</sub>
    </td>
    <td width="50%" align="center">
      <img
        src="https://github.com/user-attachments/assets/a959fca3-2fda-4284-9e5d-dbff12346550"
        alt="Navipod Search"
        width="100%"
      />
      <br />
      <strong>Search</strong>
      <br />
      <sub>Local and remote discovery in one place.</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img
        src="https://github.com/user-attachments/assets/a8cd6892-bafe-4e4d-8323-8a914fc6a0fb"
        alt="Navipod Party Room"
        width="100%"
      />
      <br />
      <strong>Party</strong>
      <br />
      <sub>Synchronized playback and a shared queue.</sub>
    </td>
    <td width="50%" align="center">
      <img
        src="https://github.com/user-attachments/assets/a868bc54-0ba3-4781-bcfb-db9e1f8c335b"
        alt="Navipod Mobile"
        width="220"
      />
      <br />
      <strong>Mobile</strong>
      <br />
      <sub>Android wrapper with system media controls.</sub>
    </td>
  </tr>
</table>


## Highlights

- **Multi-user by design** — isolated Navidrome containers managed by the concierge service.
- **Unified discovery** — local tracks plus YouTube, Spotify, Last.fm and MusicBrainz.
- **Shared download pool** — deduplication, metadata enrichment and reusable downloads.
- **Library views** — searchable and paginated artists, albums and genres.
- **Smart playlists** — editable rules, previews and automatic refreshes.
- **Everyday player tools** — persistent queues, lyrics, sleep timer, normalization and track fades.
- **Playlist sharing** — public playlists, synchronized read-only copies, custom covers and track ordering.
- **Party Rooms** — synchronized playback, room queues and host/guest controls.
- **Subsonic compatibility** — connect established mobile clients to each user's Navidrome instance.
- **Self-hosting operations** — updates, backups, monitoring and library maintenance from the admin UI.

## How it works

```mermaid
flowchart LR
    C[Browser / Android / Subsonic client] --> N[nginx]
    N --> F[FastAPI Concierge]
    F --> W[Isolated downloader worker]
    F --> U1[Navidrome · User A]
    F --> U2[Navidrome · User B]
    F --> UX[Navidrome · User N]
    F --> P[(Shared music pool)]
    F --> D[(Navipod data / DB / backups)]
    F --> R[Spotify / YouTube / Last.fm / MusicBrainz]
    W --> R
```

The standard Docker stack contains the **concierge**, **isolated downloader**, **nginx**, per-user **Navidrome** containers, and an optional **Cloudflare Tunnel** connector. Persistent data is stored under `/opt/saas-data` by default. See [Architecture](docs/ARCHITECTURE.md) for the full picture.

## Quick Start

### Requirements

- Linux host
- Docker Engine + `docker compose`
- 2+ CPU cores and 4 GB RAM recommended
- SSD-backed storage recommended
- A domain + Cloudflare account only if using the default tunnel deployment

### Default deployment: Cloudflare Tunnel

```bash
git clone https://github.com/sPROFFEs/Navipod
cd Navipod/Navipod
cp .env.example .env
nano .env   # set SECRET_KEY, TUNNEL_TOKEN and DOMAIN
chmod +x setup.sh && ./setup.sh
```

`setup.sh` checks Docker, prepares `/opt/saas-data`, builds the stack and can create the first admin user and import an existing library.

For LAN/VPN and direct-domain deployments, use the dedicated guide instead of adapting the default stack by hand.

## Deployment modes

| Mode | Best for | TLS | Public IP required |
| --- | --- | --- | --- |
| **Cloudflared** _(default)_ | Easiest remote access | Cloudflare edge | No  |
| **Internal** | LAN / VPN / development | None — HTTP only | No  |
| **Domain** | Full control with your own public endpoint | Let's Encrypt | Yes |

→ Choose and configure a deployment mode

## Documentation

Everything beyond the quick start lives in the documentation.

| Guide                                      | Description                                                               |
| ------------------------------------------ | ------------------------------------------------------------------------- |
| [Installation](docs/INSTALLATION.md)       | Install Navipod and prepare the host environment.                         |
| [Deployment](docs/DEPLOYMENT.md)           | Deploy with Cloudflare Tunnel, LAN access, or your own domain.            |
| [Configuration](docs/CONFIGURATION.md)     | Configure Navipod, providers, environment variables, and runtime options. |
| [User Guide](docs/USER-GUIDE.md)           | Learn the main workflows and everyday features.                           |
| [Party Rooms](docs/PARTY-ROOMS.md)         | Create shared rooms with synchronized playback and queues.                |
| [Importing Music](docs/IMPORTING-MUSIC.md) | Import and organize an existing music library.                            |
| [Android](docs/ANDROID.md)                 | Set up and use the Navipod Android application.                           |
| [Subsonic](docs/SUBSONIC.md)               | Connect compatible Subsonic clients to Navipod.                           |
| [Administration](docs/ADMINISTRATION.md)   | Manage users, libraries, updates, and maintenance tasks.                  |
| [Backup & Restore](docs/BACKUP-RESTORE.md) | Back up Navipod data and restore an installation.                         |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Diagnose common installation, playback, and connectivity issues.          |
| [Architecture](docs/ARCHITECTURE.md)       | Understand Navipod's services, containers, storage, and data flow.        |
| [Security](docs/SECURITY.md)               | Security considerations and recommended deployment practices.             |

> Looking for the full documentation index? See **[docs/README.md](docs/README.md)**.

## Tech stack

**Backend:** Python · FastAPI  
**Music server:** Navidrome  
**Runtime:** Docker · Docker Compose · nginx  
**Discovery:** Spotify · YouTube · Last.fm · MusicBrainz  
**Remote access:** Cloudflare Tunnel or direct TLS deployment  
**Clients:** Web · Android · Subsonic-compatible apps

## Repository layout

```text
Navipod/
├── README.md
├── LICENSE
├── docs/
├── .github/
│   └── assets/
└── Navipod/
    ├── assets/
    ├── concierge/
    ├── deployment-templates/
    ├── docker-compose.yaml
    ├── nginx.conf
    ├── setup.sh
    └── .env.example
```

## License

Navipod uses a custom **Personal Use Only** license. Private, personal, non-commercial use and modification are allowed. Commercial use, redistribution, sublicensing and offering Navipod as a hosted service are prohibited without prior written permission.

Read the complete [LICENSE](LICENSE) before deploying or modifying the project.
