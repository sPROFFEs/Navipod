"""
One-off backfill: populate Track.duration for rows that came from the
downloader before it learned to extract duration (see fix in
downloader_service._finalize_track). Reads the file from disk with
mutagen and writes the integer second count.

Run from inside the concierge container:

    docker compose exec concierge python backfill_durations.py
    docker compose exec concierge python backfill_durations.py --dry-run
"""

import argparse
import logging
import os
import sys

import database
import mutagen

logger = logging.getLogger("backfill_durations")


def _duration_for(filepath: str) -> int | None:
    if not filepath or not os.path.exists(filepath):
        return None
    try:
        audio = mutagen.File(filepath)
        if not audio or not getattr(audio, "info", None):
            return None
        length = getattr(audio.info, "length", None)
        return int(length) if length else None
    except Exception as exc:
        logger.warning("Could not read %s: %s", filepath, exc)
        return None


def backfill(dry_run: bool = False) -> dict[str, int]:
    stats = {"scanned": 0, "updated": 0, "missing_file": 0, "no_duration": 0, "already_set": 0}
    db = database.SessionLocal()
    try:
        # Only touch rows that look unpopulated. duration=0 is treated
        # as "not yet extracted" — a real 0-second track is impossible.
        candidates = (
            db.query(database.Track)
            .filter((database.Track.duration.is_(None)) | (database.Track.duration == 0))
            .all()
        )
        logger.info("Found %d candidate tracks", len(candidates))

        for track in candidates:
            stats["scanned"] += 1
            if not track.filepath or not os.path.exists(track.filepath):
                stats["missing_file"] += 1
                continue
            duration = _duration_for(track.filepath)
            if duration is None:
                stats["no_duration"] += 1
                continue
            if track.duration == duration:
                stats["already_set"] += 1
                continue
            if not dry_run:
                track.duration = duration
                stats["updated"] += 1
            else:
                stats["updated"] += 1
                logger.info("[dry-run] would set track #%s -> %ss", track.id, duration)

        if not dry_run:
            db.commit()
    finally:
        db.close()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Track.duration for rows missing it.")
    parser.add_argument("--dry-run", action="store_true", help="report what would happen without writing")
    parser.add_argument("--verbose", action="store_true", help="enable DEBUG logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    stats = backfill(dry_run=args.dry_run)
    label = "[dry-run] " if args.dry_run else ""
    print(
        f"{label}scanned={stats['scanned']} updated={stats['updated']} "
        f"missing_file={stats['missing_file']} no_duration={stats['no_duration']} "
        f"already_set={stats['already_set']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
