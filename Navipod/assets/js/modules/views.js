/**
 * views.js - View Rendering and Routing
 * Handles navigation, view switching, and UI component builders
 */

import * as state from './state.js';
import * as ui from './ui.js';
import * as api from './api.js';
import * as player from './player.js';
import * as search from './search.js';
import * as radio from './radio.js';
import * as favorites from './favorites.js';
import * as playlists from './playlists.js';
import * as downloads from './downloads.js';

// === VIEW ROUTING ===

export async function loadView(view, param = null) {
    const container = document.getElementById('view-container');
    if (!container) return;

    container.innerHTML = `
    <div class="empty-state">
        <div class="music-loader">
            <div class="music-bar" style="animation-delay: 0.0s"></div>
            <div class="music-bar" style="animation-delay: 0.1s"></div>
            <div class="music-bar" style="animation-delay: 0.2s"></div>
            <div class="music-bar" style="animation-delay: 0.3s"></div>
            <div class="music-bar" style="animation-delay: 0.4s"></div>
        </div>
    </div>`;
    lucide.createIcons();

    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    const link = document.querySelector(`.nav-link[onclick*="'${view}'"]`);
    if (link) link.classList.add('active');

    try {
        state.setCurrentViewName(view);

        if (view === 'home') await renderHome(container);
        else if (view === 'search') renderSearch(container);
        else if (view === 'radio') await radio.renderRadio(container);
        else if (view === 'favorites') await favorites.renderFavorites(container);
        else if (view === 'playlist') await playlists.renderPlaylist(container, param);
        else if (view === 'settings_admin') await renderExternalView(container, '/admin/');
        else if (view === 'system_monitor') await renderExternalView(container, '/admin/system');
        else if (view === 'settings_user') await renderExternalView(container, '/user/settings');
        else if (view === 'help') await renderExternalView(container, '/help');

        // Auto-close sidebar on mobile
        if (state.isSidebarOpen && window.innerWidth <= 768) {
            state.toggleSidebar();
        }
    } catch (e) {
        console.error(e);
        container.innerHTML = `<div class="empty-state" style="color:#e74c3c;">Error: ${e.message}</div>`;
    }
}


// === EXTERNAL VIEW (HTMX) ===

