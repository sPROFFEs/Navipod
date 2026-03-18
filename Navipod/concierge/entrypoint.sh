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
    mkdir -p /saas-data/cache /saas-data/users
    
    # FORCE RECURSIVE CHOWN
    # We always run this because subfiles might be root-owned even if the parent dir is correct.
    echo "[ENTRYPOINT] Fixing permissions recursively on /saas-data..."
    chown -R appuser:appuser /saas-data
    chmod -R 775 /saas-data
fi

# 3. DROP PRIVILEGES AND RUN COMMAND
if [ "$(id -u)" = "0" ]; then
    echo "[ENTRYPOINT] Dropping privileges to appuser..."
    exec gosu appuser "$@"
else
    echo "[ENTRYPOINT] Already running as non-root. Warning: Permissions might be broken."
    exec "$@"
fi


