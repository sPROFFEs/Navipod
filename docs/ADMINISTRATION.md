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

Open `Admin → Download Manager` to inspect the isolated downloader, switch between automatic, worker-only and legacy modes, and inspect SpotiFLAC lossless-provider sessions.

SpotiFLAC binds verification to the network and browser context that starts the challenge. A remote Navipod admin browser therefore cannot safely create a session for the downloader worker: starting from the server and solving from the administrator's device is rejected as a network change. The Download Manager does not expose a remote **Connect** action until Navipod has a host-side interactive verification bridge. It can still display, refresh and disconnect sessions already present in the worker's private persistent volume. Navipod never asks for TIDAL, Qobuz, Deezer or Amazon credentials.

The default **Automatic** policy tries the isolated worker first and retains the Concierge downloader as a compatibility fallback. A provider is attempted only while its signed session is connected and unexpired. The worker checks connected sessions every 15 minutes and invokes SpotiFLAC's normal signed refresh before expiry, even when no downloads are running. Use **Disconnect** to remove a stored session, or select **Legacy** to bypass the isolated worker entirely.

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