export async function renderExternalView(container, url) {
    try {
        const res = await fetch(url, { credentials: 'include' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const text = await res.text();

        const parser = new DOMParser();
        const doc = parser.parseFromString(text, 'text/html');
        const newContent = doc.getElementById('view-container');

        if (newContent) {
            container.innerHTML = newContent.innerHTML;
        } else {
            const main = doc.querySelector('main');
            if (main) container.innerHTML = main.innerHTML;
            else container.innerHTML = text;
        }

        lucide.createIcons();

        if (typeof htmx !== 'undefined') {
            htmx.process(container);
        }

        const scripts = doc.querySelectorAll('script[src]');
        for (const script of scripts) {
            const src = script.getAttribute('src');
            if (!document.querySelector(`script[src="${src}"]`)) {
                await new Promise((resolve, reject) => {
                    const s = document.createElement('script');
                    s.src = src;
                    s.onload = resolve;
                    s.onerror = reject;
                    document.head.appendChild(s);
                });
            }
        }

    } catch (e) {
        console.error("External view load failed", e);
        container.innerHTML = `<div class="empty-state">Failed to load content.<br><a href="${url}" style="color:var(--accent);">Open directly</a></div>`;
    }
}


// === HOME VIEW ===

export async function renderHome(container) {
    let sections = [];
    state.setCurrentViewList([]);

    try {
        sections = await (await fetch(`${state.API}/recommendations`)).json();
    } catch (e) {
        console.error("Recs error:", e);
    }

    let html = `<div class="hero-section">
        <h1 class="hero-greeting">Good ${ui.getGreeting()}, <span class="text-accent">${window.USER_DATA?.username || 'User'}</span></h1>
    </div>`;

    if (sections && sections.length > 0) {
        sections.forEach(s => {
            html += `
            <div class="shelf-section">
                <div class="shelf-header">
                    <h2 class="shelf-title">${ui.escHtml(s.title)}</h2>
                </div>
                <div class="grid-shelf">${s.items.map(createCard).join('')}</div>
            </div>`;
        });
    } else {
        html += `<div class="empty-state glass-panel">
            <i data-lucide="music" class="empty-icon"></i>
            <p>Welcome! Explore the <strong>Search</strong> tab to find music.</p>
        </div>`;
    }

    container.innerHTML = html;
    lucide.createIcons();
}


// === SEARCH VIEW ===

export function renderSearch(container) {
    container.innerHTML = `
        <div class="search-input-wrapper glass-panel" style="margin-top: 8px;">
            <i data-lucide="search" class="search-icon"></i>
            <input type="text" id="search-input" placeholder="What do you want to listen to?" oninput="handleSearch(this.value)">
        </div>
        <div class="source-chips">
            <div class="chip active" onclick="setSource(this, 'all')">All</div>
            <div class="chip spotify" onclick="setSource(this, 'spotify')">Spotify</div>
            <div class="chip youtube" onclick="setSource(this, 'youtube')">YouTube</div>
            <div class="chip lastfm" onclick="setSource(this, 'lastfm')">Last.fm</div>
            <div class="chip musicbrainz" onclick="setSource(this, 'musicbrainz')">MusicBrainz</div>
            <div class="chip" onclick="setSource(this, 'local')">Local</div>
        </div>
        <div id="search-results"></div>`;
    lucide.createIcons();
    document.getElementById('search-input')?.focus();
    search.executeSearch("");
}


// === CARD COMPONENT ===

export function createCard(item) {
    const img = item.thumbnail || '/static/img/default_cover.png';
    const data = btoa(encodeURIComponent(JSON.stringify(item)));
    const sourceMap = {
        'spotify': { icon: 'music', color: '#1DB954' },
        'youtube': { icon: 'youtube', color: '#ff0000' },
        'lastfm': { icon: 'radio', color: '#d51007' },
        'musicbrainz': { icon: 'disc-3', color: '#BA478F' },
        'local': { icon: 'hard-drive', color: '#ffffff' }
    };
    const src = sourceMap[item.source] || sourceMap['local'];
    const sourceIcon = src.icon;
    const sourceColor = src.color;

    return `<div class="card" onclick="handleCardClick('${data}', this)">
        <div class="card-img-container">
            <img src="${img}" loading="lazy" onerror="this.src='/static/img/default_cover.png'">
            <div style="position:absolute;top:8px;right:8px;background:rgba(0,0,0,0.7);border-radius:50%;padding:4px;width:24px;height:24px;display:flex;align-items:center;justify-content:center;">
                <i data-lucide="${sourceIcon}" style="color:${sourceColor};width:14px;height:14px;"></i>
            </div>
            <div class="play-overlay">
                <div style="display:flex;gap:8px;">
                    ${!item.is_local ? `<button class="play-btn-card" onclick="event.stopPropagation(); playPreview('${data}')" title="Preview" style="background:#444; color:white;"><i data-lucide="eye"></i></button>` : ''}
                    <button class="play-btn-card"><i data-lucide="${item.is_local ? 'play' : 'download'}"></i></button>
                </div>
            </div>
        </div>
        <div class="card-title">${ui.escHtml(item.title || 'Unknown')}</div>
        <div class="card-subtitle">${ui.escHtml(item.artist || 'Unknown')}</div>
    </div>`;
}


// === PLAYLIST CARD COMPONENT ===

export function createPlaylistCard(pl) {
    const thumb = pl.thumbnail || '/static/img/default_cover.png';
    const hasThumb = pl.thumbnail && !pl.thumbnail.includes('default');

    return `<div class="card glass-hover" onclick="loadView('playlist', ${pl.id})">
        <div class="card-img-container playlist-card-bg">
            ${hasThumb ? `<img src="${thumb}" class="card-img" onerror="this.src='/static/img/default_cover.png'">` : `<i data-lucide="list-music" class="playlist-icon-large"></i>`}
        </div>
        <div class="card-title">${ui.escHtml(pl.name)}</div>
        <div class="card-subtitle">${pl.track_count} tracks</div>
    </div>`;
}


// === TRACK ROW COMPONENT ===

export function createTrackRow(item, idx, playlistId = null) {
    const img = item.thumbnail || '/static/img/default_cover.png';
    const data = btoa(encodeURIComponent(JSON.stringify(item)));
    const src = item.source || 'local';
    const isLiked = state.userFavorites.has(item.db_id || item.id);
    const canLike = item.is_local && item.db_id;
    const canAddToPlaylist = item.is_local && item.db_id;
    const isActive = item.id === state.currentTrack?.id;

    const rowClickAction = item.is_local ? `playFromView(${idx})` : `playPreview('${data}')`;

    return `<div class="track-row glass-hover ${isLiked ? 'liked-row' : ''} ${isActive ? 'active-track' : ''}" onclick="${rowClickAction}" data-idx="${idx}">
        <div class="track-num">
            <span class="num-text">${idx + 1}</span>
            <i data-lucide="play" class="hover-play-icon"></i>
            ${isActive ? '<i data-lucide="bar-chart-2" class="playing-icon"></i>' : ''}
        </div>
        <div class="track-main">
            <img src="${img}" class="track-cover-sm" onerror="this.src='/static/img/default_cover.png'">
            <div class="track-titles">
                <div class="track-name-sm">${ui.escHtml(item.title || 'Unknown')}</div>
                <div class="track-artist-sm">${ui.escHtml(item.artist || 'Unknown')}</div>
            </div>
        </div>
        <div><span class="source-badge ${src}">${src}</span></div>
        <div class="action-btns" onclick="event.stopPropagation()">
            ${!item.is_local ? `<button class="action-btn" onclick="playPreview('${data}')" title="Preview"><i data-lucide="eye"></i></button>` : ''}
            
            ${item.is_local ?
            `<button class="action-btn ${isLiked ? 'liked' : ''}" onclick="toggleFavorite(${item.db_id || item.id}, this)"><i data-lucide="heart"></i></button>` :
            `<button class="action-btn" onclick="triggerDownload('${data}')" title="Download to Library"><i data-lucide="download"></i></button>`
        }
            
            ${item.is_local ? `<button class="action-btn" onclick="addToQueue('${data}')" title="Add to Queue"><i data-lucide="list-plus"></i></button>` : ''}
            ${canAddToPlaylist ? `<button class="action-btn" onclick="showAddToPlaylistModal(${item.db_id})"><i data-lucide="plus"></i></button>` : ''}
            
            ${playlistId ? `<button class="action-btn-danger" onclick="showRemoveFromPlaylistModal(${playlistId}, ${item.id}, '${ui.escHtml(item.title || 'Track').replace(/'/g, "\\'")}')"><i data-lucide="trash-2"></i></button>` : ''}
        </div>
    </div>`;
}


// === CARD CLICK HANDLER ===

export function handleCardClick(data, cardElement) {
    if (cardElement && window.innerWidth <= 768) {
        document.querySelectorAll('.card.active-mobile').forEach(c => {
            if (c !== cardElement) c.classList.remove('active-mobile');
        });

        if (!cardElement.classList.contains('active-mobile')) {
            cardElement.classList.add('active-mobile');
            return;
        }
    }

    const track = JSON.parse(decodeURIComponent(atob(data)));

    if (track.search_query) {
        const input = document.getElementById('search-input');
        if (input) {
            input.value = track.search_query;
            search.executeSearch(track.search_query);
            return;
        }
    }

    if (track.is_local) player.playTrack(track);
    else downloads.triggerDownload(track);
}


// === PREVIEW PLAYBACK ===

export async function playPreview(data) {
    const track = JSON.parse(decodeURIComponent(atob(data)));
    ui.showToast("Loading preview...", "info");

    if (state.ytPlayer && state.ytPlayer.stopVideo) state.ytPlayer.stopVideo();
    state.audio.pause();

    try {
        let url = "";
        if (track.source === 'youtube') {
            const vidId = track.id.includes('v=') ? track.id : `https://youtube.com/watch?v=${track.id}`;
            url = `${state.API}/playback/preview?url=${encodeURIComponent(vidId)}`;
        } else if (track.id.includes('spotify.com') || track.source === 'spotify') {
            const spId = track.id.split('/').pop();
            url = `${state.API}/playback/preview?spotify_id=${spId}&title=${encodeURIComponent(track.title)}`;
        } else {
            url = `${state.API}/playback/preview?title=${encodeURIComponent(track.title + ' ' + track.artist)}`;
        }

        state.setCurrentTrack(track);
        updatePlayerUIForPreview(track);
        state.audio.src = url;
        state.audio.load();

        state.audio.play().then(() => {
            state.setIsPlaying(true);
            ui.updatePlayButton();
        }).catch(e => {
            if (e.name === 'AbortError') return;
            console.error("Preview play error:", e);
            ui.showToast("Preview failed (Format error)", "error");
        });

    } catch (e) {
        if (e.name === 'AbortError') return;
        console.error("Preview failed:", e);
        ui.showToast("Failed to load preview", "error");
    }
}

