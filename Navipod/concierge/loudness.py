"""FFmpeg-based loudness measurement for volume normalisation.

Measures each track's integrated loudness (LUFS) via the loudnorm filter
and converts it to a gain_db value targeting -14 LUFS (the Spotify /
EBU R128 streaming standard). The gain is cached in the tracks table
and applied client-side by audio_engine.js — no re-encoding of audio
files, no per-play cost.

Used by:
  - media_metadata.backfill (one-time backfill of existing library)
  - admin "loudness scan" job (manual re-scan trigger)
  - importer / downloader (new tracks measured on import)
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Target loudness: -14 LUFS is the de facto streaming standard
# (Spotify, YouTube, Apple Music all cluster around -14 to -16).
TARGET_LUFS = -14.0


@dataclass(frozen=True)
class LoudnessResult:
    gain_db: float  # dB to apply toward TARGET_LUFS (negative = quieter)
    peak: float  # true peak 0..1 (1.0 = full scale)


def _find_ffmpeg() -> str | None:
    """Locate the ffmpeg binary. Returns None if not installed."""
    return shutil.which("ffmpeg")


def measure_loudness(file_path: str | Path) -> LoudnessResult | None:
    """Run ffmpeg loudnorm in measurement mode and parse the result.

    Returns LoudnessResult(gain_db, peak) or None on failure.
    The measurement pass decodes the entire file but writes no output
    (``-f null -``). On this hardware it runs ~50-78x faster than
    realtime (see benchmark in the PR discussion), so a 3.5 min track
    takes ~4 s.
    """
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        logger.warning("ffmpeg not found — loudness measurement skipped")
        return None

    path = Path(file_path)
    if not path.is_file():
        logger.debug("Loudness measurement: file not found: %s", path)
        return None

    cmd = [
        ffmpeg,
        "-i",
        str(path),
        "-af",
        "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
        "-f",
        "null",
        "-",  # discard output — we only want stderr metadata
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,  # generous; a 5 min track takes ~4 s
        )
    except subprocess.TimeoutExpired:
        logger.warning("Loudness measurement timed out for %s", path.name)
        return None
    except Exception as exc:
        logger.warning("Loudness measurement failed for %s: %s", path.name, exc)
        return None

    # ffmpeg writes the loudnorm JSON to stderr, not stdout.
    stderr = proc.stderr or ""
    if not stderr:
        logger.debug("Loudness measurement: empty ffmpeg output for %s", path.name)
        return None

    return _parse_loudnorm_json(stderr)


def _parse_loudnorm_json(stderr: str) -> LoudnessResult | None:
    """Extract the loudnorm JSON block from ffmpeg stderr and compute gain."""
    # The JSON block is delimited by { ... } in the stderr output.
    # Find the first '{' and the matching closing '}'.
    start = stderr.find("{")
    if start == -1:
        return None
    # Find the last '}' after the first '{' — the JSON is at the end
    # of the stderr output.
    end = stderr.rfind("}")
    if end == -1 or end <= start:
        return None

    json_str = stderr[start : end + 1]
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        logger.debug("Loudness measurement: could not parse JSON from ffmpeg")
        return None

    # loudnorm outputs: input_i (integrated loudness), input_tp (true peak)
    input_i_str = data.get("input_i")
    input_tp_str = data.get("input_tp")
    if not input_i_str:
        return None

    try:
        input_i = float(input_i_str)
    except (ValueError, TypeError):
        return None

    try:
        input_tp = float(input_tp_str) if input_tp_str else 1.0
    except (ValueError, TypeError):
        input_tp = 1.0

    # gain_db = how many dB to add to reach TARGET_LUFS.
    # If the track is at -20 LUFS and target is -14, gain = +6 dB.
    # If the track is at -8 LUFS, gain = -6 dB (quieter).
    gain_db = TARGET_LUFS - input_i

    # Peak is in dBFS (0 = full scale). Convert to linear 0..1.
    # loudnorm reports true peak which can exceed 0 dBFS for inter-sample
    # peaks; clamp to 1.0.
    peak_linear = 10 ** (input_tp / 20) if input_tp > -100 else 1.0
    peak_linear = max(0.0, min(peak_linear, 1.0))

    return LoudnessResult(gain_db=round(gain_db, 2), peak=round(peak_linear, 4))


def backfill_loudness(batch_size: int = 100, progress_callback=None) -> int:
    """Measure loudness for all tracks that haven't been measured yet.

    Called from main.py startup (in a thread) and from the admin
    loudness-scan job. Returns the number of tracks measured.

    If ``progress_callback`` is given it is called as
    ``callback(measured, total, current_title)`` after each track so the
    caller can update a job-progress bar. The callback is best-effort —
    it must not raise.
    """
    import database

    db = database.SessionLocal()
    updated = 0
    try:
        total = db.query(database.Track).filter(database.Track.loudness_measured_at.is_(None)).count()
        while True:
            tracks = (
                db.query(database.Track).filter(database.Track.loudness_measured_at.is_(None)).limit(batch_size).all()
            )
            if not tracks:
                break
            for track in tracks:
                if not track.filepath:
                    track.loudness_measured_at = datetime.now(timezone.utc)
                    updated += 1
                    continue
                result = measure_loudness(track.filepath)
                if result:
                    track.gain_db = result.gain_db
                    track.peak = result.peak
                # Mark as measured even on failure so we don't retry
                # infinitely — the admin can force a re-scan.
                track.loudness_measured_at = datetime.now(timezone.utc)
                updated += 1
                if progress_callback:
                    try:
                        progress_callback(updated, total, track.title or track.filepath)
                    except Exception:
                        pass  # progress reporting must never break the scan
            db.commit()
        return updated
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def measure_track_for_import(db, track_id: int) -> None:
    """Measure loudness for a single freshly-imported track.

    Called from the import / download path so new tracks get a gain
    value without waiting for the next backfill cycle. Best-effort:
    if ffmpeg is missing or the measurement fails, the track simply
    has no cached gain and the /gain endpoint falls through to tag
    reading (or defaults to 0 dB).
    """
    import database

    track = db.query(database.Track).filter(database.Track.id == track_id).first()
    if not track or not track.filepath:
        return
    # Skip if already measured (e.g. duplicate import).
    if track.loudness_measured_at is not None:
        return
    result = measure_loudness(track.filepath)
    if result:
        track.gain_db = result.gain_db
        track.peak = result.peak
    track.loudness_measured_at = datetime.now(timezone.utc)
    db.commit()
