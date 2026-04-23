import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

from navipod_config import settings

BASE_DOWNLOADS_DIR = Path(settings.MUSIC_ROOT).resolve()

def get_secure_path(base_dir: Path | str, user_input_path: str) -> Path:
    """
    Ensures that the final path is within the base_dir.
    Prevents Path Traversal attacks (e.g. ../../etc/passwd)
    """
    base = Path(base_dir).resolve()
    
    # 1. Clean basic path noise
    clean_input = os.path.normpath(user_input_path)
    if clean_input.startswith("/") or clean_input.startswith("\\"):
         # Remove leading slashes to prevent absolute path override
         clean_input = clean_input.lstrip("/\\")
         
    # 2. Resolve final absolute path
    target = (base / clean_input).resolve()
    
    # 3. Jail Check
    if not str(target).startswith(str(base)):
        raise ValueError(f"Path Traversal Attempt Detected: {user_input_path}")
        
    return target

def is_safe_url(url: str) -> bool:
    """
    Validates that a URL is safe for server-side requests (SSRF Protection).
    Blocks localhost, private IPs, and non-http schemes.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme.lower() not in ("http", "https"):
        return False

    if parsed.username or parsed.password:
        return False

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False

    if hostname in {"localhost"} or hostname.endswith(".local"):
        return False

    def _is_public_ip(ip_raw: str) -> bool:
        try:
            ip_obj = ipaddress.ip_address(ip_raw)
        except ValueError:
            return False
        return not (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        )

    # Direct IP host: must be public and routable.
    try:
        ipaddress.ip_address(hostname)
        return _is_public_ip(hostname)
    except ValueError:
        pass

    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        resolved_ips = {
            info[4][0]
            for info in socket.getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        }
    except Exception:
        return False

    if not resolved_ips:
        return False

    # Reject if any DNS answer resolves to non-public IP ranges.
    for resolved_ip in resolved_ips:
        if not _is_public_ip(resolved_ip):
            return False

    return True
