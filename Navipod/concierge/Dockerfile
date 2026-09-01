ARG DENO_VERSION=2.9.4
FROM docker.io/denoland/deno:bin-${DENO_VERSION} AS deno

FROM python:3.11-slim

WORKDIR /app

# Rebuild trigger for updater validation

RUN apt-get update && \
    apt-get install -y --no-install-recommends docker.io docker-compose ffmpeg git unzip nodejs ca-certificates gosu && \
    rm -rf /var/lib/apt/lists/*

# Pull a versioned Deno binary from its official image. Executing the mutable
# deno.land installer made otherwise-identical rebuilds non-reproducible.
COPY --from=deno /deno /usr/local/bin/deno
RUN deno --version

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- SECURITY: Create non-root user ---
RUN adduser --disabled-password --gecos '' appuser
RUN groupadd --gid 2000 downloaders && usermod -aG downloaders appuser

# Handle internal app permissions
RUN chown -R appuser:appuser /app

# Entrypoint handling
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# --reload was a dev-mode filesystem watcher; production doesn't need it.
# NOTE: --limit-max-requests is INTENTIONALLY OMITTED. uvicorn in
# single-worker mode treats that limit as a hard process termination,
# which under `restart: unless-stopped` creates a 1-3s outage window
# where Cloudflare returns a 1033. MALLOC_ARENA_MAX=2 (set in the
# compose env) is the primary leak mitigation; if the ram_audit log
# shows growth that mitigation can't keep up with, schedule a daily
# `docker compose restart concierge` from cron instead.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
