"""
Radio Garden integration endpoints.
"""
import logging
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
import httpx

import database
import manager
from shared_templates import templates

from .core import get_db, get_current_user_safe


router = APIRouter()
logger = logging.getLogger(__name__)


# Radio Garden API headers
RG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Referer": "https://radio.garden/",
    "Accept": "application/json",
}


async def fetch_saved_radios_for_user(user):
    target_ip = manager.get_or_spawn_container(user.username)
    url = f"http://{target_ip}:4533/{user.username}/rest/getInternetRadioStations"
    params = {
        "u": user.username,
        "p": "enc:000000",
        "v": "1.16.1",
        "c": "navipod-concierge",
        "f": "json"
    }
    headers = {"x-navidrome-user": user.username}

    async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
        resp = await client.get(url, params=params, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Navidrome error: {resp.status_code}")

        data = resp.json()
        stations = data.get("subsonic-response", {}).get("internetRadioStations", {}).get("internetRadioStation", [])
        if isinstance(stations, dict):
            stations = [stations]
        return stations


# --- HTML VIEW ---

@router.get("/radio")
async def radio_page(request: Request, db: Session = Depends(get_db)):
    """Radio browser page"""
    user = get_current_user_safe(db, request)
    if not user:
        return RedirectResponse("/login")
    
    # Pool Status
    u_gb, l_gb, pct = manager.get_pool_status(db)
    
    return templates.TemplateResponse("radio.html", {
        "request": request, 
        "username": user.username,
        "pool": {"used": u_gb, "limit": l_gb, "percent": pct}
    })


# --- RADIO GARDEN API PROXY ---

@router.get("/api/radio/browse")
async def browse_radio_garden():
    """Get recommended radio playlists from Radio Garden"""
    url = "https://radio.garden/api/ara/content/browse"
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers=RG_HEADERS, timeout=10)
            if resp.status_code != 200:
                return []
            content = resp.json().get("data", {}).get("content", [])
            # Extract playlist items with their URLs
            return [item for item in content if item["type"] == "playlist-excerpt"]
        except:
            return []


@router.get("/api/radio/playlist/{playlist_path:path}")
async def get_playlist_content(playlist_path: str):
    """
    Get playlist content from Radio Garden.
    Example path: playlist/rain-and-tears/5aeJ27yR
    """
    # Build URL from the frontend path
    url = f"https://radio.garden/api/ara/content/{playlist_path.lstrip('/')}"
    
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers=RG_HEADERS, timeout=10)
            if resp.status_code != 200:
                logger.warning("Radio Garden playlist fetch failed with status %s for %s", resp.status_code, url)
                return []

            data = resp.json().get("data", {}).get("content", [])
            # Find the section with items
            for section in data:
                if "items" in section and isinstance(section["items"], list):
                    items = section["items"]
                    if items:
                        logger.info("Sending %s Radio Garden playlist items", len(items))
                        return items
            
            logger.warning("No Radio Garden playlist items found")
            return []
        except Exception as e:
            logger.warning("Radio Garden playlist fetch failed: %s", e)
            return []


@router.get("/api/radio/search")
async def search_radio_garden(q: str):
    """Search radio stations on Radio Garden"""
    url = f"https://radio.garden/api/search?q={q}"
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers=RG_HEADERS)
            return resp.json().get("hits", {}).get("hits", [])
        except:
            return []


@router.get("/api/radio/place/{place_id}")
async def get_place_radios(place_id: str):
    """Get radio stations for a specific location"""
    url = f"https://radio.garden/api/ara/content/page/{place_id}"
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers=RG_HEADERS)
            sections = resp.json().get("data", {}).get("content", [])
            for s in sections:
                if s.get("itemsType") == "channel":
                    return s.get("items", [])
            return []
        except:
            return []


