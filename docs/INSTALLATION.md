# Installation

This guide covers a first Navipod installation using the default Cloudflare Tunnel deployment. If you want LAN-only access or direct Let's Encrypt TLS, read [Deployment](DEPLOYMENT.md) first.

## Requirements

- Linux host with Docker Engine installed.
- Docker Compose plugin available as `docker compose`.
- 2+ CPU cores and 4 GB RAM recommended.
- SSD-backed storage recommended.
- Sufficient disk space for your shared music pool and per-user data.
- A domain and Cloudflare account when using the default tunnel mode.

Persistent application data is stored under `/opt/saas-data` by default.

## 1. Clone the repository

```bash
git clone https://github.com/sPROFFEs/Navipod
cd Navipod/Navipod
```

The repository root contains documentation and helper files. The actual application stack lives inside the nested `Navipod/` directory.

## 2. Create the environment file

```bash
cp .env.example .env
nano .env
```

For the default Cloudflare Tunnel deployment, set at minimum:

```dotenv
SECRET_KEY=replace_with_a_long_random_secret
DOMAIN=navipod.example.com
TUNNEL_TOKEN=your_cloudflare_tunnel_token
COOKIE_SECURE=true
```

Do not rotate `SECRET_KEY` casually after provider credentials have been saved: Navipod uses it for authentication-related signing and encryption of stored provider secrets.

See [Configuration](CONFIGURATION.md) for the full environment reference.

## 3. Run setup

```bash
chmod +x setup.sh
./setup.sh
```

The setup flow checks Docker, creates the persistent data directory, builds the stack, and can optionally create the first admin user and import an existing music library.

## 4. Open Navipod

Open the URL configured for your deployment and sign in with the admin account created during setup.

If you skipped admin creation during setup, use the admin creation command documented in [Administration](ADMINISTRATION.md).

## 5. Configure discovery providers

After logging in, open **Settings → Engine** and configure the providers you want to use:

- YouTube cookies (`cookies.txt`) when needed for age-restricted content or CAPTCHA reduction.
- Spotify Client ID and Client Secret.
- Last.fm API key.
- MusicBrainz requires no API key.
- Recommended metadata priority: `spotify > lastfm > musicbrainz`.

See [Configuration](CONFIGURATION.md) for details.

## 6. Optional: import an existing library

From the repository root:

```bash
./import_music.sh /path/to/your/music
```

Add `--enrich` to fill missing artwork and metadata with the providers configured in Navipod.

Read [Importing Music](IMPORTING-MUSIC.md) before running this against a large collection because the importer moves source audio files into the shared pool.

## Next steps

- [Deployment](DEPLOYMENT.md)
- [Configuration](CONFIGURATION.md)
- [User Guide](USER-GUIDE.md)
- [Backup & Restore](BACKUP-RESTORE.md)
- [Security](SECURITY.md)
