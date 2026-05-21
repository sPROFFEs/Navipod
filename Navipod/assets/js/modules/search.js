/**
 * search.js - Search Logic
 * Query handling, source selection, and result rendering
 */

import * as state from './state.js';
import * as ui from './ui.js';

// === SEARCH INPUT HANDLER ===

export function handleSearch(val) {
  if (state.searchDebounce) clearTimeout(state.searchDebounce);
  state.setSearchDebounce(
    setTimeout(() => {
      // Previously `query.length > 1` silently skipped single-character
      // queries (U-08). Allow any length — empty shows a help state, any
      // non-empty value runs the unified search.
      executeSearch(val.trim());
    }, 400)
  );
}

// === SOURCE SELECTION ===

export function setSource(el, src) {
  // The chip click is the user's explicit signal — cancel any pending
  // debounced search from typing so we don't fire two redundant requests.
  if (state.searchDebounce) {
    clearTimeout(state.searchDebounce);
    state.setSearchDebounce(null);
  }
  state.setCurrentSource(src);
  document.querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
  el.classList.add('active');
  // The search query can come from any of (in priority order):
  //   - the desktop topbar pill        (#topbar-search-input)
  //   - the mobile in-view search pill (#m-search-input)
  //   - the legacy in-view input       (#search-input, no longer rendered)
  // We pick the first non-empty value so a chip click after typing on
  // mobile re-runs the search with the right query.
  const val =
    document.getElementById('topbar-search-input')?.value ||
    document.getElementById('m-search-input')?.value ||
    document.getElementById('search-input')?.value ||
    '';
  executeSearch(val.trim());
}

// === EXECUTE SEARCH ===

// Reusable music-bar loader strip. Sits above the results body in
// every render path; CSS shows/hides it based on .search-results-fetching
// on the parent, so the body never gets wiped on each keystroke.
const LOADER_STRIP_HTML = `
  <div class="music-loader search-loader-strip" aria-hidden="true">
    <div class="music-bar" style="animation-delay: 0.0s"></div>
    <div class="music-bar" style="animation-delay: 0.1s"></div>
    <div class="music-bar" style="animation-delay: 0.2s"></div>
    <div class="music-bar" style="animation-delay: 0.3s"></div>
    <div class="music-bar" style="animation-delay: 0.4s"></div>
  </div>`;

function renderResults(container, bodyHtml) {
  container.innerHTML = `${LOADER_STRIP_HTML}<div class="search-results-body">${bodyHtml}</div>`;
}

export async function executeSearch(query) {
  const results = document.getElementById('search-results');
  if (!results) return;

  // Always abort the previous in-flight search before starting a new
  // one. Without this, a slow upstream (yt-dlp can take 1-3s) lands its
  // response on top of a faster subsequent search, producing the
  // "previous search's results appear after switching source" bug.
  if (state.searchAbortController) {
    try {
      state.searchAbortController.abort();
    } catch (_) {
      /* noop */
    }
  }
  const controller = new AbortController();
  state.setSearchAbortController(controller);

  // Empty query: render a help/empty state and stop. The backend
  // returns [] for q='', and the frontend never had a real discovery
  // payload — surfacing "No results found" here was actively misleading.
  if (!query) {
    results.classList.remove('search-results-fetching');
    renderResults(
      results,
      '<div class="empty-state"><p>Type to search your library, federated peers, and remote sources.</p></div>'
    );
    state.setCurrentViewList([]);
    return;
  }

  // Flag the panel as fetching — CSS reveals the loader strip and
  // dims the existing body. Body content stays on screen until the
  // new render swaps it in, so there's no flash on supersede.
  results.classList.add('search-results-fetching');

  // If the panel is empty (first search after view load) make sure
  // the wrapper exists so the loader strip has somewhere to render.
  if (!results.querySelector('.search-results-body')) {
    renderResults(results, '');
  }

  try {
    const url = `${state.API}/search?q=${encodeURIComponent(query)}&source=${encodeURIComponent(state.currentSource)}`;
    const res = await fetch(url, { signal: controller.signal });

    if (!res.ok) {
      const msg =
        res.status === 401
          ? 'Your session expired. Reload the page to log in.'
          : res.status === 429
            ? 'Too many searches in a row — slow down (limit: 30/min).'
            : `Search failed (HTTP ${res.status}).`;
      renderResults(results, `<div class="empty-state" style="color:#e74c3c;"><p>${ui.escHtml(msg)}</p></div>`);
      return;
    }

    const data = await res.json();

    // Stale-response guard: a newer search may have superseded us
    // between fetch completion and json() parsing. AbortController
    // covers the fetch itself; this catches the gap.
    if (controller.signal.aborted || state.searchAbortController !== controller) return;

    if (!Array.isArray(data) || data.length === 0) {
      renderResults(
        results,
        '<div class="empty-state"><p>No results found in your library or remote sources.</p></div>'
      );
      state.setCurrentViewList([]);
      return;
    }

    state.setCurrentViewList(data);
    renderResults(
      results,
      `<div class="track-list"><div class="track-row header"><div class="track-num">#</div><div>Title</div><div>Source</div><div></div><div></div></div>${data.map((item, i) => (window.createTrackRow ? window.createTrackRow(item, i) : '')).join('')}</div>`
    );
    if (window.lucide?.createIcons) lucide.createIcons();
  } catch (e) {
    if (e.name === 'AbortError') return; // expected on supersede
    console.error('[SEARCH] Error:', e);
    renderResults(
      results,
      `<div class="empty-state" style="color:#e74c3c;">Error: ${ui.escHtml(e.message || 'Unknown error')}</div>`
    );
  } finally {
    if (state.searchAbortController === controller) {
      state.setSearchAbortController(null);
      results.classList.remove('search-results-fetching');
    }
  }
}

// === DOWNLOAD FROM URL ===

export async function downloadUrl() {
  const input = document.getElementById('url-input');
  const url = input?.value?.trim();

  if (!url) {
    ui.showToast('Please enter a URL', 'error');
    return;
  }
  // Accept all sources the backend actually supports (U-09)
  const SUPPORTED_DOMAINS = ['youtube.com', 'youtu.be', 'spotify.com', 'soundcloud.com', 'audius.co', 'jamendo.com'];
  const isSupported = SUPPORTED_DOMAINS.some((d) => url.includes(d));
  if (!isSupported) {
    ui.showToast('Invalid URL. Supported: YouTube, Spotify, SoundCloud, Audius, Jamendo.', 'error');
    return;
  }

  ui.showToast('Queuing download...');
  try {
    const res = await fetch(`${state.API}/download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, title: '', artist: '', album: '', source: '' })
    });
    if (res.ok) {
      const payload = await res.json();
      ui.showToast(payload.message || 'Download queued', 'success');
      if (input) input.value = '';
    } else {
      const err = await res.json();
      ui.showToast(err.error || 'Download failed', 'error');
    }
  } catch (e) {
    ui.showToast('Network error', 'error');
  }
}
