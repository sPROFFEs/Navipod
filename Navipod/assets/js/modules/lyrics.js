/**
 * lyrics.js - Synced lyrics fetcher + renderer
 *
 * Source: lrclib.net (proxied through /api/lyrics so we get the shared
 * 30-day cache and avoid CORS). LRC format is parsed client-side and
 * the active line is highlighted via a single rAF tick driven by the
 * existing <audio> element's `timeupdate` event.
 *
 * Design notes:
 *  - The lyrics panel is fully decoupled from the playback core. This
 *    module subscribes to track-change events and re-fetches; nothing
 *    in player.js had to be rewritten.
 *  - Two render targets: the desktop side panel and the fullscreen
 *    player overlay. They share the same DOM template — we just move
 *    the populated container between hosts.
 *  - LRC parsing is permissive: malformed timestamps are skipped
 *    rather than aborting the whole render, so a partial LRC still
 *    works.
 */

import * as state from './state.js';
import * as ui from './ui.js';

// In-memory parsed-lines for the currently visible track. Resets on
// every fetch — kept module-scoped so the rAF tick doesn't have to
// re-parse on every frame.
let _currentLines = [];        // [{time, text}]
let _currentTrackId = null;
let _activeIdx = -1;
let _isInstrumental = false;
let _hasSynced = false;
let _isVisible = false;
let _fetchAbort = null;

const PANEL_ID = 'lyrics-panel';

// === LRC PARSING ===========================================================
//
// Standard LRC line: "[mm:ss.xx] text" (xx may be 2 or 3 digits).
// A single line can carry multiple timestamps for repeated choruses.
// We expand those so the active-line tracker doesn't need to be aware.

const LRC_TIME = /\[(\d{1,2}):(\d{2}(?:[.:]\d{1,3})?)\]/g;

