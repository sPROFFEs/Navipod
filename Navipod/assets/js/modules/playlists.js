/**
 * playlists.js - Playlist Management
 * CRUD operations, modals, and view rendering
 */

import * as state from './state.js';
import * as ui from './ui.js';
import * as api from './api.js';
import * as player from './player.js';

// === RENDER PLAYLIST VIEW ===

export async function renderPlaylist(container, playlistId) {
    let data = {};
    try {
        data = await (await fetch(`${state.API}/playlists/${playlistId}`)).json();
        const tracks = (data.tracks || []).map(t => ({
            ...t,
            db_id: t.track_id || t.id,
            id: t.track_id || t.id
        }));
        state.setCurrentViewList(tracks);
    } catch (e) { }

    const thumb = data.thumbnail || '/static/img/default_cover.png';
    const hasThumb = data.thumbnail && !data.thumbnail.includes('default');
    const trackCount = data.tracks?.length || 0;

    container.innerHTML = `
        <div class="playlist-header-section">
            <div class="playlist-cover-large">
                ${hasThumb ? `<img src="${thumb}" onerror="this.src='/static/img/default_cover.png'">` : `<i data-lucide="list-music"></i>`}
            </div>
            <div class="playlist-info">
                <p class="playlist-type">Playlist</p>
                <div class="playlist-title-row">
                    <h1 class="playlist-title" id="playlist-title-${playlistId}">${ui.escHtml(data.name || 'Playlist')}</h1>
                    <button class="icon-btn-sm" onclick="showEditPlaylistModal(${playlistId}, '${ui.escHtml(data.name || 'Playlist')}')" title="Edit Name">
                        <i data-lucide="pencil" width="16"></i>
                    </button>
                </div>
                <p class="playlist-stats">${trackCount} songs</p>
                <div class="playlist-actions">
                    ${trackCount > 0 ? `
                    <button onclick="playPlaylistInOrder()" class="btn-primary-lg">
                        <i data-lucide="play" width="20" height="20"></i> Play
                    </button>
                    <button onclick="playPlaylistShuffle()" class="btn-secondary-lg">
                        <i data-lucide="shuffle" width="20" height="20"></i> Shuffle
                    </button>
                    ` : ''}
                    <button onclick="event.stopPropagation(); showDeletePlaylistModal(${playlistId}, '${ui.escHtml(data.name || 'Playlist')}')" class="btn-danger-outline">
                        <i data-lucide="trash-2" width="16"></i> Delete
                    </button>
                </div>
            </div>
        </div>
        ${trackCount > 0
            ? `<div class="track-list">${state.currentViewList.map((t, i) => window.createTrackRow ? window.createTrackRow({ ...t, is_local: true, source: 'local' }, i, playlistId) : '').join('')}</div>`
            : '<div class="empty-state glass-panel"><p>This playlist is empty.</p></div>'}`;
    lucide.createIcons();
}


// === PLAY PLAYLIST ===

export function playPlaylistInOrder() {
    if (!state.currentViewList || state.currentViewList.length === 0) {
        ui.showToast("Playlist is empty", "error");
        return;
    }
    state.setContextQueue([...state.currentViewList]);
    state.setOriginalContextQueue([...state.currentViewList]);
    state.setContextIndex(0);
    player.playTrack(state.contextQueue[0]);
    if (window.renderQueue) window.renderQueue();
}

export function playPlaylistShuffle() {
    if (!state.currentViewList || state.currentViewList.length === 0) {
        ui.showToast("Playlist is empty", "error");
        return;
    }
    const shuffled = [...state.currentViewList].sort(() => Math.random() - 0.5);
    state.setContextQueue(shuffled);
    state.setOriginalContextQueue([...state.currentViewList]);
    state.setContextIndex(0);
    player.playTrack(shuffled[0]);
    ui.showToast("Playing shuffled playlist 🔀");
    if (window.renderQueue) window.renderQueue();
}


// === MODALS ===

