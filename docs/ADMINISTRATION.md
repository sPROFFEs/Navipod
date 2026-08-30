# Administration

Administrative tools are exposed from the Navipod settings/monitoring areas for admin accounts.

## Main admin responsibilities

The current project documents admin capabilities around:

- user management;
- library health audits;
- metadata rescans;
- rotating backups;
- application monitoring;
- downloader policy and lossless-provider sessions;
- checking and applying updates.

## Download Manager

Open `Admin → Download Manager` to inspect the isolated downloader, switch between automatic, worker-only and legacy modes, and connect SpotiFLAC lossless providers.

Provider connection is admin-only. Clicking **Connect** opens the provider's own Cloudflare verification page in a popup. After verification, the browser returns a one-time grant to Navipod; the isolated worker exchanges it for a signed session and stores that session in its private persistent volume. Navipod does not ask for TIDAL, Qobuz, Deezer or Amazon credentials.

The default **Automatic** policy tries the isolated worker first and retains the Concierge downloader as a compatibility fallback. A provider is attempted only while its signed session is connected and unexpired. The worker checks connected sessions every 15 minutes and invokes SpotiFLAC's normal signed refresh before expiry, even when no downloads are running. Manual verification is needed again only when a provider expires or revokes a session that it can no longer refresh. Use **Disconnect** to remove its stored session.

After pulling a version that changes the downloader image, rebuild it as part of the normal update:

```bash
cd Navipod
docker compose up -d --build downloader concierge
```

## Create an admin manually

If setup did not create the first admin, run this from the directory containing `docker-compose.yaml`:

```bash
read -r -p "Admin username: " NAVIPOD_ADMIN_USERNAME
read -r -s -p "Admin password: " NAVIPOD_ADMIN_PASSWORD; printf '\n'
export NAVIPOD_ADMIN_USERNAME NAVIPOD_ADMIN_PASSWORD

docker compose exec -T \
  -e NAVIPOD_ADMIN_USERNAME \
  -e NAVIPOD_ADMIN_PASSWORD \
  concierge python create_admin.py

unset NAVIPOD_ADMIN_USERNAME NAVIPOD_ADMIN_PASSWORD
```

The password is read without terminal echo and passed through the environment rather than as a visible command-line argument.

## Updates from the UI

The documented flow is:

```text
Admin → System Monitor → Check for Updates → Apply Update
```

The update workflow can create a backup, run schema migrations, rebuild containers when needed and perform health checks.

## CLI update

From the repository:

```bash
git pull
cd Navipod
docker compose up -d --build
```

If you use a copied deployment template, pay attention to merge conflicts in the live Compose/nginx files so you do not accidentally switch deployment mode.

## Library operations

Use the admin UI for documented library-health and metadata-rescan operations. For a host-side bulk migration of an existing collection, use [Importing Music](IMPORTING-MUSIC.md) instead.

## Backups

Navipod provides rotating application backup slots and also benefits from a host-level backup of the complete data root and `.env`.

See [Backup & Restore](BACKUP-RESTORE.md).

## Logs

A useful first diagnostic command is:

```bash
docker compose logs -f concierge
```

For downloader/provider diagnostics:

```bash
docker compose logs -f downloader
```

For a broader service view:

```bash
docker compose ps
```

## Security note

The concierge service controls Docker to orchestrate user containers. Treat the host as privileged infrastructure and restrict administrative access accordingly.

See [Security](SECURITY.md).
