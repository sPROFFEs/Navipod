# Backup & Restore

A reliable Navipod backup should protect both persistent application data and the deployment secrets/configuration needed to interpret that data.

## In-app rotating backups

The System Monitor manages two rotating slots:

- `current`
- `previous`

Use these before risky application changes or updates when available.

## Host-level backup

At minimum, preserve:

```text
/opt/saas-data/
/path/to/repo/Navipod/.env
```

Example archive command:

```bash
sudo tar -czf navipod-backup.tar.gz \
  /opt/saas-data \
  /path/to/repo/Navipod/.env
```

If you changed `HOST_DATA_ROOT`, back up that configured path instead of `/opt/saas-data`.

## Why `.env` matters

`SECRET_KEY` is not disposable configuration. It participates in signing/encryption behavior, including stored provider credentials. A data backup without the matching environment may not be sufficient for a clean recovery.

Protect backup archives as secrets.

## Restore procedure

A conservative host-level restore flow is:

1. Stop the stack.
2. Move the current data/configuration out of the way rather than deleting it immediately.
3. Extract the backup to the original paths.
4. Confirm ownership and permissions.
5. Start the stack.
6. Verify login, library visibility, provider configuration and user instances.

Example:

```bash
cd /path/to/repo/Navipod
docker compose down

# restore /opt/saas-data and .env from your backup

docker compose up -d
```

## Permissions

If restored data has ownership problems, the project troubleshooting guidance uses:

```bash
sudo chown -R 1000:1000 /opt/saas-data
sudo find /opt/saas-data -type d -exec chmod 750 {} +
sudo find /opt/saas-data -type f -exec chmod 640 {} +
```

Only apply permission changes when they match the user/group expectations of your deployment.

## Recommended policy

- Keep more than one host-level backup generation.
- Store at least one copy away from the Navipod host.
- Test a restore before you need it.
- Back up before upgrades, deployment-mode changes or large imports.
- Do not store unencrypted backups in public cloud buckets or shared folders.

## Related guides

- [Administration](ADMINISTRATION.md)
- [Security](SECURITY.md)
- [Importing Music](IMPORTING-MUSIC.md)
