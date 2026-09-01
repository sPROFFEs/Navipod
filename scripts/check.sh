#!/usr/bin/env bash
set -euo pipefail

python -m compileall -q Navipod/concierge Navipod/downloader-worker
python -m pytest \
  --cov=Navipod/concierge \
  --cov=Navipod/downloader-worker \
  --cov-report=term-missing:skip-covered \
  --cov-fail-under=19
python -m vulture Navipod/concierge Navipod/downloader-worker \
  --min-confidence 100 \
  --exclude '*/tests/*,*/test_*.py'
shellcheck Navipod/concierge/entrypoint.sh Navipod/downloader-worker/entrypoint.sh Navipod/setup.sh scripts/check.sh
npm run lint
npm run format:check
