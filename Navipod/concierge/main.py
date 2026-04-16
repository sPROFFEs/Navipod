import logging
import socket
import os
import shutil
import re
import ipaddress
from urllib.parse import urljoin, urlparse

from fastapi import FastAPI, Request, Depends, Form, UploadFile, File, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
import httpx
from sqlalchemy.orm import Session
from pydantic import BaseModel
import asyncio
import reaper
import cache_maintenance
import operations_service
from routers import admin, user
from routers.music import router as music_router
import security
from navipod_config import settings
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

# Rate Limiting
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from limiter import limiter

# Imports locales
import manager
import auth
import database
import i18n
import track_identity
from contextvars import ContextVar

from shared_templates import templates
from http_client import http_client  # Moved to top level to avoid shutdown re-import issues


# Initialize rate limiter
# (limiter is imported from limiter.py)

app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- SECURITY MIDDLEWARE ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.all_allowed_hosts,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.add_middleware(
    TrustedHostMiddleware, 
    allowed_hosts=settings.all_allowed_hosts + ["*.localhost"] # Allow subdomains if needed
)
# ---------------------------

app.mount("/assets", StaticFiles(directory="assets"), name="assets")
app.mount("/static", StaticFiles(directory="assets"), name="static") # Alias for legacy paths
# templates = Jinja2Templates(directory="templates")  <-- Eliminado, usamos el compartido

RESERVED_GATEWAY_PREFIXES = {
    "admin",
    "api",
    "assets",
    "downloads",
    "favicon.ico",
    "health",
    "help",
    "index.html",
    "library",
    "login",
    "logout",
    "portal",
    "robots.txt",
    "search",
    "settings",
    "sitemap.xml",
    "static",
    "updater",
    "user",
}

# --- I18N SETUP ---
current_lang = ContextVar("current_lang", default="es")

def get_text_context(key: str):
    return i18n.get_text(key, current_lang.get())

templates.env.globals["_"] = get_text_context
templates.env.globals["domain"] = settings.DOMAIN

@app.middleware("http")
async def set_i18n_context(request: Request, call_next):
    lang = request.cookies.get("lang", "es")
    if lang not in i18n.SUPPORTED_LANGS: lang = i18n.DEFAULT_LANG
    token = current_lang.set(lang)
    response = await call_next(request)
    current_lang.reset(token)
    return response

# ...

@app.on_event("startup")
async def startup_event():
    # Force reload on startup to be sure
    i18n.load_translations()
    print(f"[I18N] Loaded languages: {list(i18n.translations.keys())}")
    applied_migrations = operations_service.apply_schema_migrations()
    if applied_migrations:
        print(f"[MIGRATIONS] Applied: {', '.join(applied_migrations)}")
    db = database.SessionLocal()
    try:
        refreshed_identities = track_identity.sync_track_identities(db)
        if refreshed_identities:
            print(f"[TRACK-IDENTITY] Synced {refreshed_identities} tracks.")
    finally:
        db.close()
    
    # Start Reaper Background Loop
    asyncio.create_task(reaper_scheduler())
    asyncio.create_task(cache_cleanup_scheduler())
    asyncio.create_task(operations_service.autobackup_scheduler())

async def reaper_scheduler():
    check_interval = settings.CHECK_INTERVAL_MINUTES
    print(f"[SCHEDULER] Starting Reaper every {check_interval} minutes.")
    while True:
        try:
            # Esperar primero, para no matar nada más arrancar
            await asyncio.sleep(check_interval * 60)
            
            print("[SCHEDULER] Running Reaper...")
            # Ejecutar en thread aparte para no bloquear el loop principal
            await asyncio.to_thread(reaper.reap_idle_containers)
        except Exception as e:
            print(f"[SCHEDULER-ERROR] {e}")
            await asyncio.sleep(60) # En caso de error, reintentar en 1 min


async def cache_cleanup_scheduler():
    print(f"[CACHE] Starting cache cleanup every {cache_maintenance.CACHE_CLEAN_INTERVAL_SECONDS // 3600} hours.")
    while True:
        try:
            removed = await asyncio.to_thread(cache_maintenance.purge_expired_cache_files)
            if removed:
                print(f"[CACHE] Removed {removed} expired cache files.")
            await asyncio.sleep(cache_maintenance.CACHE_CLEAN_INTERVAL_SECONDS)
        except Exception as e:
            print(f"[CACHE-ERROR] {e}")
            await asyncio.sleep(300)

