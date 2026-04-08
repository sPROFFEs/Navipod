/**
 * api.js - API Communication Layer
 * Centralized API calls and data fetching
 */

import * as state from './state.js';
import * as ui from './ui.js';

// === USER DATA LOADING ===

export async function loadUserData() {
    try {
        const [favsRes, playlistsRes] = await Promise.all([
            fetch(`${state.API}/favorites`),
            fetch(`${state.API}/playlists`)
        ]);
        const favs = await favsRes.json();
        const pls = await playlistsRes.json();

        state.setUserFavorites(new Set(favs.map(f => f.id)));
        state.setUserPlaylists(pls);

        // These will be called from views.js
        if (window.renderSidebarPlaylists) window.renderSidebarPlaylists();
        if (window.loadSidebarRadios) window.loadSidebarRadios();

        startHeartbeatSync();
        requestSyncRefresh();
    } catch (e) {
        console.error("Failed to load user data:", e);
    }
}


// === HEARTBEAT SYNC SYSTEM ===

export function startHeartbeatSync() {
    if (state.heartbeatInterval) clearInterval(state.heartbeatInterval);
    state.setHeartbeatInterval(setInterval(checkSyncState, 15000));
    checkSyncState();
}

export async function requestSyncRefresh() {
    try {
        await fetch(`${state.API}/sync-refresh`, { method: 'POST' });
    } catch (e) {
        console.error('[SYNC] Refresh request error:', e);
    }
}

export async function checkSyncState() {
    try {
        const res = await fetch(`${state.API}/sync-state`);
        if (!res.ok) return;

        const syncState = await res.json();

        if (state.lastSyncVersion !== null && syncState.version !== state.lastSyncVersion) {
            console.log('[SYNC] State changed, updating...');

            state.setUserFavorites(new Set(syncState.fav_ids));

            const plsRes = await fetch(`${state.API}/playlists`);
            if (plsRes.ok) {
                state.setUserPlaylists(await plsRes.json());
                if (window.renderSidebarPlaylists) window.renderSidebarPlaylists();
            }

            if (window.updateFullscreenPlayButton) window.updateFullscreenPlayButton();

            document.querySelectorAll('.like-btn').forEach(btn => {
                const trackId = parseInt(btn.dataset.trackId);
                if (!isNaN(trackId)) {
                    const isLiked = state.userFavorites.has(trackId);
                    btn.innerHTML = `<i data-lucide="heart" ${isLiked ? 'fill="var(--accent)"' : ''}></i>`;
                }
            });
            lucide.createIcons();
        }

        state.setLastSyncVersion(syncState.version);
    } catch (e) {
        // Silent fail - heartbeat is non-critical
    }
}


// === SEARCH API ===

export async function executeSearch(query) {
    if (!query) return [];

    try {
        const source = state.currentSource;
        const res = await fetch(`${state.API}/search?q=${encodeURIComponent(query)}&source=${source}`);
        if (!res.ok) throw new Error('Search failed');
        return await res.json();
    } catch (e) {
        console.error('[SEARCH] Error:', e);
        return [];
    }
}


// === FAVORITES API ===

export async function addFavorite(trackId) {
    try {
        const res = await fetch(`${state.API}/favorites/${trackId}`, { method: 'POST' });
        return res.ok;
    } catch (e) {
        console.error('[FAV] Add error:', e);
        return false;
    }
}

export async function removeFavorite(trackId) {
    try {
        const res = await fetch(`${state.API}/favorites/${trackId}`, { method: 'DELETE' });
        return res.ok;
    } catch (e) {
        console.error('[FAV] Remove error:', e);
        return false;
    }
}


// === PLAYLISTS API ===

export async function fetchPlaylists() {
    try {
        const res = await fetch(`${state.API}/playlists`);
        if (res.ok) return await res.json();
    } catch (e) {
        console.error('[PLAYLISTS] Fetch error:', e);
    }
    return [];
}

export async function fetchPlaylist(playlistId) {
    try {
        const res = await fetch(`${state.API}/playlists/${playlistId}`);
        if (res.ok) return await res.json();
    } catch (e) {
        console.error('[PLAYLIST] Fetch error:', e);
    }
    return null;
}

export async function createPlaylistApi(name) {
    try {
        const res = await fetch(`${state.API}/playlists`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });
        if (res.ok) return await res.json();
    } catch (e) {
        console.error('[PLAYLIST] Create error:', e);
    }
    return null;
}

