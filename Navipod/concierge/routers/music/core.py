"""
Core utilities and shared dependencies for music routers.
"""
import os
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from PIL import Image
import io
import httpx

import database
import auth
import utils
import manager
from shared_templates import templates


router = APIRouter()


# --- SHARED DEPENDENCIES ---

def get_db():
    """Database session dependency"""
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user_safe(db: Session, request: Request):
    """Retrieves user or returns None if something fails"""
    token = request.cookies.get("access_token")
    if not token:
        return None
    username = auth.get_username_from_token(token)
    if not username:
        return None
    return auth.get_user_by_username(db, username)


# --- UTILITY ENDPOINTS ---

@router.get("/api/proxy-image")
async def proxy_image(url: str):
    """Proxy and optimize external images"""
    if not utils.is_safe_url(url):
        return JSONResponse({"error": "URL not allowed"}, status_code=400)

    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        
    # Process image with Pillow
    img = Image.open(io.BytesIO(resp.content))
    img.thumbnail((300, 300))  # Resize to card size
    
    img_io = io.BytesIO()
    img.save(img_io, format="WEBP", quality=80)  # Convert to WebP
    img_io.seek(0)
    
    return Response(
        content=img_io.getvalue(), 
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=604800"}
    )


# --- HTML VIEWS ---

@router.get("/downloads")
async def downloads_page(request: Request, db: Session = Depends(get_db)):
    """Downloads management page"""
    user = get_current_user_safe(db, request)
    if not user:
        return RedirectResponse("/login")

    # AUTO-SCANNER: Sync physical folders with DB
    music_root = f"/saas-data/users/{user.username}/music"
    if os.path.exists(music_root):
        physical_folders = [f for f in os.listdir(music_root) if os.path.isdir(os.path.join(music_root, f))]
        db_playlists = db.query(database.Playlist).filter(database.Playlist.owner_id == user.id).all()
        db_names = [p.name for p in db_playlists]
        
        changes = False
        for folder in physical_folders:
            if folder not in db_names:
                new_pl = database.Playlist(owner_id=user.id, name=folder)
                db.add(new_pl)
                changes = True
        if changes:
            db.commit()

    playlists = db.query(database.Playlist).filter(database.Playlist.owner_id == user.id).all()
    
    # Global Quota
    u_gb, l_gb, pct = manager.get_pool_status(db)

    return templates.TemplateResponse("downloads.html", {
        "request": request, 
        "username": user.username, 
        "playlists": playlists,
        "pool": {"used": u_gb, "limit": l_gb, "percent": pct}
    })


@router.get("/library")
async def library_page(request: Request, db: Session = Depends(get_db)):
    """Library page"""
    user = get_current_user_safe(db, request)
    if not user:
        return RedirectResponse("/login")
    
    # Global Quota
    u_gb, l_gb, pct = manager.get_pool_status(db)
    
    return templates.TemplateResponse("library.html", {
        "request": request, 
        "username": user.username,
        "pool": {"used": u_gb, "limit": l_gb, "percent": pct}
    })


@router.get("/search")
async def search_page(request: Request, db: Session = Depends(get_db)):
    """Search page"""
    user = get_current_user_safe(db, request)
    if not user:
        return RedirectResponse("/login")
    
    # Need playlists for the modal dropdown
    playlists = db.query(database.Playlist).filter(database.Playlist.owner_id == user.id).all()
    
    # Pool Status
    u_gb, l_gb, pct = manager.get_pool_status(db)

    return templates.TemplateResponse("search.html", {
        "request": request, 
        "username": user.username, 
        "playlists": playlists,
        "pool": {"used": u_gb, "limit": l_gb, "percent": pct}
    })
