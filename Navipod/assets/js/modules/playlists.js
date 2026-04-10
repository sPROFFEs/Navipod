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
        if (data?.error) {
            container.innerHTML = `<div class="empty-state glass-panel"><p>${ui.escHtml(data.error)}</p></div>`;
            return;
        }
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
    const isPublic = Boolean(data.is_public);
    const isOwner = Boolean(data.is_owner);
    const isEditable = Boolean(data.is_editable);
    const isSyncedCopy = Boolean(data.source_playlist_id);
    const sourcePlaylistAvailable = Boolean(data.source_playlist_exists && data.source_playlist_public);
    const ownerLabel = data.owner_username ? `By ${ui.escHtml(data.owner_username)}` : '';
    const sourceBadge = isSyncedCopy ? '<span class="source-badge musicbrainz" style="margin-left:8px;">Synced copy</span>' : '';
    const visibilityBadge = isPublic ? '<span class="source-badge spotify" style="margin-left:8px;">Public</span>' : '<span class="source-badge local" style="margin-left:8px;">Private</span>';
    const safePlaylistName = ui.escHtml(data.name || 'Playlist').replace(/'/g, "\\'");
    const ownerControls = isOwner && isEditable ? `
        <button class="icon-btn-sm" onclick="showEditPlaylistModal(${playlistId}, '${safePlaylistName}')" title="Edit Name">
            <i data-lucide="pencil" width="16"></i>
        </button>` : '';
    const coverControls = isOwner ? `
        <div class="playlist-cover-actions">
            <button class="icon-btn-sm" onclick="openPlaylistCoverUpload(${playlistId})" title="Upload cover">
                <i data-lucide="image-plus" width="16"></i>
            </button>
            <button class="icon-btn-sm" onclick="showPlaylistCoverTrackModal(${playlistId})" title="Choose track cover">
                <i data-lucide="disc-3" width="16"></i>
            </button>
            <button class="icon-btn-sm" onclick="resetPlaylistCover(${playlistId})" title="Use automatic cover">
                <i data-lucide="rotate-ccw" width="16"></i>
            </button>
            <input type="file" id="playlist-cover-input-${playlistId}" accept="image/png,image/jpeg,image/webp,image/gif" style="display:none" onchange="handlePlaylistCoverUpload(${playlistId}, this)">
        </div>` : '';
    const publishButton = isOwner && !isSyncedCopy ? `
        <button onclick="togglePlaylistPublic(${playlistId}, ${isPublic ? 'false' : 'true'})" class="btn-secondary-lg playlist-action-btn" title="${isPublic ? 'Make Private' : 'Make Public'}" aria-label="${isPublic ? 'Make Private' : 'Make Public'}">
            <i data-lucide="${isPublic ? 'lock' : 'globe'}" width="20" height="20"></i>
            <span class="playlist-btn-label">${isPublic ? 'Make Private' : 'Make Public'}</span>
        </button>` : '';
    const copyButton = !isOwner && isPublic ? `
        <button onclick="copyPublicPlaylist(${playlistId})" class="btn-secondary-lg playlist-action-btn" title="Create / Sync Copy" aria-label="Create / Sync Copy">
            <i data-lucide="copy-plus" width="20" height="20"></i>
            <span class="playlist-btn-label">Create / Sync Copy</span>
        </button>` : '';
    const syncButton = isOwner && isSyncedCopy ? `
        ${sourcePlaylistAvailable ? `
        <button onclick="copyPublicPlaylist(${data.source_playlist_id})" class="btn-secondary-lg playlist-action-btn" title="Sync from Source" aria-label="Sync from Source">
            <i data-lucide="refresh-cw" width="20" height="20"></i>
            <span class="playlist-btn-label">Sync from Source</span>
        </button>` : `
        <button class="btn-secondary-lg playlist-action-btn" disabled style="opacity:0.55; cursor:not-allowed;" title="Source is private" aria-label="Source is private">
            <i data-lucide="lock" width="20" height="20"></i>
            <span class="playlist-btn-label">Source is private</span>
        </button>`}` : '';
    const deleteButton = isOwner ? `
        <button onclick="event.stopPropagation(); showDeletePlaylistModal(${playlistId}, '${ui.escHtml(data.name || 'Playlist')}')" class="btn-danger-outline playlist-action-btn" title="Delete" aria-label="Delete">
            <i data-lucide="trash-2" width="16"></i>
            <span class="playlist-btn-label">Delete</span>
        </button>` : '';

    container.innerHTML = `
        <div class="playlist-header-section">
            <div class="playlist-cover-large">
                ${hasThumb ? `<img src="${thumb}" onerror="this.src='/static/img/default_cover.png'">` : `<i data-lucide="list-music"></i>`}
                ${coverControls}
            </div>
            <div class="playlist-info">
                <p class="playlist-type">Playlist</p>
                <div class="playlist-title-row">
                    <h1 class="playlist-title" id="playlist-title-${playlistId}">${ui.escHtml(data.name || 'Playlist')}</h1>
                    ${ownerControls}
                </div>
                <p class="playlist-stats">${trackCount} songs ${ownerLabel}${visibilityBadge}${sourceBadge}</p>
                <div class="playlist-actions">
                    ${trackCount > 0 ? `
                    <button onclick="playPlaylistInOrder()" class="btn-primary-lg playlist-action-btn" title="Play" aria-label="Play">
                        <i data-lucide="play" width="20" height="20"></i>
                        <span class="playlist-btn-label">Play</span>
                    </button>
                    <button onclick="playPlaylistShuffle()" class="btn-secondary-lg playlist-action-btn" title="Shuffle" aria-label="Shuffle">
                        <i data-lucide="shuffle" width="20" height="20"></i>
                        <span class="playlist-btn-label">Shuffle</span>
                    </button>
                    ` : ''}
                    ${publishButton}
                    ${copyButton}
                    ${syncButton}
                    ${deleteButton}
                </div>
            </div>
        </div>
        ${trackCount > 0
            ? `<div class="track-list">${state.currentViewList.map((t, i) => window.createTrackRow ? window.createTrackRow({ ...t, is_local: true, source: 'local' }, i, isEditable ? playlistId : null) : '').join('')}</div>`
            : '<div class="empty-state glass-panel"><p>This playlist is empty.</p></div>'}`;
    lucide.createIcons();
}


