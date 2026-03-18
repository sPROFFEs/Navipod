/**
 * radio.js - Radio Garden Integration
 * Browse, search, play, and save radio stations
 */

import * as state from './state.js';
import * as ui from './ui.js';
import * as api from './api.js';

// === RADIO VIEW RENDERER ===

export async function renderRadio(container) {
    container.innerHTML = `
        <h1 class="section-title">Radio Explorer</h1>
        <p class="section-subtitle">Dial: <span id="radio-dial" class="text-accent">${state.currentRadioHub.toUpperCase()}</span></p>

        <h2 class="shelf-title" style="margin-bottom: 16px;">Editorial Playlists</h2>
        <div id="radio-playlists" class="grid-shelf"></div>

        <h2 class="shelf-title" style="margin: 32px 0 16px 0;">Discover Radios</h2>
        <div class="search-bar-row">
            <div class="search-input-wrapper glass-panel" style="margin:0; flex:1;">
                <i data-lucide="radio" class="search-icon"></i>
                <input type="text" id="radio-search-input" placeholder="Search city or station..." value="${state.currentRadioHub}" onkeyup="if(event.key==='Enter') executeRadioSearch()">
            </div>
            <button onclick="executeRadioSearch()" class="btn-primary">Search</button>
        </div>

        <div id="radio-results" style="margin-top: 24px;"></div>`;
    lucide.createIcons();

    loadRadioPlaylists();
    executeRadioSearch();
}


// === EDITORIAL PLAYLISTS ===

export async function loadRadioPlaylists() {
    const container = document.getElementById('radio-playlists');
    if (!container) return;

    try {
        const res = await fetch(`${state.API}/radio/browse`);
        const playlists = await res.json();
        container.innerHTML = playlists.map(pl => `
            <div class="card" onclick="loadRadioPlaylist('${pl.page.url}', '${ui.escHtml(pl.title)}')">
                <div class="card-img-container">
                    <img src="${pl.image}" loading="lazy" onerror="this.src='/static/img/default_cover.png'">
                </div>
                <div class="card-title">${ui.escHtml(pl.title)}</div>
                <div class="card-subtitle">${pl.count} radios</div>
            </div>
        `).join('');
        lucide.createIcons();
    } catch (e) {
        container.innerHTML = '<p style="color:#666;">Could not load editorial playlists.</p>';
    }
}


// === LOAD PLAYLIST ===

