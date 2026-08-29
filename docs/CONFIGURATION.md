# Configuration

Navipod configuration is split between host-level environment variables and provider settings stored from the application UI.

## Environment file

The default template is:

```text
Navipod/.env.example
```

Create the live file with:

```bash
cd Navipod/Navipod
cp .env.example .env
```

Never commit the resulting `.env`.

## Core environment variables

| Variable | Typical value / default | Purpose |
|---|---|---|
| `SECRET_KEY` | **required** | Authentication signing and encryption of provider secrets. Use a long random value. |
| `ALGORITHM` | `HS256` | Signing algorithm. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | Base access-token lifetime. |
| `REMEMBER_SESSION_DAYS` | `30` | Lifetime for the opt-in persistent session. |
| `DOMAIN` | `domain.com` | Public hostname, or `localhost` for local testing. |
| `ALLOWED_HOSTS` | comma-separated | Trusted FastAPI hostnames. |
| `CORS_ORIGINS` | comma-separated | Exact origins allowed for credentialed cross-origin requests. |
| `CHECK_INTERVAL_MINUTES` | `30` | Reaper task polling frequency. |
| `IDLE_THRESHOLD_MINUTES` | `30` | Idle time before eligible user containers are stopped. |
| `HOST_DATA_ROOT` | `/opt/saas-data` | Persistent host data root. |
| `DATABASE_URL` | empty | Optional explicit SQLAlchemy database URL override. |
| `NAVIDROME_IMAGE` | `deluan/navidrome:latest` | Navidrome image used for user instances. |
| `CONCURRENT_DOWNLOADS` | `3` | Maximum concurrent downloads per user. |
| `POOL_STATUS_CACHE_TTL_SECONDS` | `60` | Shared-pool status cache TTL. |
| `BACKUP_ROOT` | `/saas-data/backups` | Backup path inside the concierge container. |
| `APP_SOURCE_ROOT` | `/workspace` | Application source path inside the concierge container. |
| `COMPOSE_ENV_FILE` | `/saas-data/config/navipod.env` | Compose environment path used by update/setup workflows. |
| `RUNTIME_ENV_FILE` | `/run/navipod/.env` | Runtime environment path used by backup/restore. |
| `BACKUP_SCHEDULER_POLL_SECONDS` | `60` | Admin backup scheduler polling interval. |
| `UPDATE_SOURCE_REPO_URL` | official GitHub repo | Repository checked for application updates. |
| `UPDATE_SOURCE_BRANCH` | `main` | Update source branch. |
| `UPDATE_MANAGED_SERVICES` | `concierge nginx tunnel` | Services recreated by the updater. |
| `COOKIE_SECURE` | `true` | Use secure cookies for HTTPS deployments; `false` only for trusted local HTTP. |
| `TRUST_PROXY_HEADERS` | `false` | Trust reverse-proxy headers only behind a trusted proxy. |
| `TRUSTED_PROXY_IPS` | `127.0.0.1,::1` | Trusted proxy addresses/CIDRs. |
| `PROXY_IMAGE_MAX_BYTES` | `5242880` | Maximum proxied image size. |
| `PROXY_IMAGE_TIMEOUT_SECONDS` | `8` | Proxied image fetch timeout. |
| `PROXY_IMAGE_ALLOWED_CONTENT_TYPES` | image MIME list | Allowed image types for the proxy. |
| `TUNNEL_TOKEN` | optional | Cloudflare Tunnel token. |

Deployment templates can add mode-specific variables such as `ACME_EMAIL`.

## Generating a strong `SECRET_KEY`

Use a high-entropy random value and store it with the rest of your host secrets. For example:

```bash
openssl rand -hex 48
```

> [!CAUTION]
> Provider credentials are encrypted at rest using `SECRET_KEY`. Rotating it after credentials are saved can make those stored values undecryptable unless the application provides a coordinated migration path.

## Provider settings

Open **Settings → Engine** after login.

### YouTube

Upload a Netscape-format `cookies.txt` when needed to reduce CAPTCHA friction or access age-restricted content supported by your own account/session.

Do not commit this cookie file or include it in screenshots.

### Spotify

Create a Spotify developer application and enter its Client ID and Client Secret in Navipod. Spotify is used for discovery, metadata enrichment and download fallback behavior supported by the application.

### Last.fm

Create a Last.fm API application and enter the API key.

### MusicBrainz

No API key is required.

### Metadata priority

A recommended order is:

```text
spotify > lastfm > musicbrainz
```

Choose the order that best matches your library and provider availability.

## Deployment-specific cookie setting

| Deployment | `COOKIE_SECURE` |
|---|---|
| Cloudflare Tunnel / HTTPS | `true` |
| Direct domain / HTTPS | `true` |
| Internal plain HTTP | `false` |

An incorrect value in internal HTTP mode commonly causes a login loop because browsers refuse to send a `Secure` cookie over plain HTTP.

## Restart after changes

Environment changes normally require recreating or restarting the relevant services:

```bash
docker compose up -d
```

For diagnosis:

```bash
docker compose logs -f concierge
```

## Related guides

- [Deployment](DEPLOYMENT.md)
- [Security](SECURITY.md)
- [Troubleshooting](TROUBLESHOOTING.md)
