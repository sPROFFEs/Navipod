import json
import logging
import os
import time
import metadata_cache
logger = logging.getLogger(__name__)


CACHE_DIRS = ["/saas-data/cache", "/opt/saas-data/cache"]
CACHE_CLEAN_INTERVAL_SECONDS = 6 * 3600
COVER_NEGATIVE_CACHE_TTL = 86400
FALLBACK_FILE_MAX_AGE = 7 * 86400


def purge_expired_cache_files() -> int:
    now = time.time()
    removed = metadata_cache.purge_expired()

    for cache_dir in CACHE_DIRS:
        if not os.path.isdir(cache_dir):
            continue

        for root, _, files in os.walk(cache_dir):
            for filename in files:
                path = os.path.join(root, filename)
                try:
                    if filename.endswith(".json"):
                        expires_at = _read_expires_at(path)
                        if expires_at is not None and expires_at <= now:
                            os.remove(path)
                            removed += 1
                            continue
                        if expires_at is None and now - os.path.getmtime(path) > FALLBACK_FILE_MAX_AGE:
                            os.remove(path)
                            removed += 1
                            continue

                    if filename.endswith(".nocover") and now - os.path.getmtime(path) > COVER_NEGATIVE_CACHE_TTL:
                        os.remove(path)
                        removed += 1
                except FileNotFoundError:
                    continue
                except Exception as e:
                    logger.warning("Failed to inspect cache path %s: %s", path, e)

    return removed


def _read_expires_at(path: str) -> float | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        expires_at = payload.get("expires_at")
        return float(expires_at) if expires_at is not None else None
    except Exception:
        return None
