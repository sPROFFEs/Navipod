# Deployment

Navipod supports three deployment modes. The default repository files are configured for Cloudflare Tunnel.

| Mode | Public access | TLS | Use when |
|---|---:|---|---|
| **cloudflared** *(default)* | Yes | Cloudflare edge | You want the simplest remote deployment without exposing a public IP |
| **internal** | No | None / HTTP | LAN, VPN, development or a trusted private homelab |
| **domain** | Yes | Let's Encrypt | You own the public endpoint and want direct TLS termination |

## How deployment modes are switched

Project tooling and the in-app updater expect the standard live filenames inside `Navipod/`:

```text
Navipod/docker-compose.yaml
Navipod/nginx.conf
Navipod/.env
```

The alternative deployment modes therefore replace those files instead of using Compose overlays. Templates live in:

```text
Navipod/deployment-templates/
├── internal/
│   ├── README.md
│   ├── docker-compose.yaml
│   └── .env.example
└── domain/
    ├── README.md
    ├── docker-compose.yaml
    ├── nginx.conf
    └── .env.example
```

The Cloudflare mode is already present at the standard filenames in a fresh clone.

---

## 1. Cloudflare Tunnel — default

```bash
git clone https://github.com/sPROFFEs/Navipod
cd Navipod/Navipod
cp .env.example .env
nano .env   # set SECRET_KEY, TUNNEL_TOKEN and DOMAIN
chmod +x setup.sh && ./setup.sh
```

Cloudflare terminates TLS at the edge. Keep:

```dotenv
COOKIE_SECURE=true
```

A Cloudflare Tunnel deployment does not require a public IP or inbound port forwarding.

### Sessions

Browser sessions are session cookies by default. Selecting **Remember me** creates a revocable HttpOnly cookie for `REMEMBER_SESSION_DAYS` (30 days by default). Passwords are not stored in the browser.

---

## 2. Internal HTTP — LAN / VPN

Use this only on a trusted LAN or behind a VPN such as WireGuard or Tailscale.

```bash
cd Navipod/Navipod
cp deployment-templates/internal/docker-compose.yaml docker-compose.yaml
cp deployment-templates/internal/.env.example .env
nano .env

docker compose up -d
```

The critical setting is:

```dotenv
COOKIE_SECURE=false
```

Then open:

```text
http://<host-ip>/
```

> [!WARNING]
> Never expose the internal HTTP mode directly to the public internet. There is no TLS, so traffic and session cookies can be observed by anyone on the network path.

If login loops back to the login page, verify `COOKIE_SECURE=false` and restart the concierge service.

---

## 3. Own domain + Let's Encrypt

Use this mode when you have:

1. A registered domain.
2. A DNS A record pointing the Navipod hostname to the host's public IP.
3. Ports **80** and **443** forwarded to the host.
4. No conflicting service already bound to those ports.

### Copy the domain template

```bash
cd Navipod/Navipod
cp deployment-templates/domain/docker-compose.yaml docker-compose.yaml
cp deployment-templates/domain/nginx.conf nginx.conf
cp deployment-templates/domain/.env.example .env
nano .env
```

Set the required values, including:

```dotenv
SECRET_KEY=...
DOMAIN=navipod.example.com
ACME_EMAIL=you@example.com
ALLOWED_HOSTS=...
COOKIE_SECURE=true
```

### Initial certificate acquisition

The renewal service handles later renewals, but the first certificate must be issued once.

A straightforward standalone flow is:

```bash
docker compose down 2>/dev/null || true

docker run --rm \
  -p 80:80 \
  -v navipod_certbot_etc:/etc/letsencrypt \
  -v navipod_certbot_www:/var/www/certbot \
  certbot/certbot certonly --standalone \
    --non-interactive --agree-tos \
    --email "<your-email>" \
    -d "<your-domain>"

docker compose up -d
```

The template also documents a webroot option when nginx is already running.

### Certificate renewal

The Certbot service checks for renewal every 12 hours. Nginx still needs to reload to start serving a renewed certificate. A host cron entry can do that once a day:

```cron
0 4 * * * cd /path/to/Navipod && /usr/bin/docker compose exec nginx nginx -s reload
```

After you have confirmed successful renewals, you can consider enabling HSTS in the domain nginx configuration.

---

## Migrating between modes

User data is stored outside the Compose file under `/opt/saas-data`, so switching deployment mode does not inherently delete your library, users, playlists or database.

Recommended sequence:

```bash
docker compose down
# copy the new template files over the live files
# edit .env for the new mode
docker compose up -d
```

To return to the shipping Cloudflare files, restore the tracked `docker-compose.yaml` and `nginx.conf`, then configure `TUNNEL_TOKEN` and `COOKIE_SECURE=true` again.

## Deployment troubleshooting

### Internal mode login loop

`COOKIE_SECURE` is probably still `true`. Set it to `false` for plain HTTP and restart the stack.

### Certbot rate limit while testing

Use Let's Encrypt staging while debugging issuance so repeated tests do not consume the production attempt quota.

### Certificate renewed but browser still sees the old certificate

Reload nginx:

```bash
docker compose exec nginx nginx -s reload
```

### Custom deployment files after updates

The updater operates against the live standard filenames. If `git pull` creates conflicts in `docker-compose.yaml` or `nginx.conf`, resolve them while keeping the intended deployment mode, or copy the chosen template over the live files again.

## Related guides

- [Installation](INSTALLATION.md)
- [Configuration](CONFIGURATION.md)
- [Security](SECURITY.md)
- [Troubleshooting](TROUBLESHOOTING.md)