export function updatePlayerUIForPreview(track) {
    const playerTitle = document.getElementById('player-title');
    const playerArtist = document.getElementById('player-artist');
    const playerCover = document.getElementById('player-cover');

    if (playerTitle) playerTitle.innerHTML = `<span style="color:var(--accent); font-size:0.7rem; vertical-align:middle; margin-right:4px;">[PREVIEW]</span> ${ui.escHtml(track.title)}`;
    if (playerArtist) playerArtist.textContent = track.artist || 'Unknown';
    if (playerCover) playerCover.src = track.thumbnail || '/static/img/default_cover.png';

    const footer = document.querySelector('.player-footer');
    if (footer) footer.classList.remove('player-hidden');
    const mainView = document.querySelector('.main-view');
    if (mainView) mainView.classList.add('has-player');

    const fsTitle = document.getElementById('fs-title');
    const fsArtist = document.getElementById('fs-artist');
    const fsCover = document.getElementById('fs-cover');
    if (fsTitle) fsTitle.textContent = track.title || 'Unknown';
    if (fsArtist) fsArtist.textContent = track.artist || 'Unknown';
    if (fsCover) fsCover.src = track.thumbnail || '/static/img/default_cover.png';

    // Hide/disable buttons that don't apply to previews
    // Web player
    const likeBtn = document.getElementById('player-like-btn');
    const addBtn = document.getElementById('player-add-btn');
    if (likeBtn) likeBtn.style.display = 'none';
    if (addBtn) addBtn.style.display = 'none';

    // Fullscreen player - disable the action buttons
    const fsFavorite = document.getElementById('fs-btn-favorite');
    const fsActions = document.querySelector('.fs-actions');
    if (fsFavorite) fsFavorite.style.opacity = '0.3';
    if (fsFavorite) fsFavorite.style.pointerEvents = 'none';

    // Add "preview" indicator to fullscreen
    if (fsTitle) fsTitle.innerHTML = `<span style="color:var(--accent); font-size:0.65rem;">[PREVIEW]</span> ${ui.escHtml(track.title)}`;
}

