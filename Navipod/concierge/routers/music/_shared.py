"""
Shared dependencies for music sub-routers.
All sub-modules should import from here instead of duplicating.
"""
import os
import asyncio
import logging
import shutil
import subprocess
from fastapi import APIRouter, Request, Depends, Form, BackgroundTasks, Response
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
import database, auth, utils, downloader_service, manager
from pydantic import BaseModel
import mutagen
import httpx
import spotify_service
import youtube_service
from shared_templates import templates
from PIL import Image
import io
from pathlib import Path
from typing import Optional, List

# Pydantic models
from pydantic import BaseModel as PydanticBaseModel

from navipod_config import settings

# --- HTTP CLIENT (Shared) ---
# Use the global shared client for external API calls
from http_client import http_client
logger = logging.getLogger(__name__)


# --- RADIO GARDEN HEADERS ---
RG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Referer": "https://radio.garden/",
    "Accept": "application/json",
}


# --- DATABASE SESSION ---
def get_db():
    """Yields a database session. Use as FastAPI Depends()."""
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- USER HELPER ---
def get_current_user_safe(db: Session, request: Request):
    """Recupera el usuario o devuelve None si algo falla."""
    token = request.cookies.get("access_token")
    if not token:
        return None
    username = auth.get_username_from_token(token)
    if not username:
        return None
    return auth.get_user_by_username(db, username)


# --- BACKGROUND DOWNLOAD HELPER ---
async def run_download_in_background(job_id: int, user_id: int):
    """Runs a download job in background thread."""
    bg_db = database.SessionLocal()
    try:
        mgr = downloader_service.DownloadManager(bg_db, user_id)
        await mgr.process_download(job_id)
    except Exception as e:
        logger.warning("Background download task error: %s", e)
    finally:
        bg_db.close()


# --- PYDANTIC MODELS (Shared) ---
class DownloadRequest(BaseModel):
    url: str
    title: str = None
    artist: str = None
    album: str = None


class CreatePlaylistRequest(PydanticBaseModel):
    name: str


class PlaylistUpdateRequest(PydanticBaseModel):
    name: str


class AddToPlaylistRequest(PydanticBaseModel):
    track_id: int


# --- RATE LIMITER (Exposed for sub-routers) ---
# Import at endpoint level to avoid circular imports
def get_limiter():
    """Get limiter from limiter.py module."""
    from limiter import limiter
    return limiter