export function showAddToPlaylistModal(trackId) {
    const playlistItems = state.userPlaylists.length > 0
        ? state.userPlaylists.map(p => {
            const thumb = p.thumbnail || '/static/img/default_cover.png';
            const trackCount = p.track_count || 0;
            return `
                <div class="modal-playlist-item" onclick="addToPlaylist(${p.id}, ${trackId})">
                    <div class="modal-playlist-thumb">
                        <img src="${thumb}" onerror="this.src='/static/img/default_cover.png'" alt="">
                    </div>
                    <div class="modal-playlist-info">
                        <span class="modal-playlist-name">${ui.escHtml(p.name)}</span>
                        <span class="modal-playlist-count">${trackCount} track${trackCount !== 1 ? 's' : ''}</span>
                    </div>
                    <i data-lucide="plus-circle" class="modal-playlist-add-icon"></i>
                </div>`;
        }).join('')
        : '<p class="modal-empty">No playlists yet. Create one below!</p>';

    const html = `<div class="modal-overlay" onclick="closeModal()">
        <div class="modal modal-playlist" onclick="event.stopPropagation()">
            <div class="modal-header">
                <h2><i data-lucide="list-music"></i> Add to Playlist</h2>
                <button class="modal-close" onclick="closeModal()"><i data-lucide="x"></i></button>
            </div>
            <div class="modal-list">${playlistItems}</div>
            <div class="modal-actions">
                <button class="modal-btn-primary" onclick="showCreatePlaylistModal(${trackId})">
                    <i data-lucide="plus"></i> New Playlist
                </button>
            </div>
        </div>
    </div>`;
    document.getElementById('modal-container').innerHTML = html;
    lucide.createIcons();
}

export function showCreatePlaylistModal(trackIdToAdd = null) {
    const html = `<div class="modal-overlay" onclick="closeModal()">
        <div class="modal modal-create" onclick="event.stopPropagation()">
            <div class="modal-header">
                <h2><i data-lucide="folder-plus"></i> Create Playlist</h2>
                <button class="modal-close" onclick="closeModal()"><i data-lucide="x"></i></button>
            </div>
            <div class="modal-body">
                <label class="modal-label">Playlist Name</label>
                <input type="text" id="new-playlist-name" class="modal-input" placeholder="My awesome playlist..." autofocus>
            </div>
            <div class="modal-actions">
                <button class="modal-btn-cancel" onclick="closeModal()">Cancel</button>
                <button class="modal-btn-primary" onclick="createPlaylist(${trackIdToAdd})">
                    <i data-lucide="check"></i> Create
                </button>
            </div>
        </div>
    </div>`;
    document.getElementById('modal-container').innerHTML = html;
    lucide.createIcons();
    document.getElementById('new-playlist-name')?.focus();
}

export function showDeletePlaylistModal(playlistId, playlistName) {
    const html = `<div class="modal-overlay" onclick="closeModal()">
        <div class="modal" onclick="event.stopPropagation()">
            <h2 style="margin-bottom: 16px;">Delete Playlist</h2>
            <p style="color: var(--text-sub); margin-bottom: 24px;">Are you sure you want to permanently delete <strong style="color: white;">${ui.escHtml(playlistName)}</strong>? This action cannot be undone.</p>
            <div class="modal-actions">
                <button class="modal-btn-cancel" onclick="closeModal()">Cancel</button>
                <button class="modal-btn-danger" onclick="deletePlaylist(${playlistId})">Delete Permanently</button>
            </div>
        </div>
    </div>`;
    document.getElementById('modal-container').innerHTML = html;
}

export function showEditPlaylistModal(id, currentName) {
    const html = `<div class="modal-overlay" onclick="closeModal()">
        <div class="modal" onclick="event.stopPropagation()">
            <h2 style="margin-bottom: 16px;">Rename Playlist</h2>
            <input type="text" id="edit-playlist-name-input" class="modal-input" value="${ui.escHtml(currentName)}" placeholder="New playlist name" style="margin-bottom: 24px;">
            <div class="modal-actions">
                <button class="modal-btn-cancel" onclick="closeModal()">Cancel</button>
                <button class="modal-btn-primary" onclick="editPlaylistName('${id}', document.getElementById('edit-playlist-name-input').value)">Save</button>
            </div>
        </div>
    </div>`;
    document.getElementById('modal-container').innerHTML = html;
    setTimeout(() => document.getElementById('edit-playlist-name-input')?.focus(), 100);
}


// === CRUD ACTIONS ===

