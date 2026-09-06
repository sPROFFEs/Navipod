#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# `npm run check` is commonly launched without activating a virtualenv. Prefer
# the repository environment when present so test collection uses the same
# declared dependency set as CI; an explicitly activated environment wins.
if [[ -z "${VIRTUAL_ENV:-}" && -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    export PATH="$PROJECT_ROOT/.venv/bin:$PATH"
fi
PYTHON_BIN="${PYTHON_BIN:-python}"

"$PYTHON_BIN" -m compileall -q Navipod/concierge Navipod/downloader-worker
"$PYTHON_BIN" -m pytest \
  --cov=Navipod/concierge \
  --cov=Navipod/downloader-worker \
  --cov-report=term-missing:skip-covered \
  --cov-fail-under=19
"$PYTHON_BIN" -m vulture Navipod/concierge Navipod/downloader-worker \
  --min-confidence 100 \
  --exclude '*/tests/*,*/test_*.py'

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck Navipod/concierge/entrypoint.sh Navipod/downloader-worker/entrypoint.sh Navipod/setup.sh scripts/check.sh
elif [[ "${CI:-}" == "true" || "${CHECK_REQUIRE_SHELLCHECK:-0}" == "1" ]]; then
  echo "shellcheck is required in CI. Install it before running the quality check." >&2
  exit 1
else
  echo "shellcheck is not installed; shell lint skipped locally (CI enforces it)." >&2
fi

npm run lint
npm run format:check