@router.post("/api/radio/inject")
async def inject_radio(request: Request, channel_id: str = Form(...), name: str = Form(...), db: Session = Depends(get_db)):
    """Inject radio station into Navidrome"""
    # 1. Verify authentication
    user = get_current_user_safe(db, request)
    if not user: 
        return JSONResponse({"error": "Unauthorized - No session found"}, status_code=401)

    # 2. Resolve the final stream URL (capture Location header from 302)
    listen_url = f"https://radio.garden/api/ara/content/listen/{channel_id}/channel.mp3"
    
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            # Use stream=True to capture final URL after redirects without downloading infinite audio
            async with client.stream("GET", listen_url, headers=RG_HEADERS) as resp:
                if resp.status_code != 200:
                    logger.warning("Radio stream resolve failed with status %s for channel %s", resp.status_code, channel_id)
                    return JSONResponse(
                        {"error": f"Could not resolve radio (Error {resp.status_code} from Radio Garden)"}, 
                        status_code=400
                    )
                
                real_stream_url = str(resp.url)
                logger.info("Radio stream URL resolved for channel %s", channel_id)
            
            # 3. Inject into Navidrome via gateway proxy
            gateway_url = f"http://localhost:8000/{user.username}/rest/createInternetRadioStation"
            
            # Subsonic API parameters (v1.16.1)
            params = {
                "v": "1.16.1",
                "c": "navipod-concierge",
                "f": "json",
                "streamUrl": real_stream_url,
                "name": name,
                "homepageUrl": "https://radio.garden"
            }
            
            logger.info("Calling Navidrome radio gateway for user %s", user.username)
            
            # Pass auth token for gateway authorization
            cookies = {"access_token": request.cookies.get("access_token")}
            
            inject_resp = await client.get(gateway_url, params=params, cookies=cookies)
            
            logger.info("Navidrome radio gateway status: %s", inject_resp.status_code)
            
            # Parse JSON response correctly
            try:
                result = inject_resp.json()
                
                # Subsonic API returns {"subsonic-response": {"status": "ok", ...}}
                if result.get("subsonic-response", {}).get("status") == "ok":
                    logger.info("Radio added successfully: %s", name)
                    return JSONResponse({"status": "success", "stream": real_stream_url})
                else:
                    error_msg = result.get("subsonic-response", {}).get("error", {}).get("message", "Unknown error")
                    logger.warning("Navidrome radio inject error: %s", error_msg)
                    return JSONResponse({"error": f"Navidrome API error: {error_msg}"}, status_code=500)
            except Exception as e:
                logger.warning("Navidrome radio response parse error: %s", e)
                return JSONResponse({"error": f"Invalid response from Navidrome: {str(e)}"}, status_code=500)
                
    except httpx.TimeoutException:
        logger.warning("Radio inject timeout")
        return JSONResponse({"error": "Timeout connecting to Radio Garden or Navidrome"}, status_code=504)
    except Exception as e:
        logger.exception("Radio inject failed: %s", e)
        return JSONResponse({"error": f"Internal Error: {str(e)}"}, status_code=500)


@router.get("/api/radio/list")
async def get_saved_radios(request: Request, db: Session = Depends(get_db)):
    """Fetch saved internet radio stations from Navidrome"""
    user = get_current_user_safe(db, request)
    if not user: 
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        return JSONResponse(await fetch_saved_radios_for_user(user))
    except Exception as e:
        logger.warning("Failed to list saved radios: %s", e)
        return JSONResponse([], status_code=500)


@router.delete("/api/radio/{radio_id}")
async def delete_saved_radio(radio_id: str, request: Request, db: Session = Depends(get_db)):
    """Delete an internet radio station from Navidrome"""
    user = get_current_user_safe(db, request)
    if not user: 
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        target_ip = manager.get_or_spawn_container(user.username)
        url = f"http://{target_ip}:4533/{user.username}/rest/deleteInternetRadioStation"
        params = {
            "u": user.username, 
            "p": "enc:000000", 
            "v": "1.16.1", 
            "c": "navipod-concierge", 
            "f": "json",
            "id": radio_id
        }
        headers = {"x-navidrome-user": user.username}
        
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 200:
                return JSONResponse({"status": "success"})
            else:
                return JSONResponse({"error": f"Navidrome error: {resp.status_code}"}, status_code=resp.status_code)
    except Exception as e:
        logger.warning("Failed to delete saved radio %s: %s", radio_id, e)
        return JSONResponse({"error": str(e)}, status_code=500)
