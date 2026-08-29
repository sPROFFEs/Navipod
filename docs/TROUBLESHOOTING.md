# Troubleshooting

Start with service state and concierge logs:

```bash
cd Navipod/Navipod
docker compose ps
docker compose logs -f concierge
```

## Downloads fail

Check:

- the host has outbound internet access;
- YouTube cookies are current if the content/session requires them;
- Spotify credentials are valid when Spotify-dependent discovery/enrichment is involved;
- the concierge logs contain no provider, disk or permission error.

## Age-restricted YouTube content fails

Export a fresh Netscape-format `cookies.txt` from a logged-in browser session and upload it again in **Settings → Engine**.

Treat this file as a credential and never commit it.

## Covers are missing

Configure Spotify and/or Last.fm in **Settings → Engine** and check provider connectivity. Metadata priority can also affect which provider is used first.

## Permission errors under the data root

For the default data root, the project documents:

```bash
sudo chown -R 1000:1000 /opt/saas-data
sudo find /opt/saas-data -type d -exec chmod 750 {} +
sudo find /opt/saas-data -type f -exec chmod 640 {} +
```

If you use a custom `HOST_DATA_ROOT`, substitute that path.

## Backend cannot control Docker

Verify that the Docker socket is mounted into the concierge container as expected by the active Compose file:

```text
/var/run/docker.sock
```

Also verify Docker itself is healthy on the host.

## Internal deployment login loops

Plain HTTP requires:

```dotenv
COOKIE_SECURE=false
```

A browser will reject a `Secure` session cookie over HTTP, producing an apparent login loop.

## Update notification does not appear

Check that:

- you are logged in as an admin;
- the backend can reach the configured update repository;
- `UPDATE_SOURCE_REPO_URL` / `UPDATE_SOURCE_BRANCH` are correct for your deployment;
- concierge logs do not show outbound network errors.

## TLS certificate renewed but old certificate is served

Reload nginx:

```bash
docker compose exec nginx nginx -s reload
```

For direct-domain deployments, set up the daily reload described in [Deployment](DEPLOYMENT.md).

## Certbot rate limit during setup testing

Use the Let's Encrypt staging endpoint while debugging repeated issuance attempts, then switch back to production once the flow is correct.

## Large import behaves unexpectedly

Before retrying:

```bash
./import_music.sh --help
```

Use `--dry-run` and a small test folder. Remember that the importer moves source audio into the shared pool.

## Still stuck?

Collect these before opening an issue:

```bash
docker compose ps
docker compose logs --tail=200 concierge
docker compose config
```

Remove secrets, private domain information, tokens, cookies and personal library data before sharing logs publicly.