export function openPlaylistCoverUpload(playlistId) {
    document.getElementById(`playlist-cover-input-${playlistId}`)?.click();
}


export async function handlePlaylistCoverUpload(playlistId, input) {
    const file = input?.files?.[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('cover_file', file);

    try {
        const res = await fetch(`${state.API}/playlists/${playlistId}/cover/upload`, {
            method: 'POST',
            body: formData,
        });
        const data = await res.json();
        if (!res.ok) {
            ui.showToast(data.error || 'Failed to update cover', 'error');
            return;
        }
        ui.showToast('Playlist cover updated', 'success');
        if (window.loadUserData) await window.loadUserData();
        if (window.loadView) window.loadView('playlist', playlistId);
    } catch (e) {
        ui.showToast('Failed to update cover', 'error');
    } finally {
        if (input) input.value = '';
    }
}


export function showPlaylistCoverTrackModal(playlistId) {
    const tracks = state.currentViewList || [];
    if (!tracks.length) {
        ui.showToast('Playlist is empty', 'error');
        return;
    }

    const items = tracks.map(track => `
        <button class="modal-playlist-item" onclick="setPlaylistCoverFromTrack(${playlistId}, ${track.id})">
            <div class="modal-playlist-thumb">
                <img src="${track.thumbnail || '/static/img/default_cover.png'}" onerror="this.src='/static/img/default_cover.png'" alt="">
            </div>
            <div class="modal-playlist-info">
                <span class="modal-playlist-name">${ui.escHtml(track.title || 'Unknown')}</span>
                <span class="modal-playlist-count">${ui.escHtml(track.artist || 'Unknown')}</span>
            </div>
            <i data-lucide="check-circle" class="modal-playlist-add-icon"></i>
        </button>`).join('');

    const html = `<div class="modal-overlay" onclick="closeModal()">
        <div class="modal modal-playlist" onclick="event.stopPropagation()">
            <div class="modal-header">
                <h2><i data-lucide="disc-3"></i> Choose Cover Track</h2>
                <button class="modal-close" onclick="closeModal()"><i data-lucide="x"></i></button>
            </div>
            <div class="modal-list">${items}</div>
        </div>
    </div>`;
    document.getElementById('modal-container').innerHTML = html;
    lucide.createIcons();
}


export async function setPlaylistCoverFromTrack(playlistId, trackId) {
    try {
        const res = await fetch(`${state.API}/playlists/${playlistId}/cover/track`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_id: trackId }),
        });
        const data = await res.json();
        if (!res.ok) {
            ui.showToast(data.error || 'Failed to update cover', 'error');
            return;
        }
        ui.closeModal();
        ui.showToast('Playlist cover updated', 'success');
        if (window.loadUserData) await window.loadUserData();
        if (window.loadView) window.loadView('playlist', playlistId);
    } catch (e) {
        ui.showToast('Failed to update cover', 'error');
    }
}


