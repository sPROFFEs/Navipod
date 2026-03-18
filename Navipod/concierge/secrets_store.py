import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from navipod_config import settings

ENC_PREFIX = "enc:v1:"


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.startswith(ENC_PREFIX):
        return cleaned
    token = _get_fernet().encrypt(cleaned.encode("utf-8")).decode("utf-8")
    return f"{ENC_PREFIX}{token}"



def decrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.startswith(ENC_PREFIX):
        return value
    token = value[len(ENC_PREFIX):]
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None
    except Exception:
        return None
