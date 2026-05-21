FROM python:3.11-slim

WORKDIR /app

# Rebuild trigger for updater validation

# --- CAMBIO AQUÍ: Añadir ffmpeg, nodejs, git y deno (vía curl) ---
RUN apt-get update && \
    apt-get install -y docker.io docker-compose ffmpeg git unzip nodejs curl ca-certificates gosu && \
    curl -fsSL https://deno.land/install.sh | sh -s -- -y && \
    mv /root/.deno/bin/deno /usr/bin/deno && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- SECURITY: Create non-root user ---
RUN adduser --disabled-password --gecos '' appuser

# Handle internal app permissions
RUN chown -R appuser:appuser /app

# Entrypoint handling
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# --reload was a dev-mode filesystem watcher; production doesn't need it.
# --limit-max-requests recycles the worker after N served requests so any
# slow-growing leak (yt-dlp extractor cache, glibc fragmentation, etc.)
# resets on its own instead of accumulating until OOM.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--limit-max-requests", "5000"]
