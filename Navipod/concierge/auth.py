from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from fastapi import Request, HTTPException # <--- IMPORTANTE
import database
import re
# CONFIGURACIÓN
from navipod_config import settings

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def get_user_by_username(db: Session, username: str):
    return db.query(database.User).filter(database.User.username == username).first()

def create_user_in_db(db: Session, username: str, password: str):
    hashed_password = get_password_hash(password)
    db_user = database.User(username=username, hashed_password=hashed_password)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_username_from_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

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

    username = get_username_from_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid session")
    
    user = get_user_by_username(db, username)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
        
    return user

def verify_token(token: str, expected_username: str, db: Session = None) -> bool:
    # Optional DB check for blacklist (used in gateway)
    if db and is_token_blacklisted(db, token):
        return False
        
    return get_username_from_token(token) == expected_username

def blacklist_token(db: Session, token: str):
    """Revoca un token añadiéndolo a la blacklist"""
    if not token: return
    try:
        # Check if already blacklisted
        exists = db.query(database.TokenBlacklist).filter(database.TokenBlacklist.token == token).first()
        if not exists:
            revoked = database.TokenBlacklist(token=token)
            db.add(revoked)
            db.commit()
    except Exception as e:
        print(f"[AUTH-ERROR] Failed to revoke token: {e}")

def is_token_blacklisted(db: Session, token: str) -> bool:
    """Verifica si un token está en la blacklist"""
    if not token: return False
    exists = db.query(database.TokenBlacklist).filter(database.TokenBlacklist.token == token).first()
    return exists is not None

def is_password_strong(password: str) -> bool:
    password = (password or "").strip()
    if len(password) < 8: return False
    if not re.search(r"[a-z]", password): return False
    if not re.search(r"[A-Z]", password): return False
    if not re.search(r"[0-9]", password): return False
    if not re.search(r"[^A-Za-z0-9]", password): return False
    return True
