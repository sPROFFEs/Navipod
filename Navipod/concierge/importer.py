"""
Bulk music importer for Navipod.

Walks a folder recursively, moves audio files into the shared pool, registers
each track in the DB with metadata extracted via mutagen, saves embedded
cover art if present, and optionally enriches missing covers + metadata via
the configured remote providers (Spotify / Last.fm / MusicBrainz).

This is a pool-only tool — it does not create playlists or assign tracks to
any user. Tracks land in the shared pool and become available to every user
of the instance via the search / library views.

Designed to run inside the `concierge` container so it has the same DB,
pool path, cover cache and provider services as the live FastAPI app.

When `--enrich` is set, the script reads the API keys from the first admin's
`DownloadSettings` row (the same ones configured in `Settings > Engine` in
the web UI). Decryption works because the container has access to
`SECRET_KEY` from the .env.

Usage (inside the container):
    python importer.py --source /saas-data/import_stage [--enrich] [--dry-run]

Flags:
    --source PATH        Folder to scan recursively (required)
    --enrich             Use Spotify/Last.fm/MusicBrainz APIs to fill missing
                         metadata and download covers when not embedded
    --dry-run            Scan and report what would happen, change nothing
    --workers N          Concurrency for cover/metadata API calls (default 4)
    --verbose            Per-track debug output

The wrapper script `import_music.sh` at the repo root prepares the staging
area and runs this with sensible defaults.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

# DB + services live in the concierge package; importer runs inside that container
try:
    from database import SessionLocal, Track, User, DownloadSettings
    import track_identity
    import cover_cache
    import metadata_service
except ImportError:                                                       # pragma: no cover
    from concierge.database import SessionLocal, Track, User, DownloadSettings
    from concierge import track_identity
    from concierge import cover_cache
    from concierge import metadata_service


POOL_ROOT  = Path("/saas-data/pool")
AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".opus", ".aac", ".wma"}

logger = logging.getLogger("importer")


# ──────────────────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────────────────

def file_hash(path: Path, chunk: int = 64 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


_FS_ALLOWED = set(" -_.()[]&,'!")

def fs_safe(name: str) -> str:
    """Strip path-unsafe characters; preserve Unicode letters / digits."""
    cleaned = "".join(c for c in (name or "") if c.isalnum() or c in _FS_ALLOWED)
    return cleaned.strip() or "Unknown"


# ──────────────────────────────────────────────────────────────────────────────
#  Metadata extraction (mutagen)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class LocalMetadata:
    """All info we can pull from the file itself."""
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: int = 0           # seconds
    cover_bytes: bytes | None = None
    cover_mime: str = ""


def extract_metadata(path: Path) -> LocalMetadata:
    """Pull everything we can from an audio file's tags. Never raises."""
    md = LocalMetadata(title=path.stem)
    try:
        import mutagen
        from mutagen.id3 import APIC
        from mutagen.flac import Picture

        # Easy mode for common tags
        easy = mutagen.File(path, easy=True)
        if easy:
            md.title  = (easy.get("title",  [md.title])[0] or md.title).strip()
            md.artist = (easy.get("artist", [""])[0] or "").strip()
            md.album  = (easy.get("album",  [""])[0] or "").strip()

        # Re-open in non-easy mode to get duration + embedded picture
        full = mutagen.File(path)
        if full and full.info and getattr(full.info, "length", None):
            md.duration = int(full.info.length)

        # Embedded artwork — different containers stash it differently
        if full:
            # ID3 (mp3)
            if hasattr(full, "tags") and full.tags:
                for tag in full.tags.values() if hasattr(full.tags, "values") else []:
                    if isinstance(tag, APIC):
                        md.cover_bytes = tag.data
                        md.cover_mime  = tag.mime or "image/jpeg"
                        break
            # FLAC pictures
            if not md.cover_bytes and hasattr(full, "pictures") and full.pictures:
                pic: Picture = full.pictures[0]
                md.cover_bytes = pic.data
                md.cover_mime  = pic.mime or "image/jpeg"
            # MP4 / M4A
            if not md.cover_bytes and hasattr(full, "tags") and full.tags:
                covr = full.tags.get("covr") if hasattr(full.tags, "get") else None
                if covr:
                    md.cover_bytes = bytes(covr[0])
                    md.cover_mime  = "image/jpeg"

    except Exception as e:
        logger.debug("metadata extraction failed for %s: %s", path, e)

    if not md.artist: md.artist = "Unknown Artist"
    if not md.album:  md.album  = "Unknown Album"
    return md


# ──────────────────────────────────────────────────────────────────────────────
#  Cover handling
# ──────────────────────────────────────────────────────────────────────────────