function parseLRC(text) {
  if (!text) return [];
  const out = [];
  const lines = text.split(/\r?\n/);
  for (const raw of lines) {
    if (!raw) continue;
    // Skip metadata tags like [ar:Foo] / [ti:Bar] — they have a colon
    // inside the brackets but no numeric timestamp at the front.
    if (/^\[[a-z]{2}:/i.test(raw) && !/^\[\d/.test(raw)) continue;

    const matches = [...raw.matchAll(LRC_TIME)];
    if (!matches.length) continue;
    // Strip every timestamp from the line to leave just the text.
    const text = raw.replace(LRC_TIME, '').trim();
    for (const m of matches) {
      const min = parseInt(m[1], 10) || 0;
      const sec = parseFloat((m[2] || '0').replace(':', '.')) || 0;
      const time = min * 60 + sec;
      out.push({ time, text });
    }
  }
  out.sort((a, b) => a.time - b.time);
  return out;
}

// Plain lyrics → fake "lines" with no timestamps so the same renderer
// can show them as a non-synced scrolling list.
function plainToLines(text) {
  if (!text) return [];
  return text.split(/\r?\n/).map((t) => ({ time: -1, text: t }));
}

// === FETCH =================================================================

export async function loadLyricsFor(track) {
  if (!track) {
    _renderEmpty('Nothing playing.');
    return;
  }

  const tid = track.db_id || track.id || `${track.artist}|${track.title}`;
  if (tid === _currentTrackId) return;
  _currentTrackId = tid;

  // Cancel any in-flight fetch for the previous track so we don't race.
  if (_fetchAbort) {
    try { _fetchAbort.abort(); } catch (_) {}
  }
  _fetchAbort = new AbortController();

  _renderLoading();

  const params = new URLSearchParams({
    title: track.title || '',
    artist: track.artist || '',
    album: track.album || '',
    duration: String(Math.floor(state.audio?.duration || track.duration || 0)),
  });

  try {
    const res = await fetch(`${state.API}/lyrics?${params}`, { signal: _fetchAbort.signal });
    if (!res.ok) {
      _renderEmpty('Could not load lyrics.');
      return;
    }
    const data = await res.json();

    _isInstrumental = !!data.instrumental;
    if (_isInstrumental) {
      _currentLines = [];
      _hasSynced = false;
      _renderEmpty('🎼 Instrumental track.');
      return;
    }

    if (data.synced) {
      _currentLines = parseLRC(data.synced);
      _hasSynced = true;
    } else if (data.plain) {
      _currentLines = plainToLines(data.plain);
      _hasSynced = false;
    } else {
      _currentLines = [];
      _hasSynced = false;
    }

    if (!_currentLines.length) {
      _renderEmpty('No lyrics found for this track.');
      return;
    }

    _renderLines();
    _activeIdx = -1;
    if (_hasSynced) tick();
  } catch (e) {
    if (e.name === 'AbortError') return;
    console.warn('[LYRICS] fetch failed', e);
    _renderEmpty('Could not load lyrics.');
  }
}

// === RENDER ================================================================

function _container() {
  return document.getElementById(PANEL_ID);
}

function _body() {
  return document.querySelector(`#${PANEL_ID} .lyrics-body`);
}

function _renderEmpty(message) {
  const body = _body();
  if (!body) return;
  body.innerHTML = `<div class="lyrics-empty">${ui.escHtml(message)}</div>`;
}

function _renderLoading() {
  const body = _body();
  if (!body) return;
  body.innerHTML = `<div class="lyrics-empty">Loading lyrics…</div>`;
}

function _renderLines() {
  const body = _body();
  if (!body) return;
  if (!_currentLines.length) {
    _renderEmpty('No lyrics.');
    return;
  }
  body.innerHTML = _currentLines
    .map((l, i) => {
      const text = (l.text || '').trim() || '♪';
      return `<div class="lyric-line" data-idx="${i}">${ui.escHtml(text)}</div>`;
    })
    .join('');
}

// === ACTIVE-LINE TRACKING ==================================================
//
// Driven by the audio element's currentTime via the existing
// `timeupdate` listener (~4Hz). For 60Hz precision we'd need a rAF
// loop, but the visual difference is imperceptible and the simpler
// approach avoids draining battery on mobile.

export function tick() {
  if (!_isVisible || !_hasSynced || !_currentLines.length) return;
  const t = (state.audio && state.audio.currentTime) || 0;

  // Find the last line whose timestamp is <= t. Linear from the
  // current index forward (and a small backstep) — usually one
  // comparison per tick.
  let idx = _activeIdx;
  if (idx < 0 || _currentLines[idx]?.time > t) idx = 0;
  while (idx + 1 < _currentLines.length && _currentLines[idx + 1].time <= t) {
    idx++;
  }
  if (idx === _activeIdx) return;

  _activeIdx = idx;
  const body = _body();
  if (!body) return;
  body.querySelectorAll('.lyric-line.active').forEach((el) => el.classList.remove('active'));
  const el = body.querySelector(`.lyric-line[data-idx="${idx}"]`);
  if (el) {
    el.classList.add('active');
    // Keep active line vertically centered. Use 'nearest' to avoid
    // scrolling when the line is already inside the viewport.
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

// === PANEL TOGGLE ==========================================================

export function toggleLyricsPanel() {
  const panel = _container();
  if (!panel) return;
  _isVisible = !panel.classList.contains('open');
  if (_isVisible) {
    panel.classList.add('open');
    document.body.classList.add('lyrics-panel-open');
    if (state.currentTrack) loadLyricsFor(state.currentTrack);
    if (window.lucide) lucide.createIcons();
  } else {
    panel.classList.remove('open');
    document.body.classList.remove('lyrics-panel-open');
  }
  // Keep the toolbar buttons in sync across both player surfaces.
  document.querySelectorAll('.lyrics-toggle-btn').forEach((b) => {
    b.classList.toggle('active', _isVisible);
  });
}

export function closeLyricsPanel() {
  const panel = _container();
  if (!panel) return;
  panel.classList.remove('open');
  document.body.classList.remove('lyrics-panel-open');
  _isVisible = false;
  document.querySelectorAll('.lyrics-toggle-btn').forEach((b) => b.classList.remove('active'));
}

export function isLyricsPanelOpen() {
  return _isVisible;
}

// === INIT ==================================================================

export function initLyrics() {
  // Inject the panel skeleton once. We attach to <body> so it can
  // overlay both the regular player and the fullscreen player without
  // worrying about z-index stacking inside .player-footer.
  if (_container()) return;

  const panel = document.createElement('aside');
  panel.id = PANEL_ID;
  panel.className = 'lyrics-panel';
  panel.innerHTML = `
    <header class="lyrics-head">
      <div class="lyrics-head-meta">
        <i data-lucide="mic-vocal"></i>
        <span>Lyrics</span>
      </div>
      <button class="lyrics-close" onclick="closeLyricsPanel()" title="Close">
        <i data-lucide="x"></i>
      </button>
    </header>
    <div class="lyrics-body"></div>
    <footer class="lyrics-foot">
      <span class="lyrics-credit">via lrclib.net</span>
    </footer>`;
  document.body.appendChild(panel);
  if (window.lucide) lucide.createIcons();

  // Hook the audio element's timeupdate so synced lyrics scroll without
  // the player.js core having to know about us.
  if (state.audio) {
    state.audio.addEventListener('timeupdate', () => {
      if (_isVisible && _hasSynced) tick();
    });
  }
}

// Called from player.js whenever the current track changes — it's a
// no-op when the panel is closed, so no fetch happens unless the user
// has lyrics open.
export function onTrackChange(track) {
  if (!_isVisible) return;
  loadLyricsFor(track);
}
