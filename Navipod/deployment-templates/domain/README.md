# Domain + Let's Encrypt deployment template

For a public Navipod with your own DNS name and a real Let's Encrypt
certificate served by nginx. The Cloudflare Tunnel is removed; you publish
directly via DNS + ports 80/443.

## Pre-requisites

1. A registered domain.
2. A DNS **A record** pointing `$DOMAIN` at the public IP of this host.
   Verify from outside with `dig +short music.example.com`.
3. **Ports 80 AND 443 open** in your router/firewall, forwarded to the
   host. Port 80 is mandatory for the HTTP-01 ACME challenge.
4. Nothing else on the host bound to ports 80/443.

## How to switch this on

From the directory that contains the live `docker-compose.yaml`:

```bash
# Replace the three files with their domain-mode equivalents.
cp deployment-templates/domain/docker-compose.yaml docker-compose.yaml
cp deployment-templates/domain/nginx.conf nginx.conf
cp deployment-templates/domain/.env.example .env

# Edit .env — at minimum:
#   SECRET_KEY     (generate with `openssl rand -hex 32`)
#   DOMAIN         (the public hostname, must match DNS A record)
#   ALLOWED_HOSTS  (same hostname, or a comma list including aliases)
#   ACME_EMAIL     (real address — Let's Encrypt sends expiry warnings here)
nano .env
```

## First time — initial cert acquisition

The compose runs a renewal loop, but the **first** cert needs a one-shot
issuance command. Pick **one** of these:

### Option A — Standalone (simplest, requires nginx not yet running)

```bash
# Make sure nothing is on port 80 yet
docker compose down 2>/dev/null || true

# Issue cert with certbot listening on :80 directly
docker run --rm \
    -p 80:80 \
    -v navipod_certbot_etc:/etc/letsencrypt \
    -v navipod_certbot_www:/var/www/certbot \
    certbot/certbot certonly --standalone \
        --non-interactive --agree-tos \
        --email "<your-email>" \
        -d "<your-domain>"

# Now bring everything up
docker compose up -d
```

### Option B — Webroot (nginx already up serving HTTP only)

```bash
# Bring nginx up first so :80 is serving the ACME webroot
docker compose up -d nginx

# Issue cert
docker compose run --rm --entrypoint sh certbot \
    -c "certbot certonly --webroot -w /var/www/certbot \
        --non-interactive --agree-tos \
        --email \$ACME_EMAIL -d \$DOMAIN"

# Reload nginx so it serves the new cert
docker compose exec nginx nginx -s reload

# Bring up the rest
docker compose up -d
```

## Automatic renewal

The `certbot` container runs `certbot renew` every 12 hours and writes
new certs to a shared Docker volume.

**Nginx does not auto-reload**, so the new cert is only loaded when nginx
restarts. Two ways to handle that:

### Recommended: nightly nginx reload via host cron

```cron
# crontab -e
0 4 * * *  cd /path/to/Navipod && /usr/bin/docker compose exec nginx nginx -s reload
```

Once a day at 04:00. Lets Encrypt certs renew at 60 days (out of 90 valid),
so a daily reload is plenty of margin.

### Acceptable: manual reload after a renewal

```bash
docker compose exec nginx nginx -s reload
```

You'll need to do this every ~60 days when a renewal happens. Set a
calendar reminder.

## Enable HSTS once renewals are stable

After your **second** successful auto-renewal, edit `nginx.conf` and
uncomment the `Strict-Transport-Security` line. Don't enable HSTS before
that — a temporary cert outage with HSTS active locks users out for the
policy duration.

## Switching back to cloudflared

```bash
git checkout docker-compose.yaml nginx.conf
nano .env                                  # set TUNNEL_TOKEN, remove ACME_EMAIL if desired
docker compose up -d

# Optional: free the Let's Encrypt volumes
docker volume rm navipod_certbot_etc navipod_certbot_www
```

## Troubleshooting

**Certbot fails with "Connection refused" on first issuance**
→ Port 80 is not actually reachable from the internet. Check your router
   port-forward, the host's firewall (`ufw allow 80,443/tcp`), and that
   nothing else (Apache, another nginx) is bound to :80.

**Certbot fails with "Too many requests"**
→ Let's Encrypt rate-limits to 5 cert attempts per registered domain per
   week. Wait, or use the staging server while debugging by adding
   `--server https://acme-staging-v02.api.letsencrypt.org/directory` to
   the certbot command. Staging certs are not trusted by browsers but
   verify the rest of the flow.

**The cert renews but the site keeps serving the old one**
→ nginx hasn't reloaded. Run the reload command above, or set up the
   host cron entry.

**`docker compose up -d` fails with "address already in use"**
→ Something else is on port 80 or 443. `sudo lsof -i :80` and stop it.
