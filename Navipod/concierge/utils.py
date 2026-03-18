import os
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

    if parsed.scheme not in ("http", "https"):
        return False
        
    hostname = parsed.hostname
    if not hostname:
        return False
        
    # Block Localhost
    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return False
        
    # Block Private/Cloud Metadata IPs (Basic Regex or String check)
    # 169.254.169.254 (AWS/Cloud Metadata)
    if hostname == "169.254.169.254":
        return False
    
    # Simple Private Range Checks (not exhaustive but covers 99%)
    if hostname.startswith("192.168."): return False
    if hostname.startswith("10."): return False
    if hostname.startswith("172.") and 16 <= int(hostname.split('.')[1]) <= 31: return False
    
    return True