export async function createPlaylist(trackIdToAdd = null) {
    const input = document.getElementById('new-playlist-name');
    const name = input?.value?.trim();
    if (!name) {
        ui.showToast("Enter a name", "error");
        return;
    }
    try {
        const res = await fetch(`${state.API}/playlists`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });
        const pl = await res.json();
        if (res.ok) {
            const playlists = [...state.userPlaylists, pl];
            state.setUserPlaylists(playlists);
            if (window.renderSidebarPlaylists) window.renderSidebarPlaylists();
            ui.closeModal();
            ui.showToast("Playlist created!", "success");
            if (trackIdToAdd) await addToPlaylist(pl.id, trackIdToAdd);
        }
    } catch (e) {
        ui.showToast("Failed", "error");
    }
}

export async function addToPlaylist(playlistId, trackId) {
    try {
        const res = await fetch(`${state.API}/playlists/${playlistId}/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_id: trackId })
        });
        if (res.ok) {
            ui.closeModal();
            ui.showToast("Added to playlist!", "success");
        } else {
            const err = await res.json();
            ui.showToast(err.error || "Failed", "error");
        }
    } catch (e) {
        ui.showToast("Failed", "error");
    }
}

export function showRemoveFromPlaylistModal(playlistId, trackId, trackTitle) {
    const html = `<div class="modal-overlay" onclick="closeModal()">
        <div class="modal" onclick="event.stopPropagation()">
            <h2 style="margin-bottom: 16px;">Remove from Playlist</h2>
            <p style="color: var(--text-sub); margin-bottom: 24px;">Remove <strong style="color: white;">${ui.escHtml(trackTitle || 'this track')}</strong> from the playlist?</p>
            <div class="modal-actions">
                <button class="modal-btn-cancel" onclick="closeModal()">Cancel</button>
                <button class="modal-btn-danger" onclick="removeFromPlaylist(${playlistId}, ${trackId})">Remove</button>
            </div>
        </div>
    </div>`;
    document.getElementById('modal-container').innerHTML = html;
}

export async function removeFromPlaylist(playlistId, trackId) {
    ui.closeModal();
    try {
        const res = await fetch(`${state.API}/playlists/${playlistId}/remove/${trackId}`, { method: 'DELETE' });
        if (res.ok) {
            if (window.loadView) window.loadView('playlist', playlistId);
            ui.showToast("Removed from playlist", "success");
        }
    } catch (e) {
        ui.showToast("Failed to remove", "error");
    }
}

export async function deletePlaylist(playlistId) {
    ui.closeModal();
    try {
        const res = await fetch(`${state.API}/playlists/${playlistId}`, { method: 'DELETE' });
        if (res.ok) {
            const playlists = state.userPlaylists.filter(p => p.id !== playlistId);
            state.setUserPlaylists(playlists);
            if (window.renderSidebarPlaylists) window.renderSidebarPlaylists();
            if (window.loadView) window.loadView('home');
            ui.showToast("Playlist deleted", "success");
        }
    } catch (e) {
        ui.showToast("Failed", "error");
    }
}

export async function editPlaylistName(id, newName) {
    ui.closeModal();
    if (!newName || newName.trim() === "") return;

    try {
        const res = await fetch(`${state.API}/playlists/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: newName.trim() })
        });

        if (res.ok) {
            const data = await res.json();
            const titleEl = document.getElementById(`playlist-title-${id}`);
            if (titleEl) titleEl.textContent = data.name;

            if (window.loadUserData) window.loadUserData();
            ui.showToast("Playlist renamed", "success");

            renderPlaylist(document.getElementById('view-container'), id);
        } else {
            ui.showToast("Failed to rename playlist", "error");
        }
    } catch (e) {
        console.error(e);
        ui.showToast("Error renaming playlist", "error");
    }
}


// === ADD FROM CURRENT TRACK ===

export function addToPlaylistCurrent() {
    if (!state.currentTrack || !state.currentTrack.db_id) {
        ui.showToast("Cannot add this track (not in library)", "error");
        return;
    }
    showAddToPlaylistModal(state.currentTrack.db_id);
}

export function showAddToPlaylistFromPlayer() {
    if (!state.currentTrack) {
        ui.showToast("No track playing", "error");
        return;
    }
    const id = state.currentTrack.db_id || state.currentTrack.id;
    if (!id) {
        ui.showToast("This track cannot be added to playlists", "error");
        return;
    }
    showAddToPlaylistModal(id);
}
