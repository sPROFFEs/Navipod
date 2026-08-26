"""Read-only library health inspection shared by the admin job and tests."""

from __future__ import annotations

import database
import path_security
from sqlalchemy.orm import Session


def _serialize_track(track: database.Track) -> dict:
    return {
        "id": track.id,
        "title": track.title or "Unknown",
        "artist": track.artist or "Unknown",
        "filepath": track.filepath,
        "source_provider": track.source_provider or "unknown",
    }


def build_library_audit(db: Session, *, roots: tuple[str, ...], result_limit: int = 100) -> dict:
    tracks = db.query(database.Track).order_by(database.Track.id).all()
    missing_files = []
    missing_metadata = []
    source_counts: dict[str, int] = {}
    total_bytes = 0
    missing_file_count = 0
    for track in tracks:
        source = track.source_provider or "unknown"
        source_counts[source] = source_counts.get(source, 0) + 1
        incomplete = not track.artist or not track.album or not track.genre or track.year is None
        if incomplete and len(missing_metadata) < result_limit:
            missing_metadata.append(_serialize_track(track))

        safe_file = None
        if track.filepath:
            for root in roots:
                try:
                    candidate = path_security.resolve_under(track.filepath, root)
                    if candidate.is_file():
                        safe_file = candidate
                        break
                except (OSError, path_security.UnsafePathError):
                    continue
        if safe_file:
            try:
                total_bytes += safe_file.stat().st_size
            except OSError:
                pass
        else:
            missing_file_count += 1
            if len(missing_files) < result_limit:
                missing_files.append(_serialize_track(track))

    incomplete_count = sum(
        1 for track in tracks if not track.artist or not track.album or not track.genre or track.year is None
    )
    loudness_pending = sum(1 for track in tracks if track.loudness_measured_at is None)
    return {
        "track_count": len(tracks),
        "total_bytes": total_bytes,
        "source_counts": source_counts,
        "missing_file_count": missing_file_count,
        "missing_files": missing_files,
        "missing_metadata_count": incomplete_count,
        "missing_metadata": missing_metadata,
        "metadata_pending_count": sum(1 for track in tracks if track.metadata_scanned_at is None),
        "loudness_pending_count": loudness_pending,
        "loudness_measured_count": len(tracks) - loudness_pending,
        "results_truncated": missing_file_count > len(missing_files) or incomplete_count > len(missing_metadata),
    }
