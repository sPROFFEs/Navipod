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
- the Download Manager shows the expected **Automatic**, **Isolated worker only** or **Legacy Concierge only** policy;
- any required lossless-provider session is connected;
- the concierge and downloader logs contain no provider, disk or permission error.

Inspect both services while retrying one job:

```bash
docker compose logs -f concierge downloader
```

The global Downloads panel includes the selected engine, fallback and error details for a failed job. Use those details before changing provider policy.

## Downloader worker is unavailable

Check its state and startup error:

```bash
docker compose ps downloader
docker compose logs --tail=200 downloader
```

After pulling a release that changes the worker image, rebuild both sides of the integration:

```bash
docker compose up -d --build downloader concierge
```

Permission errors for `/downloads`, `.worker-token` or `.auth-browser` mean the mounted data directory is not writable by the worker's configured user. Fix the host-directory ownership for your deployment and recreate the downloader container; do not make the directory world-writable.

## Provider verification fails

Open **Admin → Download Manager** and start the provider's embedded verification browser. Complete the challenge there, then select **Check verification** in the Navipod modal. The embedded browser may close or show a black screen after the challenge succeeds; this is expected while the grant returns to the worker.

If the challenge reports a network change, verification failure or Cloudflare Turnstile error:

- keep the downloader worker on one stable outbound connection for the entire flow;
- temporarily avoid a VPN or proxy that rotates or filters the worker's public IP;
- do not complete the challenge in a separate local browser;
- stop a stale verification browser before starting another one;
- check downloader logs for Chromium profile-lock or startup errors.

Verification is bound to the worker's browser and network context. A challenge completed from another machine or public IP cannot be safely transferred into the worker session.

## Provider says Connected but downloads fail

Provider session lifetimes are controlled by the provider. The worker checks stored sessions every 15 minutes and attempts normal signed renewal before expiry, including while idle.

The **Connected** label refreshes when the Download Manager loads, when you select Refresh, and after a verification check. It is not continuously polled, and a provider may revoke a session before its recorded expiry. Refresh the page/status, inspect downloader logs, and reconnect the provider if the next download reports an authentication failure.

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
