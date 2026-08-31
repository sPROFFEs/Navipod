# Administration

Administrative tools are exposed from the Navipod settings/monitoring areas for admin accounts.

## Admin areas

The admin navigation is divided into three main areas:

- **User Admin** — users, library maintenance, deletion requests and federation;
- **System Monitor** — runtime health, user statistics, backups, Wrapped and updates;
- **Download Manager** — downloader policy, jobs and lossless-provider sessions.

All routes and API endpoints behind these pages require an authenticated administrator account.

## User management

Use **User Admin** to create standard or administrator accounts, reset a user's password, or delete an account. Account deletion is destructive; create a backup first if the user's data may be needed later.

## Library management

The Library Management panel provides several separate operations:

- **Search tracks** — find an indexed track and remove it deliberately;
- **Find duplicates** — scan for likely duplicate groups for manual review; the scan does not automatically delete files;
- **Audit Library** — compare indexed records with files, report missing files, incomplete metadata, source totals and loudness-analysis coverage;
- **Remove broken entries** — delete database records whose audio file no longer exists;
- **Rescan metadata** — rebuild metadata from the current files;
- **Scan loudness** — measure tracks used by volume normalization. Large libraries can take hours.

Long-running operations appear as background jobs. Do not repeatedly start the same scan because a page refresh makes it look idle; check the current/recent jobs first.

For a host-side bulk migration of an existing collection, use [Importing Music](IMPORTING-MUSIC.md) instead.

## Song deletion requests

Users can request removal of a shared track without receiving direct delete permission. Review these in the **Song Delete Inbox**:

- **Approve** removes the requested track;
- **Reject** leaves it in place;
- an optional admin response is shown to the requesting user.

Review the track and reason carefully: approval affects the shared pool, not just the requesting user's view.

## Download Manager

Open `Admin → Download Manager` to inspect the isolated downloader, switch between automatic, worker-only and legacy modes, and inspect SpotiFLAC lossless-provider sessions.

SpotiFLAC binds verification to the network and browser context that starts the challenge. The Download Manager can open a short-lived noVNC browser running inside the downloader worker, so verification and grant exchange use the worker's network. The session is single-admin, expires automatically, and is stopped when the administrator closes it. Navipod never asks for TIDAL, Qobuz, Deezer or Amazon credentials.

The default **Automatic** policy tries the isolated worker first and retains the Concierge downloader as a compatibility fallback. **Isolated worker only** disables that fallback; **Legacy Concierge only** bypasses the isolated worker.

A lossless provider is attempted only while its signed session is connected and unexpired. To connect one:

1. Select **Connect** for the provider.
2. Open its verification browser.
3. Complete the challenge in the embedded browser. The browser may turn black or close after successful verification.
4. Select **Check verification** in Navipod to exchange and store the signed grant.
5. Confirm that the provider status changes to **Connected**.

The worker checks connected sessions every 15 minutes and invokes the provider's normal signed refresh before expiry, even when no downloads are running. The status label updates when the page is loaded, manually refreshed, or verification completes; it is not a second-by-second validity probe. A provider can revoke a session early, so a download failure may be the first signal between refreshes. Use **Disconnect** to remove a stored session.

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

## System Monitor

The System Monitor combines operational information and actions:

- CPU, memory, shared-pool usage and the running build/revision;
- per-user listening totals for 24 hours, 7 days, 30 days, year or all time;
- update checks and application;
- current and previous rotating backup slots plus the daily backup schedule;
- Wrapped visibility, schedule, regeneration and per-user reset;
- recent background jobs and storage cleanup.

User statistics can be sorted and paginated. Listening events are finalized when playback ends or is skipped, so a currently playing track may not appear immediately.

Storage cleanup and Wrapped reset actions are intentionally destructive. Read their confirmation text and create a backup before using them to repair data.

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

## Federation (beta)

Federation lets two Navipod servers search and stream from one another without copying the remote catalog locally. It is off until an administrator configures it.

To allow another server to access this one, issue a named federation token and give it securely to the remote administrator. To consume a remote catalog, add its Navipod URL and the token it issued to you, then run a sync. Connections can be disabled, resynchronized or removed, and issued tokens can be revoked.

Only federate with administrators you trust. Use HTTPS, transmit tokens through a private channel, and revoke a token when the relationship ends.

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