# Load immediately on import too, just in case
i18n.load_translations()

# --- CLIENTE HTTP GLOBAL (Proxy Navidrome) ---
proxy_client = httpx.AsyncClient(
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
    timeout=httpx.Timeout(60.0, read=None)
)

RADIO_PROXY_ALLOWED_SCHEMES = {"http", "https"}
RADIO_PROXY_MAX_REDIRECTS = 5


def _is_public_ip_address(host: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(host)
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


def _validate_radio_proxy_url(url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL."

    if parsed.scheme.lower() not in RADIO_PROXY_ALLOWED_SCHEMES:
        return False, "Unsupported URL scheme."

    if not parsed.hostname:
        return False, "Missing target host."

    host = parsed.hostname.strip()
    lowered_host = host.lower()
    if lowered_host in {"localhost"}:
        return False, "Target host is not allowed."

    try:
        if _is_public_ip_address(host):
            return True, ""
    except Exception:
        return False, "Invalid target host."

    try:
        resolved_ips = {
            info[4][0]
            for info in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        }
    except socket.gaierror:
        return False, "Unable to resolve target host."
    except Exception:
        return False, "Failed to validate target host."

    if not resolved_ips:
        return False, "Unable to resolve target host."

    for resolved_ip in resolved_ips:
        if not _is_public_ip_address(resolved_ip):
            return False, "Target host resolved to a private or reserved address."

    return True, ""


async def _fetch_radio_proxy_response(url: str) -> httpx.Response:
    current_url = url
    for _ in range(RADIO_PROXY_MAX_REDIRECTS + 1):
        is_valid, error_message = _validate_radio_proxy_url(current_url)
        if not is_valid:
            raise ValueError(error_message)

        request = proxy_client.build_request("GET", current_url)
        response = await proxy_client.send(request, stream=True, follow_redirects=False)

        if response.status_code not in {301, 302, 303, 307, 308}:
            return response

        location = response.headers.get("location")
        if not location:
            await response.aclose()
            raise ValueError("Upstream redirect missing location header.")

        next_url = urljoin(str(response.url), location)
        await response.aclose()
        current_url = next_url

    raise ValueError("Too many upstream redirects.")


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.on_event("shutdown")
async def shutdown_event():
    await proxy_client.aclose()
    # Also close shared http_client if used
    await http_client.aclose()

@app.get("/api/proxy/radio")
async def general_stream_proxy(url: str, request: Request, db: Session = Depends(get_db)):
    """General proxy for any stream URL to bypass CORS."""
    user = auth.get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        rp_resp = await _fetch_radio_proxy_response(url)
        
        # Forward essential headers for audio streaming
        headers = {k: v for k, v in rp_resp.headers.items() 
                   if k.lower() in {"content-type", "accept-ranges", "content-length"}}
        
        return StreamingResponse(
            rp_resp.aiter_bytes(),
            status_code=rp_resp.status_code,
            headers=headers,
            background=BackgroundTask(rp_resp.aclose)
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"Stream proxy error: {str(e)}"}, status_code=502)

# --- CONNECT MUSIC ROUTER (LIBRARY & DOWNLOADS) ---
# This automatically adds: /downloads, /library, /api/downloads..., /api/library...
app.include_router(music_router)
app.include_router(admin.router)
app.include_router(user.router)
# ---------------------------------------------------------------

class UserCreate(BaseModel):
    username: str
    password: str

# --- AUTH & SYSTEM ROUTES ---

@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {
        "request": request, "error": request.query_params.get("error"), "next": request.query_params.get("next", "")
    })

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form(""), db: Session = Depends(get_db)):
    
    # 1. SHIELD: Is banned? (Thows exception and cuts here if yes)
    # Important: If this hits, FastAPI returns a 429 automatically.
    # But as it is an HTML form, sometimes it is better to capture it to show it nicely,
    # although HTTPException works well.
    try:
        security.check_brute_force(request) 
    except Exception as e:
        # Trick to show error in HTML instead of ugly JSON
        return RedirectResponse(f"/login?error={e.detail}", status_code=303)

    user = auth.get_user_by_username(db, username)
    
    # 2. VERIFICATION
    if not user or not auth.verify_password(password, user.hashed_password):
        # Register the hit
        is_blocked = security.register_failed_attempt(request)
        
        msg = "Invalid credentials"
        if is_blocked: 
            msg = "Temporarily blocked for security (15 min)."
        
        return RedirectResponse(f"/login?error={msg}", status_code=303)
    
    # 3. SUCCESS -> Clear criminal record
    security.clear_attempts(request)

    access_token = auth.create_access_token(data={"sub": user.username})
    redirect_url = "/portal"
    
    # Redirect to Admin if admin enters (optional, better to portal)
    if user.is_admin: 
        pass 

    if next and next != "None":
        clean_next = next.strip("/")
        redirect_url = f"https://{request.headers.get('host')}/{username}/{clean_next}"

    response = RedirectResponse(url=redirect_url, status_code=303)
    # SECURITY HARDENING: HttpOnly, configurable Secure flag, SameSite=Lax
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite='lax',
        max_age=86400
    )
    if user.is_admin:
        try:
            operations_service.queue_silent_update_refresh_if_stale(force=True)
        except Exception:
            pass
    return response

