# Internal HTTP deployment template

For LAN-only or VPN-only Navipod (Tailscale, WireGuard, home network behind a
router). No DNS, no certificates, plain HTTP.

## When to use it

- You don't want to expose Navipod to the public internet.
- You can already reach the host from your phone / laptop over your private
  network.
- You don't mind logging in over plain HTTP (because the network is trusted).

**Do not use this mode for a public deployment.** Cookies travel in the clear
and any device on the path between client and server can grab them.

## How to switch this on

From the repository root, in the directory that contains the live
`docker-compose.yaml`:

```bash
cp deployment-templates/internal/docker-compose.yaml docker-compose.yaml
cp deployment-templates/internal/.env.example .env

# Edit .env — at minimum:
#   SECRET_KEY            (generate with `openssl rand -hex 32`)
#   DOMAIN                (your LAN IP or hostname)
#   ALLOWED_HOSTS         (* or comma-separated list of internal addresses)
nano .env

docker compose up -d
```

Then visit `http://<host-ip>/` from a machine on your private network.

## What's different from the cloudflared default

- The `tunnel` service is removed.
- `nginx.conf` and the rest of the layout are untouched, so nothing else
  changes — including the in-app updater, which still calls
  `docker compose up -d` against the same `docker-compose.yaml` path.

## Switching back to cloudflared

```bash
git checkout docker-compose.yaml          # restore default
# (or `cp deployment-templates/cloudflared/docker-compose.yaml ...` if we add one)
nano .env                                  # set TUNNEL_TOKEN, COOKIE_SECURE=true
docker compose up -d
```
