#!/bin/bash
#
# Navipod — Bulk Music Import
# ───────────────────────────
# Imports audio files from a host folder into the SHARED POOL only. Tracks
# become available to every user of the instance via search / library views.
# This script does NOT create playlists and does NOT assign tracks to any
# user; it only moves audio into /opt/saas-data/pool, registers each track
# in the DB with metadata, and optionally fetches missing covers via the
# remote APIs already configured by the admin in `Settings > Engine`.
#
# Usage:
#     ./import_music.sh /path/to/music [options]
#
# Options forwarded to the Python importer:
#     --enrich             use Spotify/Last.fm/MusicBrainz APIs for covers + metadata
#                          (uses the keys configured in Settings > Engine)
#     --dry-run            scan and report what would happen, change nothing
#     --workers N          concurrent API calls during enrichment (default 4)
#     --verbose            per-track debug output
#
# Examples:
#     ./import_music.sh /mnt/library
#     ./import_music.sh /mnt/library --enrich
#     ./import_music.sh /tmp/old-music --dry-run
#

set -euo pipefail

# ─── Locate the Compose project ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$SCRIPT_DIR/Navipod"

if [[ ! -f "$COMPOSE_DIR/docker-compose.yaml" ]]; then
    echo "✗ docker-compose.yaml not found at $COMPOSE_DIR" >&2
    echo "  Run this script from the Navipod repository root." >&2
    exit 1
fi

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    echo "✗ docker / docker compose not available on PATH" >&2
    exit 1
fi

# ─── Parse first positional argument: the source folder ──────────────────────
if [[ $# -lt 1 || "$1" == "-h" || "$1" == "--help" ]]; then
    sed -n '2,30p' "$0"
    exit 0
fi

SRC="$1"; shift
if [[ ! -d "$SRC" ]]; then
    echo "✗ source folder does not exist: $SRC" >&2
    exit 2
fi
SRC="$(cd "$SRC" && pwd)"

# ─── Verify the concierge container is up ────────────────────────────────────
cd "$COMPOSE_DIR"
if ! docker compose ps --status running --services 2>/dev/null | grep -qx concierge; then
    echo "✗ the 'concierge' container isn't running. Start the stack first:" >&2
    echo "    cd $COMPOSE_DIR && docker compose up -d" >&2
    exit 3
fi

# ─── Resolve the host path that the container sees as /saas-data/import_stage
DATA_ROOT="${HOST_DATA_ROOT:-/opt/saas-data}"
STAGE_HOST="$DATA_ROOT/import_stage"

if [[ ! -d "$STAGE_HOST" ]]; then
    echo "→ creating staging directory: $STAGE_HOST"
    sudo mkdir -p "$STAGE_HOST"
    sudo chmod 777 "$STAGE_HOST"
fi

# ─── Move audio files into the staging volume ────────────────────────────────
# Strategy: enumerate files with `find -iname` (case-insensitive,
# recursive, doesn't depend on shell globstar or rsync filter rules)
# and move each one preserving its relative subpath under $SRC.
#
# Why not rsync? We used rsync's --include filter previously, which
# DOES recurse but is case-sensitive — files like "Song.Mp3" slipped
# through. `find -iname` matches *.MP3, *.mp3, *.Mp3, *.mP3, etc.,
# eliminating an entire class of "no audio files found" reports.
echo "→ staging audio files from $SRC into $STAGE_HOST"

# Audio extensions we accept. Add new ones here — the find call below
# expands them with -iname so case is irrelevant.
EXTS=(
    mp3 m4a flac wav ogg opus aac wma
    aif aiff alac dsf dsd ape mka mp4
)

# Build `\( -iname '*.mp3' -o -iname '*.flac' -o ... \)` for find.
FIND_FILTER=( '(' )
first=1
for ext in "${EXTS[@]}"; do
    if [[ $first -eq 1 ]]; then
        FIND_FILTER+=( -iname "*.${ext}" )
        first=0
    else
        FIND_FILTER+=( -o -iname "*.${ext}" )
    fi
done
FIND_FILTER+=( ')' )

# Enumerate audio files NUL-separated to survive spaces/newlines in
# filenames. `-print0` + `read -d ''` is the canonical safe loop.
count=0
while IFS= read -r -d '' f; do
    rel="${f#$SRC/}"
    dest="$STAGE_HOST/$rel"
    sudo mkdir -p "$(dirname "$dest")"
    sudo mv "$f" "$dest"
    # `count=$((count+1))` instead of `((count++))` because the latter
    # exits with status 1 when the OLD value was 0, which under
    # `set -e` would abort the script on the very first file.
    count=$((count+1))
done < <(sudo find "$SRC" -type f "${FIND_FILTER[@]}" -print0)

# Clean up empty source directories left behind by the moves so the
# user's original tree ends up tidy (the importer also drops empty
# dirs in the staging volume on its way out).
sudo find "$SRC" -mindepth 1 -depth -type d -empty -delete 2>/dev/null || true

if [[ "$count" -eq 0 ]]; then
    echo "✗ no audio files found under $SRC" >&2
    exit 4
fi
echo "→ staged $count file(s)"

# ─── Run the in-container importer ───────────────────────────────────────────
echo "→ running importer inside the concierge container"
echo
docker compose exec -T concierge python importer.py \
    --source /saas-data/import_stage "$@"
RC=$?

# ─── Tidy up: remove now-empty staging directory contents ───────────────────
sudo find "$STAGE_HOST" -mindepth 1 -depth -empty -delete 2>/dev/null || true

exit $RC
