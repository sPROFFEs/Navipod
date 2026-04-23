import ipaddress
import logging
import time

from fastapi import HTTPException, Request
from navipod_config import settings

logger = logging.getLogger("security")

# IN-MEMORY STORE (Volatile)
# Structure: { "IP_ADDR": { "attempts": 0, "block_until": timestamp, "last_seen": timestamp } }
login_attempts = {}

# --- HARDNESS CONFIGURATION ---
MAX_ATTEMPTS = 5          # Failures allowed before ban
BLOCK_TIME = 900          # Punishment time: 15 minutes (900s)
RESET_TIME = 300          # Time to forget previous failures: 5 minutes

def get_real_ip(request: Request) -> str:
    """
    Tries to get real user IP bypassing Docker/Nginx proxy.
    """
    direct_ip = (request.client.host or "").strip() if request.client else ""
    if not direct_ip:
        return "unknown"

    # Trust forwarded headers only from explicitly trusted proxies.
    if not settings.TRUST_PROXY_HEADERS:
        return direct_ip
    if direct_ip not in settings.trusted_proxy_ips:
        return direct_ip

    # 1. Standard proxy headers (Cloudflare, Nginx, Traefik)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # First IP is the real client, subsequent ones are intermediate proxies
        candidate = forwarded.split(",")[0].strip()
        try:
            ipaddress.ip_address(candidate)
            return candidate
        except ValueError:
            logger.warning("Ignored invalid X-Forwarded-For value: %s", candidate)
    
    # 2. Alternative header
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        candidate = real_ip.strip()
        try:
            ipaddress.ip_address(candidate)
            return candidate
        except ValueError:
            logger.warning("Ignored invalid X-Real-IP value: %s", candidate)
    
    # 3. Fallback: Direct IP (might be Docker Gateway 172.x.x.x if no proxy)
    return direct_ip

def check_brute_force(request: Request):
    """
    Called BEFORE processing login. If banned, raises 429 error.
    """
    client_ip = get_real_ip(request)
    now = time.time()
    
    record = login_attempts.get(client_ip)
    
    if not record:
        return # Clean
    
    # 1. Is IP currently in jail?
    if record["block_until"] > now:
        remaining = int(record["block_until"] - now)
        logger.warning(f"Blocked IP {client_ip} tried to enter. Remaining {remaining}s")
        raise HTTPException(
            status_code=429, 
            detail=f"Too many attempts. IP blocked for security. Wait {int(remaining/60)} minutes."
        )

    # 2. If reset time passed without block, clear history
    # (Ex: failed 2 times yesterday, today enters clean)
    if record["block_until"] == 0 and (now - record["last_seen"] > RESET_TIME):
        del login_attempts[client_ip]

def register_failed_attempt(request: Request) -> bool:
    """
    Called when password fails. Returns True if just blocked.
    """
    client_ip = get_real_ip(request)
    now = time.time()
    
    if client_ip not in login_attempts:
        login_attempts[client_ip] = {"attempts": 0, "block_until": 0, "last_seen": now}
    
    record = login_attempts[client_ip]
    record["attempts"] += 1
    record["last_seen"] = now
    
    logger.info(f"Login failed from {client_ip} ({record['attempts']}/{MAX_ATTEMPTS})")
    
    # APPLY HAMMER
    if record["attempts"] >= MAX_ATTEMPTS:
        record["block_until"] = now + BLOCK_TIME
        logger.warning(f"⛔ IP BLOCKED: {client_ip} for {BLOCK_TIME} seconds.")
        return True
    
    return False

def clear_attempts(request: Request):
    """
    Called when login is SUCCESSFUL. Clears history.
    """
    client_ip = get_real_ip(request)
    if client_ip in login_attempts:
        del login_attempts[client_ip]
