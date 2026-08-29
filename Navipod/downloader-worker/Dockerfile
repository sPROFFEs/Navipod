FROM python:3.11-slim

ARG SPOTIFLAC_EXTENSIONS_COMMIT=6a4227aec696cd98d6fa9d25d92b1a38f9ae1a07

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates chromium curl ffmpeg gosu nodejs unzip xvfb && \
    curl -fsSL https://deno.land/install.sh | sh -s -- -y && \
    mv /root/.deno/bin/deno /usr/local/bin/deno && \
    command -v Xvfb && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Extensions are executable third-party code. Pin both the repository commit
# and each archive digest so a mutable registry cannot change a rebuild.
RUN mkdir -p /opt/spotiflac-extensions && \
    curl -fsSL "https://raw.githubusercontent.com/spotiflacapp/SpotiFLAC-Extension/${SPOTIFLAC_EXTENSIONS_COMMIT}/extensions/tidal-web.sflx" -o /tmp/tidal-web.sflx && \
    curl -fsSL "https://raw.githubusercontent.com/spotiflacapp/SpotiFLAC-Extension/${SPOTIFLAC_EXTENSIONS_COMMIT}/extensions/qobuz-web.sflx" -o /tmp/qobuz-web.sflx && \
    curl -fsSL "https://raw.githubusercontent.com/spotiflacapp/SpotiFLAC-Extension/${SPOTIFLAC_EXTENSIONS_COMMIT}/extensions/deezer.sflx" -o /tmp/deezer.sflx && \
    curl -fsSL "https://raw.githubusercontent.com/spotiflacapp/SpotiFLAC-Extension/${SPOTIFLAC_EXTENSIONS_COMMIT}/extensions/amzn.sflx" -o /tmp/amazon.sflx && \
    printf '%s  %s\n' \
        'd346f3e5fdb6f349d8f6ede1310d1961862936f64b3dabbbb4fba868cea31a9a' '/tmp/tidal-web.sflx' \
        '3b5fab92608ada9eefe94dcc11a11bef79204804dee3ced2d86809df0d5306cd' '/tmp/qobuz-web.sflx' \
        'f6a2505da67c25d4ddfc5f233fdc14e127031f7e15a39fc9d2d0b82fc4b1fd60' '/tmp/deezer.sflx' \
        '5089f8e0458b02fe6e52e55a6f9a050d1981a53542a7e210ec53661dba4c8e00' '/tmp/amazon.sflx' \
        | sha256sum -c - && \
    for extension in tidal-web qobuz-web deezer amazon; do \
        mkdir -p "/opt/spotiflac-extensions/$extension"; \
        unzip -q "/tmp/$extension.sflx" -d "/opt/spotiflac-extensions/$extension"; \
    done && \
    rm -f /tmp/*.sflx

RUN groupadd --gid 2000 downloaders && \
    useradd --create-home --uid 2000 --gid downloaders --shell /usr/sbin/nologin downloader && \
    mkdir -p /home/downloader/.spotiflac/extensions && \
    cp -a /opt/spotiflac-extensions/. /home/downloader/.spotiflac/extensions/ && \
    chown -R downloader:downloaders /home/downloader /app

COPY worker.py /app/worker.py
COPY entrypoint.sh /usr/local/bin/downloader-entrypoint
RUN chmod 0755 /usr/local/bin/downloader-entrypoint

ENV HOME=/home/downloader \
    XDG_CACHE_HOME=/tmp/cache \
    CHROME_PATH=/usr/bin/chromium \
    TS_MAX_CONCURRENT_BROWSERS=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8081
ENTRYPOINT ["/usr/local/bin/downloader-entrypoint"]
CMD ["uvicorn", "worker:app", "--host", "0.0.0.0", "--port", "8081"]