@app.get("/portal")
async def portal(request: Request, db: Session = Depends(get_db)):
    # Use robust auth checker that handles blacklist
    try:
        user = auth.get_current_user(request, db)
    except:
        return RedirectResponse("/login")
    
    username = user.username

    # Get Global Pool Status
    u_gb, l_gb, pct = manager.get_pool_status(db)

    return templates.TemplateResponse("app_shell.html", {
        "request": request, 
        "username": username,
        "is_admin": user.is_admin,
        "pool": {"used": u_gb, "limit": l_gb, "percent": pct}
    })
    
@app.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if token:
        # 1. Revoke Token (Blacklist)
        auth.blacklist_token(db, token)
        
        # 2. Stop User Container (Save Resources)
        username = auth.get_username_from_token(token)
        if username:
            manager.stop_user_container(username)

    res = RedirectResponse("/login", status_code=303)
    res.delete_cookie("access_token", path="/", httponly=True, secure=settings.COOKIE_SECURE, samesite='lax')
    return res

@app.get("/help")
async def help_page(request: Request, db: Session = Depends(get_db)):
    # Autenticación requerida
    token = request.cookies.get("access_token")
    if not token: return RedirectResponse("/login")
    username = auth.get_username_from_token(token)
    if not username: return RedirectResponse("/login")
    user = auth.get_user_by_username(db, username)
    # Pool Status
    u_gb, l_gb, pct = manager.get_pool_status(db)
    
    return templates.TemplateResponse("help.html", {
        "request": request, 
        "username": username,
        "is_admin": user.is_admin if user else False,
        "pool": {"used": u_gb, "limit": l_gb, "percent": pct}
    })

@app.get("/")
def home(request: Request):
    return RedirectResponse("/portal") if request.cookies.get("access_token") else RedirectResponse("/login")

@app.post("/admin/create_user")
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    auth.create_user_in_db(db, user.username, user.password)
    manager.provision_user_env(user.username)
    return {"status": "ok", "user": user.username}



@app.post("/settings")
async def change_password(request: Request, old_password: str = Form(...), new_password: str = Form(...), confirm_password: str = Form(...), db: Session = Depends(get_db)):
    # 1. Autenticación básica
    token = request.cookies.get("access_token")
    if not token: return RedirectResponse("/login")
    
    # 2. Validación de consistencia (Fail Fast)
    if new_password != confirm_password:
        return RedirectResponse("/settings?msg=Passwords do not match&type=error", status_code=303)
    
    # 3. Validación de complejidad (Fail Fast) - AHORRAS CPU AQUÍ
    if not is_password_strong(new_password):
        return RedirectResponse("/settings?msg=Weak password: use 8 characters, uppercase letters, numbers, and symbols&type=error", status_code=303)

    # 4. Operaciones pesadas (DB y Hash Verify)
    username = auth.get_username_from_token(token)
    user = auth.get_user_by_username(db, username)
    
    if not auth.verify_password(old_password, user.hashed_password):
        return RedirectResponse("/settings?msg=Current password is incorrect&type=error", status_code=303)
    
    # 5. Commit final
    user.hashed_password = auth.get_password_hash(new_password)
    db.commit()
    
    return RedirectResponse("/settings?msg=Password updated successfully&type=success", status_code=303)

