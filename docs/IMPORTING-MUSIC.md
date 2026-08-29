# Importing Music

Navipod includes a bulk importer for moving an existing host music collection into the shared pool.

> [!WARNING]
> The importer **moves** supported audio files into the Navipod pool. Do not point it at your only copy of a library until you have verified your backup strategy.

## Basic import

Run from the repository root:

```bash
./import_music.sh /path/to/your/music
```

To enrich missing artwork/metadata using providers configured in Navipod:

```bash
./import_music.sh /path/to/your/music --enrich
```

## What the importer does

- Recursively scans supported audio formats.
- Moves tracks into the shared pool under an artist/album structure.
- Reads tags using Mutagen.
- Registers tracks in the Navipod database.
- Saves embedded cover artwork to the cover cache.
- Can fetch missing artwork/metadata from configured providers when `--enrich` is used.
- Detects duplicates using hash/fingerprint information and skips/reuses them as appropriate.

Supported extensions documented by the project include:

```text
mp3  m4a  flac  wav  ogg  opus  aac  wma
```

The shared pool is stored under the configured `HOST_DATA_ROOT` (default `/opt/saas-data`).

## Important behavior

Imported tracks become available to users through Navipod's library/search flows. Bulk import does not automatically create personal playlists or assign the imported tracks to one specific user.

## Useful flags

```text
--enrich
--dry-run
--workers N
--verbose
```

For the currently installed script's full option list:

```bash
./import_music.sh --help
```

## Before a large import

1. Back up the source collection.
2. Confirm free disk space under the Navipod data root.
3. Run a dry run first.
4. Configure Spotify and/or Last.fm if you want enrichment.
5. Start with a small representative folder before migrating everything.

## Related guides

- [Configuration](CONFIGURATION.md)
- [Backup & Restore](BACKUP-RESTORE.md)
- [Troubleshooting](TROUBLESHOOTING.md)
