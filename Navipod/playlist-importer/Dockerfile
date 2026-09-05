FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --requirement /app/requirements.txt

RUN groupadd --gid 10001 importer \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin importer \
    && mkdir -p /data /navipod-cover-cache \
    && chown -R 10001:10001 /app /data /navipod-cover-cache

COPY --chown=10001:10001 app.py /app/app.py

USER 10001:10001

EXPOSE 8091

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8091"]