// Restore player UI for normal tracks (called from player.js when playing local tracks)
export function restorePlayerUIForLocal() {
    const likeBtn = document.getElementById('player-like-btn');
    const addBtn = document.getElementById('player-add-btn');
    if (likeBtn) likeBtn.style.display = '';
    if (addBtn) addBtn.style.display = '';

    const fsFavorite = document.getElementById('fs-btn-favorite');
    if (fsFavorite) {
        fsFavorite.style.opacity = '';
        fsFavorite.style.pointerEvents = '';
    }
}


// === SIDEBAR RENDERING ===

export function renderSidebarPlaylists() {
    const container = document.getElementById('sidebar-playlists');
    if (!container) return;

    if (state.userPlaylists.length === 0) {
        container.innerHTML = '<div class="playlist-item empty" style="color:#666; font-size:0.8rem;"><i data-lucide="list-music" style="width:14px;height:14px;"></i> No playlists yet</div>';
    } else {
        container.innerHTML = state.userPlaylists.map(pl => {
            const thumb = pl.thumbnail || '/static/img/default_cover.png';
            const hasThumb = pl.thumbnail && !pl.thumbnail.includes('default');
            return `
            <div class="playlist-item" onclick="loadView('playlist', ${pl.id})">
                <div class="sidebar-playlist-thumb">
                    ${hasThumb
                    ? `<img src="${thumb}" onerror="this.src='/static/img/default_cover.png'">`
                    : '<i data-lucide="list-music"></i>'}
                </div>
                <span>${ui.escHtml(pl.name)}</span>
            </div>
        `}).join('');
    }
    lucide.createIcons();
}


// === LOAD USER DATA ===

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

        renderSidebarPlaylists();
        if (window.loadSidebarRadios) window.loadSidebarRadios();

        startHeartbeatSync();
    } catch (e) {
        console.error("Failed to load user data:", e);
    }
}


// === HEARTBEAT SYNC ===

export function startHeartbeatSync() {
    state.setHeartbeatInterval(setInterval(checkSyncState, 5000));
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
                renderSidebarPlaylists();
            }

            ui.updateFullscreenPlayButton();

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
