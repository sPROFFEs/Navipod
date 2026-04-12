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

const SECRET_EYE_ICON = `
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"></path>
        <circle cx="12" cy="12" r="3"></circle>
    </svg>
`;

const SECRET_EYE_OFF_ICON = `
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-5 0-9.27-3.11-11-8 1-2.68 2.87-4.9 5.31-6.34"></path>
        <path d="M9.9 4.24A10.94 10.94 0 0 1 12 4c5 0 9.27 3.11 11 8a11.05 11.05 0 0 1-4.08 5.36"></path>
        <path d="M14.12 14.12A3 3 0 1 1 9.88 9.88"></path>
        <path d="M3 3l18 18"></path>
    </svg>
`;

const MIX_META = {
    repeat: {
        summary: 'Your most replayed tracks, weighted by favorites and recent full listens.',
        short: 'Your most played local tracks',
    },
    deep_cuts: {
        summary: 'Tracks with solid history that are not the obvious top repeats.',
        short: 'Repeated tracks outside the obvious top',
    },
    favorites: {
        summary: 'Liked songs mixed with tracks from the same artists and albums already in your library.',
        short: 'Liked songs and their closest local neighbors',
    },
    rediscovery: {
        summary: 'Local tracks you liked before but have not played much lately.',
        short: 'Older local favorites worth bringing back',
    },
};

function renderSecretToggleIcon(button, revealed) {
    const iconTarget = button.querySelector('.secret-toggle-icon');
    if (!iconTarget) return;
    iconTarget.innerHTML = revealed ? SECRET_EYE_OFF_ICON : SECRET_EYE_ICON;
}

function initUserSettingsView(container) {
    const userSettingsShell = container.querySelector('.user-settings-shell');
    if (!userSettingsShell) return;

    const avatarInput = userSettingsShell.querySelector('#avatar-input');
    const avatarForm = userSettingsShell.querySelector('#avatar-form');
    if (avatarInput && avatarForm && avatarInput.dataset.bound !== 'true') {
        avatarInput.dataset.bound = 'true';
        avatarInput.addEventListener('change', function () {
            if (this.files && this.files.length > 0 && typeof htmx !== 'undefined') {
                htmx.trigger(avatarForm, 'submit');
            }
        });
    }

    userSettingsShell.querySelectorAll('.toggle-secret-btn').forEach((button) => {
        if (button.dataset.bound === 'true') return;
        button.dataset.bound = 'true';

        const wrapper = button.closest('.user-settings-secret');
        const input = wrapper?.querySelector('.secret-input');
        if (!input) return;

        renderSecretToggleIcon(button, input.type !== 'password');
        button.addEventListener('click', () => {
            const reveal = input.type === 'password';
            input.type = reveal ? 'text' : 'password';
            renderSecretToggleIcon(button, reveal);
        });
    });
}

// === VIEW ROUTING ===

function _canTrackSpaHistory(view) {
    return !['settings_admin', 'system_monitor', 'settings_user', 'help'].includes(view);
}


function _pushSpaHistory(view, param = null, replace = false) {
    if (!_canTrackSpaHistory(view)) return;
    const statePayload = { navipodView: view, navipodParam: param };
    if (replace) {
        window.history.replaceState(statePayload, '');
    } else {
        window.history.pushState(statePayload, '');
    }
}


