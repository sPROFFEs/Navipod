import json
import logging
import os
import shutil

import auth
import database
import spotify_service
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from lastfm_service import lastfm_service
from secrets_store import ENC_PREFIX
from shared_templates import templates
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user")

DEFAULT_METADATA_PREFERENCES = ["spotify", "lastfm", "musicbrainz"]
ALLOWED_METADATA_PROVIDERS = set(DEFAULT_METADATA_PREFERENCES)


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token:
        return None
    username = auth.get_username_from_token(token)
    return auth.get_user_by_username(db, username)


def ensure_download_settings(db: Session, user: database.User) -> database.DownloadSettings:
    settings = db.query(database.DownloadSettings).filter(database.DownloadSettings.user_id == user.id).first()
    if not settings:
        settings = database.DownloadSettings(user_id=user.id)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    _normalize_secret_storage(db, settings)
    return settings


def _normalize_secret_storage(db: Session, settings: database.DownloadSettings | None) -> None:
    if not settings:
        return

    changed = False
    fields = [
        ("_spotify_client_id", "spotify_client_id"),
        ("_spotify_client_secret", "spotify_client_secret"),
        ("_lastfm_api_key", "lastfm_api_key"),
        ("_lastfm_shared_secret", "lastfm_shared_secret"),
        ("_youtube_cookies", "youtube_cookies"),
    ]

    for raw_attr, public_attr in fields:
        raw_value = getattr(settings, raw_attr, None)
        if raw_value and not str(raw_value).startswith(ENC_PREFIX):
            setattr(settings, public_attr, getattr(settings, public_attr))
            changed = True

    if changed:
        db.commit()


def parse_metadata_preferences(raw_value: str) -> str:
    if not raw_value:
        return json.dumps(DEFAULT_METADATA_PREFERENCES)

    try:
        loaded = json.loads(raw_value)
        if isinstance(loaded, list):
            normalized = [str(v).strip().lower() for v in loaded if str(v).strip()]
            return json.dumps(normalized or DEFAULT_METADATA_PREFERENCES)
    except Exception:
        pass

    normalized = [v.strip().lower() for v in raw_value.split(",") if v.strip()]
    return json.dumps(normalized or DEFAULT_METADATA_PREFERENCES)


def build_metadata_preferences(
    priority_1: str | None,
    priority_2: str | None,
    priority_3: str | None,
    raw_value: str | None,
) -> str:
    ordered = []
    for value in [priority_1, priority_2, priority_3]:
        if not value:
            continue
        provider = value.strip().lower()
        if provider in ALLOWED_METADATA_PROVIDERS and provider not in ordered:
            ordered.append(provider)

    for provider in DEFAULT_METADATA_PREFERENCES:
        if provider not in ordered:
            ordered.append(provider)

    if ordered:
        return json.dumps(ordered)

    return parse_metadata_preferences(raw_value or "")


