# Deployment templates

Drop-in replacements for the live `docker-compose.yaml`, `nginx.conf`, and
`.env` in the parent directory. Pick the template that matches how you want
to expose Navipod, copy the files in, edit `.env`, and bring it up.

| Mode | Use when | Files to copy |
| ---- | -------- | ------------- |
| `cloudflared` *(default — already in place)* | Easiest. No public IP, no certs. Cloudflare Tunnel does TLS at the edge. | (no copy needed — this is what ships in the repo) |
| [`internal`](internal/) | LAN / VPN testing. Plain HTTP. No DNS. | `docker-compose.yaml`, `.env.example` |
| [`domain`](domain/) | Own public domain + Let's Encrypt cert. | `docker-compose.yaml`, `nginx.conf`, `.env.example` |

## Why this layout (instead of `-f` overlays)?

The in-app updater calls `docker compose up -d` against the standard file
paths (`./docker-compose.yaml`, `./nginx.conf`). If we shipped the
alternative modes as overlays you'd activate with `-f`, the updater would
miss them on every redeploy and silently revert your setup to cloudflared.

By making each mode a **standalone replacement** of the same filenames,
once you've copied the template in, every other tool in the project —
auto-updater, backup, setup script — keeps working without changes.

## Switching modes

Just copy a different template over the live files and edit `.env`:

```bash
# Example: switch from cloudflared to domain mode
cp deployment-templates/domain/docker-compose.yaml docker-compose.yaml
cp deployment-templates/domain/nginx.conf nginx.conf
cp deployment-templates/domain/.env.example .env   # then edit
docker compose up -d
```

## Switching back to cloudflared (the default in this repo)

The shipping `docker-compose.yaml` and `nginx.conf` ARE the cloudflared
mode — `git checkout` restores them:

```bash
git checkout docker-compose.yaml nginx.conf
nano .env   # set TUNNEL_TOKEN, COOKIE_SECURE=true
docker compose up -d
```

Each subdirectory has its own `README.md` with full pre-requisites,
first-time setup, and troubleshooting.