export async function addToPlaylistApi(playlistId, trackId) {
    try {
        const res = await fetch(`${state.API}/playlists/${playlistId}/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_id: trackId })
        });
        return res.ok;
    } catch (e) {
        console.error('[PLAYLIST] Add error:', e);
        return false;
    }
}

export async function removeFromPlaylistApi(playlistId, trackId) {
    try {
        const res = await fetch(`${state.API}/playlists/${playlistId}/remove/${trackId}`, {
            method: 'DELETE'
        });
        return res.ok;
    } catch (e) {
        console.error('[PLAYLIST] Remove error:', e);
        return false;
    }
}

export async function deletePlaylistApi(playlistId) {
    try {
        const res = await fetch(`${state.API}/playlists/${playlistId}`, { method: 'DELETE' });
        return res.ok;
    } catch (e) {
        console.error('[PLAYLIST] Delete error:', e);
        return false;
    }
}

export async function updatePlaylistNameApi(playlistId, name) {
    try {
        const res = await fetch(`${state.API}/playlists/${playlistId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });
        return res.ok;
    } catch (e) {
        console.error('[PLAYLIST] Rename error:', e);
        return false;
    }
}


// === DOWNLOAD API ===

export async function triggerDownloadApi(trackData) {
    try {
        const res = await fetch(`${state.API}/download`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url: trackData.id || trackData.url,
                title: trackData.title,
                artist: trackData.artist
            })
        });
        return res.ok;
    } catch (e) {
        console.error('[DOWNLOAD] Error:', e);
        return false;
    }
}

export async function fetchDownloadJobs() {
    try {
        const res = await fetch(`${state.API}/jobs`);
        if (res.ok) return await res.json();
    } catch (e) {
        console.error('[JOBS] Fetch error:', e);
    }
    return [];
}


// === RADIO API ===

export async function fetchRadioBrowse(query) {
    try {
        const res = await fetch(`${state.API}/radio/browse?q=${encodeURIComponent(query)}`);
        if (res.ok) return await res.json();
    } catch (e) {
        console.error('[RADIO] Browse error:', e);
    }
    return [];
}

export async function fetchRadioPlaylist(path) {
    try {
        const res = await fetch(`${state.API}/radio/playlist/${path}`);
        if (res.ok) return await res.json();
    } catch (e) {
        console.error('[RADIO] Playlist error:', e);
    }
    return [];
}

export async function fetchRadioSearch(query) {
    try {
        const res = await fetch(`${state.API}/radio/search?q=${encodeURIComponent(query)}`);
        if (res.ok) return await res.json();
    } catch (e) {
        console.error('[RADIO] Search error:', e);
    }
    return [];
}

export async function fetchRadioPlace(placeId) {
    try {
        const res = await fetch(`${state.API}/radio/place/${placeId}`);
        if (res.ok) return await res.json();
    } catch (e) {
        console.error('[RADIO] Place error:', e);
    }
    return null;
}

export async function injectRadioApi(id, name) {
    try {
        const res = await fetch(`${state.API}/radio/inject`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ radio_garden_id: id, name })
        });
        return res.ok;
    } catch (e) {
        console.error('[RADIO] Inject error:', e);
        return false;
    }
}

export async function fetchSavedRadios() {
    try {
        const res = await fetch(`${state.API}/radio/list`);
        if (res.ok) return await res.json();
    } catch (e) {
        console.error('[RADIO] List error:', e);
    }
    return [];
}

export async function deleteRadioApi(radioId) {
    try {
        const res = await fetch(`${state.API}/radio/${radioId}`, { method: 'DELETE' });
        return res.ok;
    } catch (e) {
        console.error('[RADIO] Delete error:', e);
        return false;
    }
}


// === RECOMMENDATIONS API ===

export async function fetchRecommendations() {
    try {
        const res = await fetch(`${state.API}/recommendations`);
        if (res.ok) return await res.json();
    } catch (e) {
        console.error('[RECS] Fetch error:', e);
    }
    return [];
}

export async function fetchRandomTrack() {
    try {
        const res = await fetch(`${state.API}/random-track`);
        if (res.ok) return await res.json();
    } catch (e) {
        console.error('[RANDOM] Fetch error:', e);
    }
    return null;
}


// === FAVORITES LIST ===

export async function fetchFavorites() {
    try {
        const res = await fetch(`${state.API}/favorites`);
        if (res.ok) return await res.json();
    } catch (e) {
        console.error('[FAVS] Fetch error:', e);
    }
    return [];
}