export async function resetPlaylistCover(playlistId) {
    try {
        const res = await fetch(`${state.API}/playlists/${playlistId}/cover`, { method: 'DELETE' });
        const data = await res.json();
        if (!res.ok) {
            ui.showToast(data.error || 'Failed to reset cover', 'error');
            return;
        }
        ui.showToast('Playlist cover reset', 'success');
        if (window.loadUserData) await window.loadUserData();
        if (window.loadView) window.loadView('playlist', playlistId);
    } catch (e) {
        ui.showToast('Failed to reset cover', 'error');
    }
}


export async function renderPublicPlaylists(container) {
    let publicPlaylists = [];
    try {
        const res = await fetch(`${state.API}/public/playlists`);
        publicPlaylists = await res.json();
        if (!res.ok) throw new Error(publicPlaylists.error || `HTTP ${res.status}`);
    } catch (e) {
        container.innerHTML = `<div class="empty-state glass-panel"><p>Failed to load public playlists.</p></div>`;
        return;
    }

    container.innerHTML = `
        <div class="hero-section">
            <h1 class="hero-greeting">Public Playlists</h1>
            <p class="playlist-stats">Browse read-only playlists shared by other users and create your own synced copy.</p>
        </div>
        ${publicPlaylists.length > 0
            ? `<div class="grid-shelf playlist-mobile-list">${publicPlaylists.map(window.createPlaylistCard).join('')}</div>`
            : '<div class="empty-state glass-panel"><p>No public playlists yet.</p></div>'}`;
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
    const editablePlaylists = state.userPlaylists.filter(p => p.is_editable !== false);
    const playlistItems = editablePlaylists.length > 0
        ? editablePlaylists.map(p => {
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
        : '<p class="modal-empty">No editable playlists available. Create one below!</p>';

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
            if (state.currentViewName === 'library' && window.loadView) window.loadView('library');
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
            await fetch(`${state.API}/recent-activity/playlist/${playlistId}`, { method: 'DELETE' }).catch(() => null);
            const playlists = state.userPlaylists.filter(p => p.id !== playlistId);
            state.setUserPlaylists(playlists);
            if (window.renderSidebarPlaylists) window.renderSidebarPlaylists();
            if (window.refreshRecentActivity) window.refreshRecentActivity();
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

export async function togglePlaylistPublic(playlistId, shouldBePublic) {
    try {
        const res = await fetch(`${state.API}/playlists/${playlistId}/public`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_public: Boolean(shouldBePublic) })
        });
        const data = await res.json();
        if (!res.ok) {
            ui.showToast(data.error || "Failed to update playlist visibility", "error");
            return;
        }

        if (window.loadUserData) await window.loadUserData();
        if (window.loadView) window.loadView('playlist', playlistId);
        ui.showToast(data.is_public ? "Playlist is now public" : "Playlist is now private", "success");
    } catch (e) {
        ui.showToast("Failed to update playlist visibility", "error");
    }
}

export async function copyPublicPlaylist(sourcePlaylistId) {
    try {
        const res = await fetch(`${state.API}/playlists/${sourcePlaylistId}/copy`, {
            method: 'POST'
        });
        const data = await res.json();
        if (!res.ok) {
            ui.showToast(data.error || "Failed to sync playlist copy", "error");
            return;
        }

        if (window.loadUserData) await window.loadUserData();
        if (window.loadView) window.loadView('playlist', data.id);
        ui.showToast(data.status === 'copied' ? "Playlist copied to your library" : "Playlist copy synced", "success");
    } catch (e) {
        ui.showToast("Failed to sync playlist copy", "error");
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
