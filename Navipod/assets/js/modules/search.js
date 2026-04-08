/**
 * search.js - Search Logic
 * Query handling, source selection, and result rendering
 */

import * as state from './state.js';
import * as ui from './ui.js';
import * as api from './api.js';

// === SEARCH INPUT HANDLER ===

export function handleSearch(val) {
    if (state.searchDebounce) clearTimeout(state.searchDebounce);
    state.setSearchDebounce(setTimeout(() => {
        const query = val.trim();
        if (query.length === 0 || query.length > 1) {
            executeSearch(query);
        }
    }, 400));
}


// === SOURCE SELECTION ===

export function setSource(el, src) {
    state.setCurrentSource(src);
    document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
    el.classList.add('active');
    const val = document.getElementById('search-input')?.value || "";
    executeSearch(val.trim());
}


// === EXECUTE SEARCH ===

export async function executeSearch(query) {
    console.log("[SEARCH] Executing search for:", query || "(empty/discovery)");
    const results = document.getElementById('search-results');
    if (!results) return;

    results.innerHTML = `
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

    try {
        const url = `${state.API}/search?q=${encodeURIComponent(query)}&source=${state.currentSource}`;
        console.log("[SEARCH] Fetching from:", url);
        const res = await fetch(url);
        const data = await res.json();
        console.log("[SEARCH] Received data:", data);

        if (query) state.setCurrentViewList(data);

        if (!data || data.length === 0) {
            results.innerHTML = '<div class="empty-state glass-panel"><p>No results found in your library or remote sources.</p></div>';
            return;
        }

        // If empty query, render discovery grid
        if (!query) {
            console.log("[SEARCH] Rendering discovery sections with", data.length, "sections");
            let html = '';
            data.forEach(section => {
                html += `
                <div class="shelf-section">
                    <div class="shelf-header">
                        <h2 class="shelf-title">${ui.escHtml(section.title)}</h2>
                    </div>
                    <div class="grid-shelf">${section.items.map(item => window.createCard ? window.createCard(item) : '').join('')}</div>
                </div>`;
            });
            results.innerHTML = html;
            lucide.createIcons();
            return;
        }

        // Render list
        results.innerHTML = `<div class="track-list"><div class="track-row header"><div class="track-num">#</div><div>Title</div><div>Source</div><div></div><div></div></div>${data.map((item, i) => window.createTrackRow ? window.createTrackRow(item, i) : '').join('')}</div>`;
        lucide.createIcons();
    } catch (e) {
        console.error("[SEARCH] Error:", e);
        results.innerHTML = `<div class="empty-state" style="color:#e74c3c;">Error: ${e.message}</div>`;
    }
}


// === DOWNLOAD FROM URL ===

export async function downloadUrl() {
    const input = document.getElementById('url-input');
    const url = input?.value?.trim();

    if (!url) {
        ui.showToast("Please enter a URL", "error");
        return;
    }
    if (!url.includes('youtube.com') && !url.includes('youtu.be') && !url.includes('spotify.com')) {
        ui.showToast("Invalid URL. Use YouTube or Spotify links.", "error");
        return;
    }

    ui.showToast("Queuing download...");
    try {
        const res = await fetch(`${state.API}/download`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, title: '', artist: '', album: '', source: '' })
        });
        if (res.ok) {
            const payload = await res.json();
            ui.showToast(payload.message || "Download queued", "success");
            if (input) input.value = '';
        } else {
            const err = await res.json();
            ui.showToast(err.error || "Download failed", "error");
        }
    } catch (e) {
        ui.showToast("Network error", "error");
    }
}
