import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import database
from fastapi import HTTPException, Request
from jose import JWTError, jwt
from navipod_config import settings
from passlib.context import CryptContext
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,31}$")


def is_valid_username(username: str) -> bool:
    return bool(USERNAME_RE.fullmatch((username or "").strip()))


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def get_user_by_username(db: Session, username: str):
    return db.query(database.User).filter(database.User.username == username).first()


def create_user_in_db(db: Session, username: str, password: str):
    username = (username or "").strip()
    if not is_valid_username(username):
        raise ValueError("Username must be 2-32 letters, numbers, underscores, or hyphens")
    hashed_password = get_password_hash(password)
    db_user = database.User(username=username, hashed_password=hashed_password)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_username_from_token(token: str):
    payload = get_token_payload(token)
    return payload.get("sub") if payload else None


def get_token_payload(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def _token_session_version(payload: dict) -> int:
    try:
        return int(payload.get("sv", 0))
    except (TypeError, ValueError):
        return -1


# --- ESTA ES LA FUNCIÓN QUE FALTABA Y QUE PIDE EL ROUTER DE RADIOS ---
def get_current_user(request: Request, db: Session):
    """
    Busca la cookie de sesión, extrae el usuario y lo valida contra la DB.
    Si algo falla, lanza un 401 para redirigir al login.
    """
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="No session")

    # BLACKLIST CHECK
    if is_token_blacklisted(db, token):
        raise HTTPException(status_code=401, detail="Session revoked")

    payload = get_token_payload(token)
    username = payload.get("sub") if payload else None
    if not username:
        raise HTTPException(status_code=401, detail="Invalid session")

    user = get_user_by_username(db, username)
    if (
        not user
        or not user.is_active
        or user.is_service_account
        or not is_valid_username(user.username)
        or _token_session_version(payload) != int(user.session_version or 0)
    ):
        raise HTTPException(status_code=401, detail="User account unavailable")

    return user


def verify_token(token: str, expected_username: str, db: Session = None) -> bool:
    # Optional DB check for blacklist (used in gateway)
    if db and is_token_blacklisted(db, token):
        return False

    payload = get_token_payload(token)
    username = payload.get("sub") if payload else None
    if username != expected_username:
        return False
    if db is None:
        return True
    user = get_user_by_username(db, username)
    return bool(
        user
        and user.is_active
        and not user.is_service_account
        and is_valid_username(user.username)
        and _token_session_version(payload) == int(user.session_version or 0)
    )


def blacklist_token(db: Session, token: str):
    """Revoca un token añadiéndolo a la blacklist"""
    if not token:
        return
    try:
        # Check if already blacklisted
        exists = db.query(database.TokenBlacklist).filter(database.TokenBlacklist.token == token).first()
        if not exists:
            revoked = database.TokenBlacklist(token=token)
            db.add(revoked)
            db.commit()
    except Exception as e:
        logger.warning("Failed to revoke token: %s", e)


def is_token_blacklisted(db: Session, token: str) -> bool:
    """Verifica si un token está en la blacklist"""
    if not token:
        return False
    exists = db.query(database.TokenBlacklist).filter(database.TokenBlacklist.token == token).first()
    return exists is not None


def prune_token_blacklist(max_age_hours: int = 48) -> int:
    """Delete blacklist rows older than max_age_hours. Tokens expire after
    ACCESS_TOKEN_EXPIRE_MINUTES (24h) anyway, so a row past 48h can never
    match a still-valid token — it is dead weight scanned on every request.
    Opens its own session: called from a background scheduler, never from
    a request handler."""
    # blacklisted_at is naive UTC (SQLite CURRENT_TIMESTAMP) — compare naive.
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=max_age_hours)
    db = database.SessionLocal()
    try:
        removed = (
            db.query(database.TokenBlacklist)
            .filter(database.TokenBlacklist.blacklisted_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
        return removed
    except Exception as e:
        db.rollback()
        logger.warning("Token blacklist prune failed: %s", e)
        return 0
    finally:
        db.close()


def is_password_strong(password: str) -> bool:
    password = (password or "").strip()
    if len(password) < 8:
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[0-9]", password):
        return False
    if not re.search(r"[^A-Za-z0-9]", password):
        return False
    return True
