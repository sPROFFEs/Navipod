/**
 * sync.js - single heartbeat/sync coordinator for favorites, playlists and UI.
 */

import * as state from './state.js';
import * as ui from './ui.js';

const HEARTBEAT_MS = 30000;
let handlers = {
    renderSidebarPlaylists: null,
    refreshRecentActivity: null,
};

export function setSyncHandlers(nextHandlers = {}) {
    handlers = { ...handlers, ...nextHandlers };
}

export function startHeartbeatSync() {
    if (state.heartbeatInterval) {
        clearInterval(state.heartbeatInterval);
        state.setHeartbeatInterval(null);
    }

    if (document.visibilityState === 'hidden') return;

    state.setHeartbeatInterval(setInterval(checkSyncState, HEARTBEAT_MS));
    checkSyncState();
}

export function stopHeartbeatSync() {
    if (!state.heartbeatInterval) return;
    clearInterval(state.heartbeatInterval);
    state.setHeartbeatInterval(null);
}

export function initHeartbeatLifecycle() {
    if (window.__navipodHeartbeatLifecycleBound) return;
    window.__navipodHeartbeatLifecycleBound = true;

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') {
            stopHeartbeatSync();
        } else {
            startHeartbeatSync();
        }
    });

    window.addEventListener('pageshow', () => {
        startHeartbeatSync();
    });

    window.addEventListener('pagehide', () => {
        stopHeartbeatSync();
    });
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
                if (handlers.renderSidebarPlaylists) handlers.renderSidebarPlaylists();
                else if (window.renderSidebarPlaylists) window.renderSidebarPlaylists();
                if (handlers.refreshRecentActivity) await handlers.refreshRecentActivity();
            }

            ui.updateFullscreenPlayButton();

            document.querySelectorAll('.like-btn').forEach(btn => {
                const trackId = parseInt(btn.dataset.trackId);
                if (!isNaN(trackId)) {
                    const isLiked = state.userFavorites.has(trackId);
                    btn.innerHTML = `<i data-lucide="heart" ${isLiked ? 'fill="var(--accent)"' : ''}></i>`;
                }
            });
            if (window.lucide) lucide.createIcons();
        }

        state.setLastSyncVersion(syncState.version);
    } catch (e) {
        // Heartbeat is non-critical.
    }
}