async def download_cover_if_missing(
    track_id: int, settings: DownloadSettings | None,
    title: str, artist: str, album: str,
    sem: asyncio.Semaphore, http: httpx.AsyncClient,
) -> bool:
    """If the track has no cached cover yet, ask the configured providers."""
    if cover_cache.get_cached_cover(track_id):
        return False
    if not settings:
        return False

    async with sem:
        try:
            url = await metadata_service.resolve_cover_url(settings, title, artist, album)
            if not url:
                return False
            r = await http.get(url, timeout=15.0, follow_redirects=True)
            if r.status_code == 200 and r.content:
                cover_cache.cache_cover(track_id, r.content)
                return True
        except Exception as e:
            logger.debug("cover fetch failed for #%s (%s — %s): %s", track_id, artist, title, e)
    return False


# ──────────────────────────────────────────────────────────────────────────────
#  Per-file processing
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Stats:
    scanned: int   = 0
    imported: int  = 0
    duplicate: int = 0
    failed: int    = 0
    covers_embedded: int = 0
    covers_remote:   int = 0
    enriched: int  = 0
    new_track_ids: list[int] = field(default_factory=list)


def _pool_target(md: LocalMetadata, ext: str, pool_root: Path = POOL_ROOT) -> Path:
    folder = pool_root / fs_safe(md.artist) / fs_safe(md.album)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{fs_safe(md.title)}{ext.lower()}"


def _resolve_collision(target: Path, fhash: str) -> Path:
    """If a different file already lives at `target`, append a hash suffix."""
    if not target.exists():
        return target
    return target.with_name(f"{target.stem}_{fhash[:6]}{target.suffix}")


def import_one(db, path: Path, *, dry_run: bool, stats: Stats) -> int | None:
    """Move file into pool + create Track row. Returns the new track id, or None."""
    try:
        fhash = file_hash(path)
        md    = extract_metadata(path)
        ident = track_identity.compute_track_identity(md.artist, md.title)

        existing = track_identity.find_existing_track(
            db, file_hash=fhash, fingerprint=ident["fingerprint"]
        )
        if existing:
            stats.duplicate += 1
            logger.info("DUP   %s  →  already in DB as #%s (%s — %s)",
                        path.name, existing.id, existing.artist, existing.title)
            if not dry_run:
                # Source file is now redundant; delete it from the staging area
                try: path.unlink()
                except OSError: pass
            return None

        target = _resolve_collision(_pool_target(md, path.suffix), fhash)
        if dry_run:
            logger.info("DRY   %s  →  %s", path.name, target)
            return None

        shutil.move(str(path), str(target))
        track = Track(
            title           = md.title,
            artist          = md.artist,
            album           = md.album,
            duration        = md.duration,
            filepath        = str(target),
            source_id       = f"local:{fhash}",
            file_hash       = fhash,
            artist_norm     = ident["artist_norm"],
            title_norm      = ident["title_norm"],
            version_tag     = ident["version_tag"],
            fingerprint     = ident["fingerprint"],
            source_provider = "local",
        )
        db.add(track)
        db.commit()
        db.refresh(track)

        # Embedded cover → cover cache
        if md.cover_bytes:
            try:
                cover_cache.cache_cover(track.id, md.cover_bytes)
                stats.covers_embedded += 1
            except Exception as e:
                logger.warning("could not cache embedded cover for #%s: %s", track.id, e)

        stats.imported += 1
        stats.new_track_ids.append(track.id)
        logger.info("OK    %s  →  #%s  %s — %s", path.name, track.id, md.artist, md.title)
        return track.id

    except Exception as e:
        stats.failed += 1
        logger.error("FAIL  %s  (%s)", path, e)
        try: db.rollback()
        except Exception: pass
        return None


# ──────────────────────────────────────────────────────────────────────────────
#  Async enrichment pass (runs after all files are imported)
# ──────────────────────────────────────────────────────────────────────────────