export async function loadRadioPlaylist(path, title) {
    ui.showToast(`Loading ${title}...`);
    const cleanPath = path.replace(/^\//, '');
    try {
        const res = await fetch(`${state.API}/radio/playlist/${cleanPath}`);
        const items = await res.json();
        const stations = items.map(i => i.page || i).filter(s => s && s.url);
        if (stations.length > 0) {
            const dial = document.getElementById('radio-dial');
            if (dial) dial.innerText = title.toUpperCase();
            drawRadioGrid(stations);
        } else {
            ui.showToast("No stations found in this playlist", "error");
        }
    } catch (e) {
        ui.showToast("Error loading playlist", "error");
    }
}


// === RADIO SEARCH ===

export async function executeRadioSearch() {
    const input = document.getElementById('radio-search-input');
    const query = input?.value?.trim();
    if (!query) return;

    const grid = document.getElementById('radio-results');
    const dial = document.getElementById('radio-dial');
    if (dial) dial.innerText = query.toUpperCase();
    if (grid) grid.innerHTML = '<p style="color:#666;">Searching...</p>';

    try {
        const res = await fetch(`${state.API}/radio/search?q=${encodeURIComponent(query)}`);
        const hits = await res.json();

        if (!hits.length) {
            if (grid) grid.innerHTML = '<p style="color:#666;">No signal found.</p>';
            return;
        }

        if (hits[0]._source.type === 'place') {
            const content = await fetch(`${state.API}/radio/place/${hits[0]._source.page.map}`);
            const channels = await content.json();
            drawRadioGrid(channels.map(c => c.page).filter(p => !p.url.endsWith('/channels')));
        } else {
            const stations = hits.filter(h => h._source.type === 'channel').map(h => h._source.page);
            drawRadioGrid(stations);
        }
    } catch (e) {
        if (grid) grid.innerHTML = '<p style="color:#e74c3c;">Connection error</p>';
    }
}


// === DRAW RADIO GRID ===

export function drawRadioGrid(stations) {
    const grid = document.getElementById('radio-results');
    if (!grid) return;

    grid.innerHTML = `<div class="grid-shelf">${stations.map(s => {
        if (!s || !s.url) return '';
        const id = s.url.split('/').pop();
        if (id === 'channels' || id.length < 5) return '';
        const title = (s.title || 'Unknown').replace(/'/g, "\\'");
        return `
            <div class="card glass-hover">
                <div class="card-img-container radio-card-bg">
                    <div class="card-placeholder-icon">
                        <i data-lucide="radio"></i>
                    </div>
                    <div class="play-overlay">
                        <div class="card-actions">
                            <button class="play-btn-card" onclick="event.stopPropagation(); playRadioStream('${id}', '${title}')" title="Preview"><i data-lucide="play"></i></button>
                            <button class="play-btn-card" onclick="event.stopPropagation(); injectRadioToNavidrome('${id}', '${title}')" title="Add to Navidrome"><i data-lucide="plus"></i></button>
                        </div>
                    </div>
                </div>
                <div class="card-title">${ui.escHtml(s.title || 'Unknown')}</div>
                <div class="card-subtitle">${ui.escHtml(s.subtitle || 'Streaming')}</div>
            </div>`;
    }).join('')}</div>`;
    lucide.createIcons();
}


// === PLAY RADIO STREAM ===

export function playRadioStream(id, name) {
    const streamUrl = `https://radio.garden/api/ara/content/listen/${id}/channel.mp3`;
    const proxyUrl = `/api/proxy/radio?url=${encodeURIComponent(streamUrl)}`;

    state.setCurrentTrack({ title: name, artist: 'Live Radio', thumbnail: '/static/img/default_cover.png', is_radio: true });

    document.getElementById('player-title').textContent = name;
    document.getElementById('player-artist').textContent = 'Live Radio';
    document.getElementById('player-cover').src = '/static/img/default_cover.png';

    const footer = document.querySelector('.player-footer');
    if (footer) footer.classList.remove('player-hidden');

    const mainView = document.querySelector('.main-view');
    if (mainView) mainView.classList.add('has-player');

    const fsTitle = document.getElementById('fs-title');
    const fsArtist = document.getElementById('fs-artist');
    const fsCover = document.getElementById('fs-cover');
    if (fsTitle) fsTitle.textContent = name;
    if (fsArtist) fsArtist.textContent = 'Live Radio';
    if (fsCover) fsCover.src = '/static/img/default_cover.png';

    state.audio.src = proxyUrl;
    state.audio.play().catch(e => console.error("Play error", e));
    ui.showToast(`Playing: ${name}`, 'success');
}


// === INJECT TO NAVIDROME ===

export async function injectRadioToNavidrome(id, name) {
    ui.showToast(`Adding ${name} to Navidrome...`);
    const fd = new FormData();
    fd.append('channel_id', id);
    fd.append('name', name);

    try {
        const res = await fetch(`${state.API}/radio/inject`, { method: 'POST', body: fd });
        const data = await res.json();
        if (data.status === 'success') {
            ui.showToast(`${name} added to Navidrome!`, 'success');
            loadSidebarRadios();
        } else {
            ui.showToast(data.error || "Failed to add radio", "error");
        }
    } catch (e) {
        ui.showToast("Network error", "error");
    }
}


// === SIDEBAR RADIOS ===

export async function loadSidebarRadios() {
    const container = document.getElementById('sidebar-radios');
    if (!container) return;

    try {
        const res = await fetch(`${state.API}/radio/list`);
        const radios = await res.json();

        if (!radios || radios.length === 0) {
            container.innerHTML = '<div class="playlist-item" style="color:#666; font-size:0.8rem;"><i data-lucide="radio" style="width:14px;height:14px;"></i> No saved radios</div>';
        } else {
            container.innerHTML = radios.map(r => `
                <div class="playlist-item" onclick="playSavedRadio('${encodeURIComponent(r.streamUrl)}', '${ui.escHtml(r.name)}')">
                    <div class="playlist-content">
                        <i data-lucide="radio"></i> 
                        <span class="truncate">${ui.escHtml(r.name)}</span>
                    </div>
                    <button class="delete-btn-sm" onclick="event.stopPropagation(); showDeleteRadioModal('${r.id}', '${ui.escHtml(r.name)}')">
                        <i data-lucide="trash-2"></i>
                    </button>
                </div>
            `).join('');
        }
        lucide.createIcons();
    } catch (e) {
        container.innerHTML = '<div class="playlist-item" style="color:#e74c3c; font-size:0.8rem;">Error loading radios</div>';
    }
}


// === PLAY SAVED RADIO ===

export function playSavedRadio(encodedUrl, name) {
    const proxyUrl = `/api/proxy/radio?url=${encodedUrl}`;

    state.setCurrentTrack({ title: name, artist: 'Saved Radio', thumbnail: '/static/img/default_cover.png', is_radio: true });

    document.getElementById('player-title').textContent = name;
    document.getElementById('player-artist').textContent = 'Saved Radio';
    document.getElementById('player-cover').src = '/static/img/default_cover.png';

    const footer = document.querySelector('.player-footer');
    if (footer) footer.classList.remove('player-hidden');
    const mainView = document.querySelector('.main-view');
    if (mainView) mainView.classList.add('has-player');

    const fsTitle = document.getElementById('fs-title');
    const fsArtist = document.getElementById('fs-artist');
    const fsCover = document.getElementById('fs-cover');
    if (fsTitle) fsTitle.textContent = name;
    if (fsArtist) fsArtist.textContent = 'Saved Radio';
    if (fsCover) fsCover.src = '/static/img/default_cover.png';

    state.audio.pause();
    state.audio.removeAttribute('src');
    state.audio.load();

    state.audio.src = proxyUrl;
    state.audio.play().catch(e => {
        if (e.name === 'AbortError') return;
        console.error("[RADIO] Play error:", e);
        ui.showToast(`Error playing ${name}. The stream might be offline.`, 'error');
    });
    ui.showToast(`Playing Saved Radio: ${name}`, 'success');
}


// === DELETE SAVED RADIO ===

export async function deleteSavedRadio(id, name) {
    ui.closeModal();
    try {
        const res = await fetch(`${state.API}/radio/${id}`, { method: 'DELETE' });
        if (res.ok) {
            ui.showToast("Radio removed", "success");
            loadSidebarRadios();
        } else {
            const err = await res.json();
            ui.showToast(err.error || "Failed to remove radio", "error");
        }
    } catch (e) {
        ui.showToast("Network error", "error");
    }
}

export function showDeleteRadioModal(id, name) {
    const html = `<div class="modal-overlay" onclick="closeModal()">
        <div class="modal" onclick="event.stopPropagation()">
            <h2 style="margin-bottom: 16px;">Remove Radio</h2>
            <p style="color: var(--text-sub); margin-bottom: 24px;">Remove <strong style="color: white;">${ui.escHtml(name)}</strong> from your saved radios?</p>
            <div class="modal-actions">
                <button class="modal-btn-cancel" onclick="closeModal()">Cancel</button>
                <button class="modal-btn-danger" onclick="deleteSavedRadio('${id}', '${ui.escHtml(name)}')">Remove</button>
            </div>
        </div>
    </div>`;
    document.getElementById('modal-container').innerHTML = html;
}
