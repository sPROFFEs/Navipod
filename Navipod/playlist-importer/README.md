# Playlist importer prototype

This directory contains an optional, standalone proof of concept for importing
YouTube, YouTube Music, and Spotify playlists into Navipod. It is intentionally
not enabled by the main Compose stack: maintainers can review and evolve the
integration without changing existing deployments.

The importer:

- enumerates public YouTube, YouTube Music, and Spotify playlists;
- submits one track at a time through Navipod's existing job API;
- can target a new playlist, an existing playlist, Liked Songs, or both;
- persists progress in SQLite and supports pause, resume, reauthentication, and
  retrying failed tracks;
- limits concurrent jobs and retries transient failures with backoff;
- encrypts the copied Navipod session token at rest with Fernet; and
- can invalidate generated cover thumbnails when the cache directory is shared.

## Security model

No credentials are included in this directory. Keep secrets in an untracked
`.env` file and never commit browser cookies or the importer's data directory.
The included `.gitignore` and `.dockerignore` exclude those files from both Git
and the Docker build context.

The importer must be exposed below the **same HTTPS origin** as Navipod. It reads
Navipod's existing `access_token` cookie, validates the session against Navipod,
and associates every import with the authenticated JWT subject. The copied token
is encrypted before it is stored in the importer's SQLite database. Changing
`IMPORTER_FERNET_KEY` makes existing stored sessions unreadable.

Treat the importer data directory and encryption key as sensitive. Use a unique
Fernet key, restrict filesystem access, keep HTTPS enabled, and do not expose port
8091 directly to untrusted networks.

## Run the prototype

1. Copy the example environment file and replace every placeholder:

   ```bash
   cp .env.example .env
   python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   ```

2. Ensure the external Docker network in `.env` is the one used by Navipod.

3. Start the sidecar:

   ```bash
   docker compose --env-file .env -f docker-compose.example.yml up -d --build
   ```

4. Add `nginx-location.example.conf` inside the HTTPS server block that already
   serves Navipod, then reload nginx.

5. While signed in to Navipod, run `bookmarklet.js` from the browser developer
   console or create a browser bookmark from the single-line `bookmarklet.txt`.

Spotify enumeration requires a Spotify application client ID and client secret.
YouTube authentication is optional; when it is needed, place a Netscape-format
cookie file in the importer's data volume and set `YTDLP_COOKIE_FILE` to its
container path. Never commit either credential.

## Development

From the repository root, install the normal test requirements and run:

```bash
python -m pytest Navipod/playlist-importer/tests
python -m ruff check Navipod/playlist-importer
python -m ruff format --check Navipod/playlist-importer
```

## Prototype limitations

This implementation deliberately stays outside Concierge, so it currently has
some integration compromises that should be considered before making it a core
feature:

- it infers a newly submitted job ID by comparing the job list before and after
  submission because the current endpoint does not return that ID;
- Liked-Songs-only imports use a temporary Navipod playlist while tracks are
  downloaded;
- cover invalidation requires a writable mount of Concierge's generated cover
  cache;
- state and encrypted sessions live in a separate SQLite database; and
- completed import history has no automatic retention policy.

A native implementation in Concierge could reuse its authentication and database,
return job IDs directly, remove the temporary-playlist workaround, and invalidate
covers without a shared writable volume. The sidecar is supplied primarily as a
working reference for that integration.
