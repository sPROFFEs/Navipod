#!/bin/bash
#
# Navipod — Bulk Music Import
# ───────────────────────────
# Stages a host folder into the import_stage volume and runs the in-container
# importer to move files into the shared pool, register them in the DB, save
# embedded covers, and (optionally) enrich missing metadata + covers via the
# configured remote providers.
#
# Usage:
#     ./import_music.sh /path/to/music [options]
#
# Options forwarded to the Python importer:
#     --user USERNAME      owner of the created playlist (default: first admin)
#     --no-playlist        don't create a playlist for the imported tracks
#     --enrich             use Spotify/Last.fm/MusicBrainz APIs for covers + metadata
#     --dry-run            scan and report what would happen, change nothing
#     --workers N          concurrent API calls during enrichment (default 4)
#     --verbose            per-track debug output
#
# Examples:
#     ./import_music.sh /mnt/library/rock --enrich
#     ./import_music.sh /tmp/old-music --no-playlist --dry-run
#     ./import_music.sh ~/downloads/album --user alice --enrich
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
# We use rsync --remove-source-files so that the original folder ends up
# empty afterwards (the importer cleans up empty dirs as well). If rsync
# isn't available we fall back to mv.
echo "→ staging audio files from $SRC into $STAGE_HOST"
shopt -s globstar nullglob
EXTS=("mp3" "m4a" "flac" "wav" "ogg" "opus" "aac" "wma")

count=0
if command -v rsync >/dev/null 2>&1; then
    INCLUDE_ARGS=()
    for ext in "${EXTS[@]}"; do
        INCLUDE_ARGS+=(--include "*.${ext}" --include "*.${ext^^}")
    done
    sudo rsync -av --prune-empty-dirs \
        --include "*/" "${INCLUDE_ARGS[@]}" --exclude "*" \
        --remove-source-files \
        "$SRC/" "$STAGE_HOST/"
    count=$(sudo find "$STAGE_HOST" -type f \( $(printf -- '-iname *.%s -o ' "${EXTS[@]}" | sed 's/ -o $//') \) 2>/dev/null | wc -l)
else
    for ext in "${EXTS[@]}"; do
        for f in "$SRC"/**/*."$ext" "$SRC"/**/*."${ext^^}"; do
            [[ -e "$f" ]] || continue
            rel="${f#$SRC/}"
            dest="$STAGE_HOST/$rel"
            sudo mkdir -p "$(dirname "$dest")"
            sudo mv "$f" "$dest"
            ((count++))
        done
    done
fi
shopt -u globstar nullglob

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
