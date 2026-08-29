#!/usr/bin/env bash
set -euo pipefail

python -m compileall -q Navipod/concierge Navipod/downloader-worker
python -m pytest
npm run lint
npm run format:check
