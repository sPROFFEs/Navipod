"""
Music module - Aggregates all music-related routers.
"""
from fastapi import APIRouter

from . import core
from . import streaming
from . import downloads
from . import radio
from . import favorites
from . import playlists
from . import recommendations
from . import search
from . import sync
from . import recent_activity
from . import personalization


# Create the main router that includes all sub-routers
router = APIRouter()

# Include all sub-routers
router.include_router(core.router)
router.include_router(streaming.router)
router.include_router(downloads.router)
router.include_router(radio.router)
router.include_router(favorites.router)
router.include_router(playlists.router)
router.include_router(recommendations.router)
router.include_router(search.router)
router.include_router(sync.router)
router.include_router(recent_activity.router)
router.include_router(personalization.router)

# Re-export the combined router
__all__ = ["router"]
