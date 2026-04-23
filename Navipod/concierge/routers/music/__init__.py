"""
Music module - Aggregates all music-related routers.
"""

from fastapi import APIRouter

from . import (
    core,
    delete_requests,
    downloads,
    favorites,
    personalization,
    playback_state,
    playlists,
    radio,
    recent_activity,
    recommendations,
    search,
    streaming,
    sync,
    wrapped,
)

# Create the main router that includes all sub-routers
router = APIRouter()

# Include all sub-routers
router.include_router(core.router)
router.include_router(streaming.router)
router.include_router(delete_requests.router)
router.include_router(downloads.router)
router.include_router(radio.router)
router.include_router(favorites.router)
router.include_router(playlists.router)
router.include_router(recommendations.router)
router.include_router(search.router)
router.include_router(sync.router)
router.include_router(recent_activity.router)
router.include_router(personalization.router)
router.include_router(playback_state.router)
router.include_router(wrapped.router)

# Re-export the combined router
__all__ = ["router"]