async def enrich_pass(track_ids: list[int], settings: DownloadSettings,
                      workers: int, stats: Stats) -> None:
    if not track_ids:
        return

    # `sem` was previously used only inside _process to gate the HTTP
    # call to enrich_metadata. The DB session opened ABOVE that
    # semaphore was unbounded — for an N-track import, asyncio.gather
    # spawned N concurrent _process tasks, each holding a fresh
    # SQLAlchemy connection (we use NullPool, so each session is its
    # own SQLite file descriptor). On a 1700-track import that blows
    # past the default Linux fd limit of 1024 and SQLite starts
    # returning "unable to open database file" — a transient error
    # that the metadata_cache helper then mistakes for corruption.
    #
    # Add an OUTER semaphore that bounds total concurrent tasks. The
    # inner `sem` is kept for the HTTP enrich call so we don't change
    # the cover-download / enrichment HTTP behavior.
    http_sem = asyncio.Semaphore(workers)            # HTTP bound (existing)
    db_sem = asyncio.Semaphore(max(2, workers))      # DB / overall bound (new)

    async with httpx.AsyncClient() as http:
        async def _process(tid: int):
            async with db_sem:
                db = SessionLocal()
                try:
                    t = db.get(Track, tid)
                    if not t: return
                    if await download_cover_if_missing(
                        t.id, settings, t.title, t.artist, t.album, http_sem, http
                    ):
                        stats.covers_remote += 1
                    async with http_sem:
                        try:
                            await metadata_service.enrich_metadata(settings, t.title, t.artist, t.album)
                            stats.enriched += 1
                        except Exception as e:
                            logger.debug("enrich failed for #%s: %s", t.id, e)
                finally:
                    db.close()

        # gather still creates len(track_ids) tasks but only `db_sem`
        # of them run concurrently — the rest are queued at the
        # `async with db_sem` await.
        await asyncio.gather(*(_process(tid) for tid in track_ids))


# ──────────────────────────────────────────────────────────────────────────────
#  API settings discovery
# ──────────────────────────────────────────────────────────────────────────────

def get_api_settings(db) -> DownloadSettings | None:
    """
    Return the first admin's DownloadSettings if it has any provider key
    configured. Decryption happens automatically inside the container because
    SECRET_KEY is loaded from the env. If no admin has configured keys yet,
    return None so enrichment can be silently skipped.
    """
    admin = db.query(User).filter_by(is_admin=True).first()
    if not admin:
        return None
    s = db.query(DownloadSettings).filter_by(user_id=admin.id).first()
    if not s:
        return None
    has_any = bool(
        getattr(s, "spotify_client_id", None) or
        getattr(s, "lastfm_api_key", None)
    )
    return s if has_any else None


# ──────────────────────────────────────────────────────────────────────────────
#  Top-level driver
# ──────────────────────────────────────────────────────────────────────────────

def find_audio_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS]


def cleanup_empty_dirs(root: Path) -> None:
    for dirpath, _, _ in sorted(os.walk(root, topdown=False), reverse=True):
        try: os.rmdir(dirpath)
        except OSError: pass


def run(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    if not source.is_dir():
        logger.error("source folder does not exist: %s", source)
        return 2

    files = find_audio_files(source)
    if not files:
        logger.error("no audio files found under %s", source)
        return 3

    db = SessionLocal()
    try:
        # Resolve API keys for enrichment (always pulls from first admin's
        # configured Settings > Engine credentials).
        settings = None
        if args.enrich:
            settings = get_api_settings(db)
            if not settings:
                logger.warning(
                    "--enrich requested but no admin has API keys configured in "
                    "Settings > Engine. API enrichment will be skipped."
                )

        stats = Stats()
        logger.info("scanning %d audio file(s) under %s", len(files), source)

        for idx, path in enumerate(files, start=1):
            stats.scanned += 1
            print(f"[{idx}/{len(files)}] {path.name}", flush=True)
            import_one(db, path, dry_run=args.dry_run, stats=stats)

        if args.enrich and settings and stats.new_track_ids and not args.dry_run:
            logger.info("enriching %d new track(s) — covers + metadata cache",
                        len(stats.new_track_ids))
            asyncio.run(enrich_pass(stats.new_track_ids, settings,
                                    workers=args.workers, stats=stats))

        if not args.dry_run:
            cleanup_empty_dirs(source)

    finally:
        db.close()

    print()
    print("─" * 60)
    print(f"  scanned:           {stats.scanned}")
    print(f"  imported:          {stats.imported}")
    print(f"  duplicates:        {stats.duplicate}")
    print(f"  failed:            {stats.failed}")
    print(f"  embedded covers:   {stats.covers_embedded}")
    if args.enrich:
        print(f"  remote covers:     {stats.covers_remote}")
        print(f"  metadata enriched: {stats.enriched}")
    print("─" * 60)
    return 0 if stats.failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bulk import audio files into the Navipod shared pool."
    )
    parser.add_argument("--source", required=True, help="folder to scan recursively")
    parser.add_argument("--enrich", action="store_true",
                        help="call remote APIs to download missing covers and warm metadata cache")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would happen without writing to disk or DB")
    parser.add_argument("--workers", type=int, default=4,
                        help="concurrent API calls during enrichment (default 4)")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level   = logging.DEBUG if args.verbose else logging.INFO,
        format  = "%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt = "%H:%M:%S",
    )
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
