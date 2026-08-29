# Navipod deployment guide

Three supported deployment modes:

| Mode               | Public access | TLS                      | Use when                                          |
| ------------------ | ------------- | ------------------------ | ------------------------------------------------- |
| **cloudflared** *(default)* | Yes           | Cloudflare edge          | Easiest, no firewall changes, no public IP        |
| **internal**       | No            | None (HTTP)              | LAN / VPN testing, dev, fully private homelab     |
| **domain**         | Yes           | Let's Encrypt (your TLS) | Own domain + public IP + you want full control    |

## How modes are switched

The in-app updater and the rest of the project tooling all call
`docker compose up -d` against the **standard filenames** in
`Navipod/`:

- `Navipod/docker-compose.yaml`
- `Navipod/nginx.conf`
- `Navipod/.env`

To keep the updater working without surprises, each deployment mode is
a **standalone replacement** of those files, not a docker-compose
overlay. You copy the template in, the live files match, the updater
keeps doing the same thing.

Templates live at:

```
Navipod/deployment-templates/
├── README.md
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

The cloudflared mode is what ships at the standard filenames, so there's
no `cloudflared/` template — that's already what you have on a fresh
clone.

---

## 1. Cloudflared (default)

```bash
git clone https://github.com/sPROFFEs/Navipod
cd Navipod/Navipod
cp .env.example .env
nano .env             # set SECRET_KEY, TUNNEL_TOKEN, DOMAIN
chmod +x setup.sh && ./setup.sh
```

`setup.sh` checks Docker, creates `/opt/saas-data`, builds the stack,
optionally creates the first admin user, and optionally imports an
existing music library.

The first build also creates the isolated `downloader` image. It includes
Chromium for SpotiFLAC's signed-session verification, so it is larger and may
take longer than a normal Concierge rebuild. Provider state survives image
replacement in the `navipod_downloader_state` volume. Temporary audio is the
only host data mounted into that container, at
`/opt/saas-data/download-staging`.

Then open your tunnel URL and log in.

Login sessions are browser-session cookies by default. Selecting **Remember
me on this device** creates a revocable HttpOnly cookie for
`REMEMBER_SESSION_DAYS` (30 by default); Navipod never stores the password in
the browser. Keep `COOKIE_SECURE=true` for every HTTPS deployment.

---

## 2. Internal HTTP (LAN / VPN)

```bash
cd Navipod/Navipod
cp deployment-templates/internal/docker-compose.yaml docker-compose.yaml
cp deployment-templates/internal/.env.example .env
nano .env             # set SECRET_KEY; COOKIE_SECURE must stay false
docker compose up -d
```

Visit `http://<host-ip>/` from inside your network.

**Critical**: `.env` must have `COOKIE_SECURE=false`. On plain HTTP a
`Secure` cookie is silently rejected by the browser, which causes the
login form to loop indefinitely.

**Security**: never expose this mode to the public internet. Anyone on
the network path can sniff session cookies. Keep it behind a VPN
(Tailscale, WireGuard) or on a LAN you trust.

Full per-template guide: [`deployment-templates/internal/README.md`](../Navipod/deployment-templates/internal/README.md).

---

## 3. Own domain + Let's Encrypt

Real public domain, real TLS, auto-renewal via certbot.

### Pre-requisites

1. A registered domain.
2. DNS **A record** pointing `$DOMAIN` at the host's public IP.
3. **Ports 80 and 443 open** in your router/firewall, forwarded to the
   host. Port 80 is mandatory for the HTTP-01 ACME challenge.
4. Nothing else on the host bound to ports 80/443.

### Switch the files in

```bash
cd Navipod/Navipod
cp deployment-templates/domain/docker-compose.yaml docker-compose.yaml
cp deployment-templates/domain/nginx.conf nginx.conf
cp deployment-templates/domain/.env.example .env
nano .env             # set SECRET_KEY, DOMAIN, ACME_EMAIL, ALLOWED_HOSTS
```

### First time — initial cert acquisition

The compose runs a renewal loop, but the **first** cert needs a one-shot
issuance. Two options, full instructions in the template's
[`README.md`](../Navipod/deployment-templates/domain/README.md):

#### Option A — Standalone (simplest)

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

#### Option B — Webroot (nginx already up)

See the per-template README for the full sequence.

### Automatic renewal

The `certbot` service renews every 12h. Nginx does NOT auto-reload
when the cert is renewed — set up a host cron:

```cron
0 4 * * *  cd /path/to/Navipod && /usr/bin/docker compose exec nginx nginx -s reload
```

Lets Encrypt certs renew at day 60 of 90, so a daily reload is plenty
of margin.

After your second successful auto-renewal, you can enable HSTS in
`nginx.conf` by uncommenting the `Strict-Transport-Security` line.

---

## Migrating between modes

Your `/opt/saas-data` lives outside any compose file (it's a host bind
mount), so user data, library, playlists, federation peers, and DBs all
survive a mode switch. Steps:

1. `docker compose down`
2. Copy the new template files (and `.env.example`) over the live ones.
3. Edit `.env`.
4. `docker compose up -d`

User accounts, settings, federation tokens — all preserved.

To go back to **cloudflared**: `git checkout docker-compose.yaml nginx.conf`
restores the shipping defaults; edit `.env` to set `TUNNEL_TOKEN` and
`COOKIE_SECURE=true`.

---

## Troubleshooting

**Downloader shows unavailable after an upgrade**
  → Run `docker compose up -d --build downloader`, then check
    `docker compose logs --tail=200 downloader`. Navipod defaults to automatic
    mode, so downloads continue through the legacy Concierge engine while the
    worker is unavailable. Use **Admin → System Monitor → Downloader runtime**
    to inspect versions or force either engine.

**SpotiFLAC asks for verification or falls through to spotDL**
  → The current extension providers use a signed browser session. Confirm the
    container can reach `api.zarz.moe`, Chromium starts successfully, and the
    `navipod_downloader_state` volume is persistent. No Tidal, Qobuz, Deezer,
    or Amazon account/API key is required by the pinned extensions.

**Login redirects in a loop in `internal` mode**
  → `COOKIE_SECURE` is still `true`. Set to `false` in `.env`, restart
    concierge.

**The in-app updater overwrote my custom mode after a `git pull`**
  → It shouldn't — the updater calls `docker compose up -d` against
    your live `docker-compose.yaml`, which is whichever template you
    last copied in. If `git pull` introduced merge conflicts on
    `docker-compose.yaml`/`nginx.conf`, resolve them keeping your
    version, or re-copy the template.

**Certbot rate-limit hit while debugging**
  → Use `--server https://acme-staging-v02.api.letsencrypt.org/directory`
    on the certbot commands. Staging certs are untrusted by browsers
    but exercise the full flow without burning the 5-attempts-per-week
    production quota.

**The cert renews but the site keeps serving the old one**
  → nginx hasn't reloaded. Run `docker compose exec nginx nginx -s reload`
    or set up the host cron above.