export async function loadView(view, param = null, options = {}) {
    const container = document.getElementById('view-container');
    if (!container) return;
    if (view === 'radio') view = 'discover_radios';
    const { pushHistory = true, replaceHistory = false } = options;

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
    const navView = view === 'mix' ? 'home' : view;
    const link = document.querySelector(`.nav-link[onclick*="'${navView}'"]`);
    if (link) link.classList.add('active');

    try {
        state.setCurrentViewName(view);
        if (pushHistory) {
            _pushSpaHistory(view, param, replaceHistory);
        }

        if (view === 'home') await renderHome(container);
        else if (view === 'library') await renderLibrary(container);
        else if (view === 'mix') await renderMix(container, param);
        else if (view === 'search') renderSearch(container);
        else if (view === 'public') await playlists.renderPublicPlaylists(container);
        else if (view === 'discover_radios') await radio.renderRadio(container);
        else if (view === 'your_radios') await radio.renderSavedRadios(container);
        else if (view === 'favorites') await favorites.renderFavorites(container);
        else if (view === 'playlist') {
            await playlists.renderPlaylist(container, param);
            await trackRecentPlaylist(param);
        }
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

        if (url === '/user/settings') {
            initUserSettingsView(container);
        }

        const scripts = doc.querySelectorAll('script[src]');
        for (const script of scripts) {
            const src = script.getAttribute('src');
            if (!src) continue;
            if (src.includes('/assets/js/main.js') || src.includes('/assets/js/modules/admin_system.js')) {
                continue;
            }
            if (!document.querySelector(`script[src="${src}"]`)) {
                await new Promise((resolve, reject) => {
                    const s = document.createElement('script');
                    s.src = src;
                    const scriptType = script.getAttribute('type');
                    if (scriptType) s.type = scriptType;
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


export function initSpaHistory() {
    if (window.__navipodSpaHistoryBound) return;
    window.__navipodSpaHistoryBound = true;

    window.addEventListener('popstate', (event) => {
        const historyState = event.state;
        if (historyState?.navipodView) {
            loadView(historyState.navipodView, historyState.navipodParam ?? null, { pushHistory: false });
            return;
        }
        if (window.location.pathname === '/portal' || window.location.pathname === '/' || window.location.pathname === '/index.html') {
            loadView('home', null, { pushHistory: false });
        }
    });
}

document.body.addEventListener('htmx:afterSwap', (event) => {
    if (event.target?.id !== 'view-container') return;
    const sidebarAvatar = document.querySelector('.user-menu .avatar-circle img');
    if (sidebarAvatar && window.USER_DATA?.username) {
        sidebarAvatar.src = `/user/avatar/${window.USER_DATA.username}?t=${Date.now()}`;
    }
});


// === HOME VIEW ===

export async function renderHome(container) {
    let sections = [];
    let mixes = [];
    state.setCurrentViewList([]);

    try {
        const [recommendations, personalizedMixes] = await Promise.all([
            api.fetchRecommendations(),
            api.fetchMixes(),
        ]);
        sections = recommendations;
        mixes = personalizedMixes;
    } catch (e) {
        console.error("Recs error:", e);
    }

    const username = ui.escHtml(window.USER_DATA?.username || 'User');
    let html = `<section class="hero-section">
        <div class="hero-kicker">Your library, tuned for right now</div>
        <h1 class="hero-greeting">Good ${ui.getGreeting()}, <span class="hero-username">${username}</span></h1>
    </section>`;

    if (mixes && mixes.length > 0) {
        html += `
            <div class="shelf-section">
                <div class="shelf-header">
                    <h2 class="shelf-title">Your Mixes</h2>
                </div>
                <div class="grid-shelf">${mixes.map(createMixCard).join('')}</div>
            </div>`;
    }

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
        <div class="search-input-wrapper glass-panel search-panel">
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


export async function renderMix(container, mixKey) {
    const mix = await api.fetchMixDetail(mixKey);
    if (!mix || mix.error) {
        container.innerHTML = `<div class="empty-state glass-panel"><p>Mix not available.</p></div>`;
        return;
    }

    const tracks = (mix.items || []).map(item => ({
        ...item,
        id: item.db_id || item.id,
        db_id: item.db_id || item.id,
        mix_key: mix.key,
        is_local: true,
        source: 'local',
    }));
    state.setCurrentViewList(tracks);
    const mixMeta = MIX_META[mix.key] || {};

    container.innerHTML = `
        <div class="playlist-header-section">
            <div class="playlist-cover-large">
                <img src="${mix.thumbnail || '/static/img/default_cover.png'}" onerror="this.src='/static/img/default_cover.png'">
            </div>
            <div class="playlist-info">
                <p class="playlist-type">Personal Mix</p>
                <div class="playlist-title-row">
                    <h1 class="playlist-title">${ui.escHtml(mix.title || 'Mix')}</h1>
                </div>
                <p class="playlist-stats">${mix.track_count || tracks.length} songs · ${ui.escHtml(mixMeta.summary || 'Built from your local library')}</p>
                <div class="playlist-actions">
                    ${tracks.length > 0 ? `
                    <button onclick="playPlaylistInOrder()" class="btn-primary-lg playlist-action-btn" title="Play" aria-label="Play">
                        <i data-lucide="play" width="20" height="20"></i>
                        <span class="playlist-btn-label">Play</span>
                    </button>
                    <button onclick="playPlaylistShuffle()" class="btn-secondary-lg playlist-action-btn" title="Shuffle" aria-label="Shuffle">
                        <i data-lucide="shuffle" width="20" height="20"></i>
                        <span class="playlist-btn-label">Shuffle</span>
                    </button>` : ''}
                    <button onclick="showSaveMixModal('${ui.escHtml(mix.key).replace(/'/g, "\\'")}', '${ui.escHtml(mix.title || 'Mix').replace(/'/g, "\\'")}')" class="btn-secondary-lg playlist-action-btn" title="Save as playlist" aria-label="Save as playlist">
                        <i data-lucide="save" width="20" height="20"></i>
                        <span class="playlist-btn-label">Save as Playlist</span>
                    </button>
                </div>
            </div>
        </div>
        ${tracks.length > 0
            ? `<div class="track-list">${tracks.map((t, i) => createTrackRow(t, i, null)).join('')}</div>`
            : '<div class="empty-state glass-panel"><p>This mix is empty.</p></div>'}`;
    lucide.createIcons();
}


export async function renderLibrary(container) {
    let playlistList = [];
    try {
        const res = await fetch(`${state.API}/playlists`);
        playlistList = await res.json();
        if (!res.ok) throw new Error(playlistList.error || `HTTP ${res.status}`);
        state.setUserPlaylists(playlistList);
        renderSidebarRecents();
    } catch (e) {
        container.innerHTML = `<div class="empty-state glass-panel"><p>Failed to load your library.</p></div>`;
        return;
    }

    container.innerHTML = `
        <section class="collection-shell">
            <div class="collection-header">
                <div>
                    <h1 class="section-title">Library</h1>
                </div>
                <button class="btn-primary" onclick="showCreatePlaylistModal()">
                    <i data-lucide="plus"></i>
                    <span>New Playlist</span>
                </button>
            </div>
            ${playlistList.length > 0
                ? `<div class="grid-shelf playlist-mobile-list">${playlistList.map(createPlaylistCard).join('')}</div>`
                : `<div class="empty-state glass-panel"><p>No playlists yet. Create one and stop hiding your library in the sidebar.</p></div>`}
        </section>`;
    lucide.createIcons();
}


// === CARD COMPONENT ===

export function createCard(item) {
    const img = item.thumbnail || '/static/img/default_cover.png';
    const data = btoa(encodeURIComponent(JSON.stringify(item)));
    const sourceMap = {
        'spotify': { icon: 'music', color: '#1DB954' },
        'youtube': { icon: 'tv', color: '#ff0000' },
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
    const ownerLine = pl.owner_username && !pl.is_owner ? `By ${ui.escHtml(pl.owner_username)} - ` : '';
    const visibilityLabel = pl.source_playlist_id ? 'Synced copy' : (pl.is_public ? 'Public' : 'Private');

    return `<div class="card glass-hover" onclick="loadView('playlist', ${pl.id})">
        <div class="card-img-container playlist-card-bg">
            ${hasThumb ? `<img src="${thumb}" class="card-img" onerror="this.src='/static/img/default_cover.png'">` : `<i data-lucide="list-music" class="playlist-icon-large"></i>`}
        </div>
        <div class="playlist-card-copy">
            <div class="card-title">${ui.escHtml(pl.name)}</div>
            <div class="card-subtitle">${ownerLine}${pl.track_count} tracks - ${visibilityLabel}</div>
        </div>
    </div>`;
}

export function createMixCard(mix) {
    const thumb = mix.thumbnail || '/static/img/default_cover.png';
    const mixMeta = MIX_META[mix.key] || {};
    return `<div class="card glass-hover" onclick="loadView('mix', '${ui.escHtml(mix.key).replace(/'/g, "\\'")}')">
        <div class="card-img-container playlist-card-bg">
            <img src="${thumb}" class="card-img" onerror="this.src='/static/img/default_cover.png'">
        </div>
        <div class="playlist-card-copy">
            <div class="card-title">${ui.escHtml(mix.title || 'Mix')}</div>
            <div class="card-subtitle">${ui.escHtml(mixMeta.short || `${mix.track_count || 0} tracks`)}</div>
        </div>
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

    player.syncPlayerShellVisibility(track);

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

export function renderSidebarRecents() {
    const playlistContainer = document.getElementById('sidebar-recent-playlists');
    const radioContainer = document.getElementById('sidebar-recent-radios');

    if (playlistContainer) {
        playlistContainer.innerHTML = state.recentPlaylists.length > 0
            ? state.recentPlaylists.map(pl => {
                const thumb = pl.thumbnail || '/static/img/default_cover.png';
                const hasThumb = pl.thumbnail && !pl.thumbnail.includes('default');
                const playlistIcon = pl.source_playlist_id ? 'refresh-cw' : (pl.is_public ? 'globe' : 'list-music');
                return `
                    <div class="playlist-item" onclick="loadView('playlist', ${pl.id})">
                        <div class="sidebar-playlist-thumb">
                            ${hasThumb
                                ? `<img src="${thumb}" onerror="this.src='/static/img/default_cover.png'">`
                                : `<i data-lucide="${playlistIcon}"></i>`}
                        </div>
                        <span class="truncate">${ui.escHtml(pl.name)}</span>
                    </div>`;
            }).join('')
            : '<div class="playlist-item empty recent-empty">No recent playlists</div>';
    }

    if (radioContainer) {
        radioContainer.innerHTML = state.recentRadios.length > 0
            ? state.recentRadios.map(radioItem => {
                const radioName = ui.escHtml(radioItem.name || 'Saved Radio');
                const radioNameJs = String(radioItem.name || 'Saved Radio').replace(/'/g, "\\'");
                const radioIdJs = String(radioItem.id || '').replace(/'/g, "\\'");
                return `
                    <div class="playlist-item radio-item" onclick="playSavedRadio('${encodeURIComponent(radioItem.streamUrl || '')}', '${radioNameJs}', '${radioIdJs}')">
                        <div class="sidebar-playlist-thumb">
                            <i data-lucide="radio"></i>
                        </div>
                        <span class="truncate">${radioName}</span>
                    </div>`;
            }).join('')
            : '<div class="playlist-item empty recent-empty">No recent radios</div>';
    }

    lucide.createIcons();
}


export function renderSidebarPlaylists() {
    renderSidebarRecents();
}


export async function refreshRecentActivity() {
    try {
        const res = await fetch(`${state.API}/recent-activity`);
        if (!res.ok) return;
        const data = await res.json();
        state.setRecentPlaylists(Array.isArray(data.playlists) ? data.playlists : []);
        state.setRecentRadios(Array.isArray(data.radios) ? data.radios : []);
        renderSidebarRecents();
    } catch (e) {
        console.error("Failed to refresh recent activity:", e);
    }
}


export async function trackRecentPlaylist(playlistId) {
    if (!playlistId) return;
    try {
        await fetch(`${state.API}/recent-activity/playlist`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ playlist_id: Number(playlistId) })
        });
        await refreshRecentActivity();
    } catch (e) {
        console.error("Failed to track recent playlist:", e);
    }
}


// === LOAD USER DATA ===

export async function loadUserData() {
    try {
        const [favsRes, playlistsRes, recentsRes] = await Promise.all([
            fetch(`${state.API}/favorites`),
            fetch(`${state.API}/playlists`),
            fetch(`${state.API}/recent-activity`)
        ]);
        const favs = await favsRes.json();
        const pls = await playlistsRes.json();
        const recents = recentsRes.ok ? await recentsRes.json() : { playlists: [], radios: [] };

        state.setUserFavorites(new Set(favs.map(f => f.id)));
        state.setUserPlaylists(pls);
        state.setRecentPlaylists(Array.isArray(recents.playlists) ? recents.playlists : []);
        state.setRecentRadios(Array.isArray(recents.radios) ? recents.radios : []);

        renderSidebarRecents();

        startHeartbeatSync();
    } catch (e) {
        console.error("Failed to load user data:", e);
    }
}


export function showSaveMixModal(mixKey, mixTitle) {
    const defaultName = ui.escHtml(mixTitle || 'New Mix').replace(/'/g, '&#39;');
    const html = `<div class="modal-overlay" onclick="closeModal()">
        <div class="modal modal-playlist" onclick="event.stopPropagation()">
            <div class="modal-header">
                <h2><i data-lucide="save"></i> Save Mix as Playlist</h2>
                <button class="modal-close" onclick="closeModal()"><i data-lucide="x"></i></button>
            </div>
            <div class="modal-body">
                <label class="settings-label" for="mix-save-name">Playlist Name</label>
                <input id="mix-save-name" class="modal-input" type="text" value="${defaultName}" maxlength="120" placeholder="Choose a playlist name">
                <div class="playlist-actions" style="margin-top:16px;">
                    <button class="btn-primary-lg playlist-action-btn" onclick="saveMixAsPlaylistAction('${mixKey}')">
                        <i data-lucide="save" width="18" height="18"></i>
                        <span class="playlist-btn-label">Save</span>
                    </button>
                </div>
            </div>
        </div>
    </div>`;
    document.getElementById('modal-container').innerHTML = html;
    lucide.createIcons();
    document.getElementById('mix-save-name')?.focus();
}


export async function saveMixAsPlaylistAction(mixKey) {
    const input = document.getElementById('mix-save-name');
    const name = (input?.value || '').trim();
    if (!name) {
        ui.showToast('Playlist name is required', 'error');
        return;
    }

    const saved = await api.saveMixAsPlaylist(mixKey, name);
    if (!saved) {
        ui.showToast('Failed to save mix', 'error');
        return;
    }

    ui.closeModal();
    ui.showToast('Mix saved as playlist', 'success');
    await loadUserData();
    loadView('playlist', saved.id);
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
                await refreshRecentActivity();
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
