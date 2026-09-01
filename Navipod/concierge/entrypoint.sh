#!/bin/bash
# entrypoint.sh - Fixes volume permissions and drops privileges

set -e

# 1. FIX VOLUME PERMISSIONS
# Only need to fix /saas-data as that's where the persistent data is.
# We do this as root before switching to the unprivileged user.
echo "[ENTRYPOINT] Ensuring /saas-data is owned by appuser (UID 1000)..."

# Create cache directory if it doesn't exist (needed for Spotify/YouTube services)
mkdir -p /saas-data/cache
mkdir -p /saas-data/users

echo "[ENTRYPOINT] Starting as UID: $(id -u) ($(whoami))"

# 1. FIX DOCKER SOCKET PERMISSIONS
if [ -S /var/run/docker.sock ]; then
    DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
    echo "[ENTRYPOINT] Docker socket GID: $DOCKER_GID"
    
    if [ "$(id -u)" = "0" ]; then
        # Create group if it doesn't exist
        groupadd -for -g "$DOCKER_GID" docker_socket || true
        usermod -aG "$DOCKER_GID" appuser || usermod -aG docker_socket appuser || true
        echo "[ENTRYPOINT] Added appuser to docker group ($DOCKER_GID)"
    else
        echo "[ENTRYPOINT] Warning: Cannot fix Docker socket permissions as non-root"
    fi
fi

# 2. FIX VOLUME PERMISSIONS
if [ "$(id -u)" = "0" ]; then
    echo "[ENTRYPOINT] Ensuring /saas-data is owned by appuser (UID 1000)..."
    mkdir -p /saas-data/cache /saas-data/users /saas-data/download-staging/jobs

    # Existing installations may contain files created by older root-running
    # releases. Repair that tree once, then keep restarts O(1) even when the
    # music library contains hundreds of thousands of files.
    PERMISSION_MARKER="/saas-data/.permissions-v2"
    if [ ! -f "$PERMISSION_MARKER" ]; then
        echo "[ENTRYPOINT] Running one-time /saas-data permission migration..."
        chown -R appuser:appuser /saas-data
        find /saas-data -type d -exec chmod 750 {} +
        find /saas-data -type f -exec chmod 640 {} +
        chown -R appuser:downloaders /saas-data/download-staging
        find /saas-data/download-staging -type d -exec chmod 2770 {} +
        find /saas-data/download-staging -type f -exec chmod 660 {} +
        touch "$PERMISSION_MARKER"
        chown appuser:appuser "$PERMISSION_MARKER"
        chmod 640 "$PERMISSION_MARKER"
    else
        chown appuser:appuser /saas-data /saas-data/cache /saas-data/users
        chmod 750 /saas-data /saas-data/cache /saas-data/users
        chown appuser:downloaders /saas-data/download-staging /saas-data/download-staging/jobs
        chmod 2770 /saas-data/download-staging /saas-data/download-staging/jobs
    fi
fi

# 3. DROP PRIVILEGES AND RUN COMMAND
if [ "$(id -u)" = "0" ]; then
    if [ "${KEEP_ROOT:-false}" = "true" ]; then
        echo "[ENTRYPOINT] KEEP_ROOT=true, running as root."
        exec "$@"
    fi
    echo "[ENTRYPOINT] Dropping privileges to appuser..."
    exec gosu appuser "$@"
else
    echo "[ENTRYPOINT] Already running as non-root. Warning: Permissions might be broken."
    exec "$@"
fi
