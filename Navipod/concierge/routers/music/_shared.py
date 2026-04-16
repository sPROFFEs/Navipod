"""
Shared dependencies for music sub-routers.
All sub-modules should import from here instead of duplicating.
"""

import asyncio
import io
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

import auth
import database
import downloader_service
import httpx
import manager
import mutagen
import spotify_service
import utils
import youtube_service
from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

# --- HTTP CLIENT (Shared) ---
# Use the global shared client for external API calls
from http_client import http_client
from navipod_config import settings
from PIL import Image
from pydantic import BaseModel

# Pydantic models
from pydantic import BaseModel as PydanticBaseModel
from shared_templates import templates
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

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
