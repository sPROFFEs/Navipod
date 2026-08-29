# Security

Navipod is self-hosted software that orchestrates containers and stores credentials for optional external providers. Treat the host and its configuration as privileged infrastructure.

## Secrets

Never commit or publicly share:

- `Navipod/.env`;
- `SECRET_KEY`;
- Cloudflare tunnel tokens;
- Spotify client secrets;
- Last.fm keys you consider private;
- YouTube `cookies.txt`;
- database files;
- backup archives containing configuration or user data.

## `SECRET_KEY`

Set a long random value before production use.

```bash
openssl rand -hex 48
```

Navipod uses this key for authentication-related signing and encryption of stored provider credentials. Keep a secure copy with your backup/recovery material.

## Sessions

- The **Remember me** flow stores a signed HttpOnly session cookie, not the user's password.
- Keep `COOKIE_SECURE=true` for HTTPS deployments.
- Use `COOKIE_SECURE=false` only for trusted plain-HTTP LAN/VPN testing.

## TLS

For any deployment reachable over an untrusted network, use HTTPS:

- Cloudflare Tunnel mode terminates TLS at Cloudflare's edge.
- Direct-domain mode uses Let's Encrypt.
- Internal mode has no TLS and must remain on a trusted LAN or inside a VPN.

## Docker socket

The concierge needs Docker control for user-container orchestration. A mounted Docker socket effectively gives the service powerful control over the host's container environment.

Recommendations:

- restrict host shell access;
- restrict Navipod admin access;
- keep the host patched;
- avoid running unrelated untrusted workloads on the same Docker daemon;
- review Compose changes before deploying them.

## Reverse proxy headers

`TRUST_PROXY_HEADERS` should only be enabled when requests are actually coming through a trusted proxy, and `TRUSTED_PROXY_IPS` should be scoped to the proxy addresses/CIDRs you control.

## Backups

Backups can contain user data and secrets. Encrypt them at rest when stored outside the host and test restores periodically.

See [Backup & Restore](BACKUP-RESTORE.md).

## Screenshots and issue reports

Before publishing a screenshot or log, remove:

- private domain names when necessary;
- usernames you do not want public;
- tokens and secrets;
- cookies;
- private playlists/library data;
- IP addresses that reveal private infrastructure.

## License note

Security guidance does not change the project's license. Navipod is licensed for private, personal, non-commercial use only. Read the root [LICENSE](../LICENSE) for binding terms.