@app.get("/settings/downloader")
async def settings_downloader_page(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token: return RedirectResponse("/login")
    username = auth.get_username_from_token(token)
    user = auth.get_user_by_username(db, username)
    
    if not user.download_settings:
        user.download_settings = database.DownloadSettings(user_id=user.id)
        db.add(user.download_settings)
        db.commit()
    
    return templates.TemplateResponse("settings_downloader.html", {
        "request": request, 
        "username": username, 
        "is_admin": user.is_admin,
        "settings": user.download_settings
    })

@app.post("/api/settings/cookies")
async def upload_cookies(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    username = auth.get_username_from_token(token)
    
    config_dir = f"/saas-data/users/{username}/config"
    os.makedirs(config_dir, exist_ok=True)
    file_path = f"{config_dir}/cookies.txt"
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    user = auth.get_user_by_username(db, username)
    if not user.download_settings:
        user.download_settings = database.DownloadSettings(user_id=user.id)
    user.download_settings.youtube_cookies_path = file_path
    db.commit()
    return RedirectResponse("/settings?msg=Cookies updated successfully", status_code=303)

@app.post("/api/settings/spotify")
async def save_spotify_settings(
    request: Request,
    spotify_client_id: str = Form(...),
    spotify_client_secret: str = Form(...),
    db: Session = Depends(get_db)
):
    token = request.cookies.get("access_token")
    if not token: return RedirectResponse("/login")
    username = auth.get_username_from_token(token)
    user = auth.get_user_by_username(db, username)
    
    if not user.download_settings:
        user.download_settings = database.DownloadSettings(user_id=user.id)
    
    # Guardamos las credenciales
    user.download_settings.spotify_client_id = spotify_client_id
    user.download_settings.spotify_client_secret = spotify_client_secret
    db.commit()
    
    return RedirectResponse("/settings/downloader?msg=Spotify configured successfully", status_code=303)

@app.get("/set-language/{lang}")
async def set_language(lang: str, request: Request):
    if lang not in i18n.SUPPORTED_LANGS: lang = i18n.DEFAULT_LANG
    
    # Redirect back to where they came from, or home
    referer = request.headers.get("referer", "/portal")
    
    response = RedirectResponse(referer, status_code=303)
    response.set_cookie(
        key="lang",
        value=lang,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite='lax',
        max_age=31536000
    ) # 1 year
    return response

# --- USER API ENDPOINTS (JSON) ---

from pydantic import BaseModel as PydanticBaseModel

class PasswordChangeRequest(PydanticBaseModel):
    new_password: str

class UserSettingsRequest(PydanticBaseModel):
    spotify_client_id: str = None
    spotify_client_secret: str = None
    lastfm_api_key: str = None
    lastfm_shared_secret: str = None
    youtube_cookies: str = None
    metadata_preferences: list[str] | None = None
    audio_quality: str = "320"

@app.post("/api/user/password")
async def api_change_password(req: PasswordChangeRequest, request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token: return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    username = auth.get_username_from_token(token)
    user = auth.get_user_by_username(db, username)
    if not user: return JSONResponse({"error": "User not found"}, status_code=404)
    
    # Validate password strength
    if len(req.new_password) < 8:
        return JSONResponse({"error": "Password must be at least 8 characters"}, status_code=400)
    
    user.hashed_password = auth.get_password_hash(req.new_password)
    db.commit()
    
    return JSONResponse({"status": "updated"})

@app.post("/api/user/settings")
async def api_save_settings(req: UserSettingsRequest, request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token: return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    username = auth.get_username_from_token(token)
    user = auth.get_user_by_username(db, username)
    if not user: return JSONResponse({"error": "User not found"}, status_code=404)
    
    if not user.download_settings:
        user.download_settings = database.DownloadSettings(user_id=user.id)
        db.add(user.download_settings)
    
    if req.spotify_client_id is not None:
        user.download_settings.spotify_client_id = req.spotify_client_id
    if req.spotify_client_secret is not None:
        user.download_settings.spotify_client_secret = req.spotify_client_secret
    if req.lastfm_api_key is not None:
        user.download_settings.lastfm_api_key = req.lastfm_api_key
    if req.lastfm_shared_secret is not None:
        user.download_settings.lastfm_shared_secret = req.lastfm_shared_secret
    if req.youtube_cookies is not None:
        user.download_settings.youtube_cookies = req.youtube_cookies
    if req.metadata_preferences is not None:
        import json
        user.download_settings.metadata_preferences = json.dumps(req.metadata_preferences)
    if req.audio_quality:
        user.download_settings.audio_quality = req.audio_quality
    
    db.commit()
    
    return JSONResponse({"status": "saved"})

# --- PROXY GATEWAY (SIEMPRE EL ÚLTIMO) ---
@app.api_route("/{username}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"])
async def gateway(username: str, path: str, request: Request, db: Session = Depends(get_db)):
    # Keep internal/static/admin routes out of the per-user Navidrome gateway,
    # even when traffic bypasses nginx and reaches concierge directly.
    if (username or "").lower() in RESERVED_GATEWAY_PREFIXES:
        return JSONResponse({"error": "Not found"}, status_code=404)
    
    # 1. DEBUG LOGS FOR SUBSONIC (Only Errors/Warns)
    # k = request.method + " " + path
    
    # 2. AUTH RÁPIDA (Sin DB)
    authorized = False
    
    # Check cookie token
    token = request.cookies.get("access_token")
    if token and auth.verify_token(token, username):
        authorized = True
    
    # 3. AUTH LENTA (Legacy / Subsonic API)
    if not authorized:
        user_db = auth.get_user_by_username(db, username)
        if not user_db: 
            print(f"[AUTH-ERROR] User '{username}' not found in DB")
            return JSONResponse({"error": "User not found"}, status_code=404)
        
        # Check Subsonic Params (GET or POST)
        if "rest/" in path or "view.view" in path:
            params = dict(request.query_params)
            
            # If POST, check form body for credentials too
            if request.method == "POST" and (not params.get('u') or not params.get('p')):
                try:
                    form_data = await request.form()
                    params.update(form_data)
                except:
                    pass

            u = params.get("u")
            p = params.get("p")
            
            if u == username and p:
                # Handle hex-encoded passwords (enc:)
                if p.startswith("enc:"): 
                    try: 
                        hex_str = p[4:]
                        p = bytes.fromhex(hex_str).decode("utf-8")
                    except Exception as ex: 
                        print(f"[AUTH-ERROR] Failed to decode 'enc' password from '{hex_str}': {ex}")
                        pass
                
                if auth.verify_password(p, user_db.hashed_password): 
                    authorized = True
                else:
                    print(f"[AUTH-ERROR] Password Invalid for user '{username}' (Client: {params.get('c')})")
            else:
                 if not u or not p:
                     # Silent fail for missing credentials is ok, but log if it looks like a login attempt
                     if "ping" in path: print(f"[AUTH-WARN] Missing credentials for {path}")

    if not authorized:
        if "rest/" in path: 
            print(f"[AUTH-FAILED] Returning 403. Path: {path} | User: {username}")
            return JSONResponse({"error": {"code":40,"message":"Auth failed"}}, status_code=403)
        return RedirectResponse(f"/login?next={username}/{path}")

    # --- 3.5 REGISTRAR ACTIVIDAD ---
    from starlette.background import BackgroundTasks as StarletteBackgroundTasks
    bg = StarletteBackgroundTasks()
    
    def update_last_access(uname):
        try:
            from database import SessionLocal
            with SessionLocal() as session:
                from database import User
                from sqlalchemy import func
                session.query(User).filter(User.username == uname).update({User.last_access: func.now()})
                session.commit()
        except: pass

    bg.add_task(update_last_access, username)

    # 3. IP CACHÉ
    try:
        target_ip = manager.get_or_spawn_container(username)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    # 4. PROXY
    target_url = f"http://{target_ip}:4533/{username}/{path}"
    if request.query_params: target_url += f"?{str(request.query_params)}"

    # Whitelist de cabeceras seguras (NUNCA pasar cookies o IPs externas)
    safe_keys = {"user-agent", "accept", "accept-encoding", "accept-language", "range", "content-type"}
    req_headers = {k: v for k, v in request.headers.items() if k.lower() in safe_keys}
    
    # Inyectar la identidad de forma limpia
    req_headers["x-navidrome-user"] = username
    # print(f"[DEBUG-PROXY] Clean Headers for {username}: {req_headers}")

    try:
        body = await request.body()
        rp_req = proxy_client.build_request(request.method, target_url, headers=req_headers, content=body)
        rp_resp = await proxy_client.send(rp_req, stream=True)
        
        # Agregamos el cierre del proxy al grupo de tareas de fondo
        bg.add_task(rp_resp.aclose)

        headers = {k: v for k, v in rp_resp.headers.items() if k.lower() not in {"content-encoding", "content-length", "transfer-encoding", "connection"}}

        return StreamingResponse(
            rp_resp.aiter_bytes(),
            status_code=rp_resp.status_code,
            headers=headers,
            background=bg
        )
    except Exception as e:
        return JSONResponse({"error": f"Proxy error: {str(e)}"}, status_code=502)