@router.get("/settings")
async def user_settings(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")

    # Get Download Settings
    dl_settings = db.query(database.DownloadSettings).filter(database.DownloadSettings.user_id == user.id).first()
    _normalize_secret_storage(db, dl_settings)

    return templates.TemplateResponse(
        "user_settings.html",
        {
            "request": request,
            "user": user,
            "is_admin": user.is_admin,
            "username": user.username,
            "dl_settings": dl_settings,
        },
    )


@router.post("/update-api-keys")
async def update_api_keys(
    request: Request,
    spotify_client_id: str = Form(None),
    spotify_client_secret: str = Form(None),
    lastfm_api_key: str = Form(None),
    lastfm_shared_secret: str = Form(None),
    metadata_priority_1: str = Form(None),
    metadata_priority_2: str = Form(None),
    metadata_priority_3: str = Form(None),
    metadata_preferences: str = Form(None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")

    settings = ensure_download_settings(db, user)

    settings.spotify_client_id = spotify_client_id
    settings.spotify_client_secret = spotify_client_secret
    settings.lastfm_api_key = lastfm_api_key
    settings.lastfm_shared_secret = lastfm_shared_secret
    settings.metadata_preferences = build_metadata_preferences(
        metadata_priority_1,
        metadata_priority_2,
        metadata_priority_3,
        metadata_preferences,
    )

    db.commit()

    return templates.TemplateResponse(
        "user_settings.html",
        {
            "request": request,
            "user": user,
            "success": "Integrations updated",
            "is_admin": user.is_admin,
            "username": user.username,
            "dl_settings": settings,
        },
    )


@router.post("/validate-api-keys")
async def validate_api_keys(
    request: Request,
    spotify_client_id: str = Form(None),
    spotify_client_secret: str = Form(None),
    lastfm_api_key: str = Form(None),
    lastfm_shared_secret: str = Form(None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")

    settings = ensure_download_settings(db, user)
    messages = []

    if spotify_client_id and spotify_client_secret:
        ok = await spotify_service.spotify_service.validate_credentials(spotify_client_id, spotify_client_secret)
        messages.append("Spotify OK" if ok else "Spotify failed (401/403 or invalid credentials)")
    else:
        messages.append("Spotify skipped (missing credentials)")

    if lastfm_api_key:
        ok = await lastfm_service.validate_api_key(lastfm_api_key)
        messages.append("Last.fm OK" if ok else "Last.fm failed (invalid/expired API key)")
    else:
        messages.append("Last.fm skipped (missing API key)")

    if lastfm_shared_secret:
        messages.append("Last.fm shared secret saved (used only for signed/user-write methods)")

    return templates.TemplateResponse(
        "user_settings.html",
        {
            "request": request,
            "user": user,
            "success": " | ".join(messages),
            "is_admin": user.is_admin,
            "username": user.username,
            "dl_settings": settings,
        },
    )


@router.post("/upload-cookies")
async def upload_cookies(request: Request, cookies_file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    settings = ensure_download_settings(db, user)

    try:
        # Save file securely
        user_dir = f"/saas-data/users/{user.username}"
        os.makedirs(user_dir, exist_ok=True)
        file_path = f"{user_dir}/cookies.txt"

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(cookies_file.file, buffer)

        # Update DB
        settings.youtube_cookies_path = file_path
        settings.youtube_cookies = None
        db.commit()

        return templates.TemplateResponse(
            "user_settings.html",
            {
                "request": request,
                "user": user,
                "success": "Cookies uploaded successfully",
                "is_admin": user.is_admin,
                "username": user.username,
                "dl_settings": settings,
            },
        )
    except Exception as e:
        return templates.TemplateResponse(
            "user_settings.html",
            {
                "request": request,
                "user": user,
                "error": f"Upload failed: {str(e)}",
                "is_admin": user.is_admin,
                "username": user.username,
                "dl_settings": settings,
            },
        )


@router.post("/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")

    if new_password != confirm_password:
        return templates.TemplateResponse(
            "user_settings.html",
            {
                "request": request,
                "user": user,
                "error": "Passwords do not match",
                "is_admin": user.is_admin,
                "username": user.username,
            },
        )

    if not auth.verify_password(current_password, user.hashed_password):
        return templates.TemplateResponse(
            "user_settings.html",
            {
                "request": request,
                "user": user,
                "error": "Incorrect current password",
                "is_admin": user.is_admin,
                "username": user.username,
            },
        )

    # Update password
    user.hashed_password = auth.get_password_hash(new_password)
    db.commit()

    return templates.TemplateResponse(
        "user_settings.html",
        {
            "request": request,
            "user": user,
            "success": "Password updated successfully",
            "is_admin": user.is_admin,
            "username": user.username,
        },
    )


# --- PROFILE PICTURE / AVATAR ---
import hashlib
import io
import uuid

from fastapi.responses import FileResponse, Response
from PIL import Image

# Allowed image types with magic bytes for security
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/png": [b"\x89PNG"],
    "image/webp": [b"RIFF", b"WEBP"],
    "image/gif": [b"GIF87a", b"GIF89a"],
}
MAX_AVATAR_SIZE = 5 * 1024 * 1024  # 5MB max
AVATAR_OUTPUT_SIZE = (256, 256)  # Resize to this for storage efficiency


def validate_image_file(file_content: bytes, content_type: str) -> bool:
    """Validate image using magic bytes (file signature)"""
    if content_type not in ALLOWED_IMAGE_TYPES:
        return False

    magic_patterns = ALLOWED_IMAGE_TYPES[content_type]
    for pattern in magic_patterns:
        if file_content[: len(pattern)] == pattern:
            return True
    return False


@router.post("/upload-avatar")
async def upload_avatar(request: Request, avatar_file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Secure avatar upload with exhaustive validation"""
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login")

    dl_settings = db.query(database.DownloadSettings).filter(database.DownloadSettings.user_id == user.id).first()

    try:
        # Read file content
        content = await avatar_file.read()

        # Size check
        if len(content) > MAX_AVATAR_SIZE:
            return templates.TemplateResponse(
                "user_settings.html",
                {
                    "request": request,
                    "user": user,
                    "error": f"Image too large. Max size: {MAX_AVATAR_SIZE // (1024 * 1024)}MB",
                    "is_admin": user.is_admin,
                    "username": user.username,
                    "dl_settings": dl_settings,
                },
            )

        # Extension check
        filename = avatar_file.filename or ""
        ext = filename.lower().split(".")[-1] if "." in filename else ""
        if ext not in ["jpg", "jpeg", "png", "webp", "gif"]:
            return templates.TemplateResponse(
                "user_settings.html",
                {
                    "request": request,
                    "user": user,
                    "error": "Invalid file type. Allowed: JPG, PNG, WEBP, GIF",
                    "is_admin": user.is_admin,
                    "username": user.username,
                    "dl_settings": dl_settings,
                },
            )

        # Content-Type check
        content_type = avatar_file.content_type or ""
        if content_type not in ALLOWED_IMAGE_TYPES:
            return templates.TemplateResponse(
                "user_settings.html",
                {
                    "request": request,
                    "user": user,
                    "error": "Invalid content type",
                    "is_admin": user.is_admin,
                    "username": user.username,
                    "dl_settings": dl_settings,
                },
            )

        # Magic bytes validation
        if not validate_image_file(content, content_type):
            return templates.TemplateResponse(
                "user_settings.html",
                {
                    "request": request,
                    "user": user,
                    "error": "File content does not match declared type",
                    "is_admin": user.is_admin,
                    "username": user.username,
                    "dl_settings": dl_settings,
                },
            )

        # Process and resize image with Pillow (also validates it's a real image)
        try:
            img = Image.open(io.BytesIO(content))
            img = img.convert("RGB")  # Normalize to RGB
            img.thumbnail(AVATAR_OUTPUT_SIZE, Image.Resampling.LANCZOS)

            # Generate unique filename
            unique_id = uuid.uuid4().hex[:8]
            avatar_filename = f"avatar_{user.id}_{unique_id}.webp"

            # Save to user directory
            user_dir = f"/saas-data/users/{user.username}"
            os.makedirs(user_dir, exist_ok=True)
            avatar_path = f"{user_dir}/{avatar_filename}"

            # Remove old avatar if exists
            if user.avatar_path and os.path.exists(user.avatar_path):
                try:
                    os.remove(user.avatar_path)
                except:
                    pass

            # Save as WebP for optimal size
            img.save(avatar_path, "WEBP", quality=85)

        except Exception:
            return templates.TemplateResponse(
                "user_settings.html",
                {
                    "request": request,
                    "user": user,
                    "error": "Failed to process image: Invalid or corrupt file",
                    "is_admin": user.is_admin,
                    "username": user.username,
                    "dl_settings": dl_settings,
                },
            )

        # Update DB
        user.avatar_path = avatar_path
        db.commit()

        return templates.TemplateResponse(
            "user_settings.html",
            {
                "request": request,
                "user": user,
                "success": "Avatar updated successfully!",
                "is_admin": user.is_admin,
                "username": user.username,
                "dl_settings": dl_settings,
            },
        )

    except Exception as e:
        logger.warning("Avatar upload error: %s", e)
        return templates.TemplateResponse(
            "user_settings.html",
            {
                "request": request,
                "user": user,
                "error": f"Upload failed: {str(e)}",
                "is_admin": user.is_admin,
                "username": user.username,
                "dl_settings": dl_settings,
            },
        )


@router.get("/avatar/{username}")
async def get_avatar(username: str, db: Session = Depends(get_db)):
    """Serve user avatar or default"""
    user = db.query(database.User).filter(database.User.username == username).first()

    if user and user.avatar_path and os.path.exists(user.avatar_path):
        return FileResponse(
            user.avatar_path,
            media_type="image/webp",
            headers={"Cache-Control": "public, max-age=3600"},  # Cache 1 hour
        )

    # Return default avatar
    default_avatar = "/saas-data/default_avatar.webp"
    if os.path.exists(default_avatar):
        return FileResponse(default_avatar, media_type="image/webp")

    # Fallback: Generate a simple colored avatar
    from PIL import Image, ImageDraw

    # Use username hash for consistent color
    color_hash = int(hashlib.md5(username.encode()).hexdigest()[:6], 16)
    r = (color_hash >> 16) & 0xFF
    g = (color_hash >> 8) & 0xFF
    b = color_hash & 0xFF

    img = Image.new("RGB", (128, 128), (r, g, b))
    draw = ImageDraw.Draw(img)

    # Draw first letter
    letter = username[0].upper() if username else "?"
    draw.text((45, 30), letter, fill="white")

    img_io = io.BytesIO()
    img.save(img_io, format="WEBP", quality=80)
    img_io.seek(0)

    return Response(
        content=img_io.getvalue(), media_type="image/webp", headers={"Cache-Control": "public, max-age=3600"}
    )
