/**
 * ui.js - UI Utilities
 * Toast notifications, modals, formatters, draggable controls
 */

import * as state from './state.js';

// === FORMATTERS ===

export function fmtTime(s) {
  if (!s || isNaN(s) || !isFinite(s)) return '0:00';
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec < 10 ? '0' : ''}${sec}`;
}

export function getGreeting() {
  const h = new Date().getHours();
  return h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening';
}

export function escHtml(str) {
  return str ? str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;') : '';
}

// Scoped lucide icon refresh. lucide.createIcons() with no args walks the
// WHOLE document — on a freshly-rendered home page (hundreds of cards) that
// triggers a layout/reflow Safari iOS can't keep up with → the freeze.
// Pass an Element to scope the work to its subtree.
export function refreshIcons(scope) {
  if (!window.lucide?.createIcons) return;
  if (scope && scope.querySelectorAll) {
    const nodes = scope.querySelectorAll('[data-lucide]');
    if (nodes.length) window.lucide.createIcons({ nodes });
    return;
  }
  window.lucide.createIcons();
}

/**
 * Spotify-style tab bar shown at the top of every Browse-level surface
 * (Home / Party / Public / Discover / Radios) so users can flip between them
 * without leaving the visual context. Each chip calls loadView() with
 * the corresponding canonical view name; the active one is highlighted
 * based on the argument.
 *
 * Note: 'Discover Radios' is shortened to just 'Radios' here because
 * we now also have a 'Discover' tab for the preview-feed view, and
 * the two side by side were ambiguous.
 */
export function homeTabsBar(activeTab) {
  const tabs = [
    { key: 'all', label: 'All', view: 'home' },
    { key: 'party', label: 'Party', view: 'party' },
    { key: 'public', label: 'Public', view: 'public' },
    { key: 'discovery', label: 'Discover', view: 'discovery' },
    { key: 'discover_radios', label: 'Radios', view: 'discover_radios' }
  ];
  return `
    <div class="home-tabs">
        ${tabs
          .map(
            (t) =>
              `<button class="home-tab${t.key === activeTab ? ' active' : ''}"
                       onclick="loadView('${t.view}')">${t.label}</button>`
          )
          .join('')}
    </div>`;
}

// === TOAST NOTIFICATIONS ===
//
// Spotify-aligned: flat charcoal surface, no blur, green accent for
// success/error color-coding via left border. Supports an optional
// action button (e.g. "Undo") rendered inline to the right of the
// message — matches Spotify's "Added to X" pattern.

let _toastTimer = null;

/**
 * Show a toast notification.
 * @param {string} msg - Message text.
 * @param {'info'|'success'|'error'} type - Controls accent color.
 * @param {{label:string, callback:Function}|null} action - Optional
 *   inline action button (e.g. {label:'Undo', callback:fn}). When
 *   provided, the toast stays for 5s (vs 3s) and dismisses on click.
 */
export function showToast(msg, type = 'info', action = null) {
  document.querySelectorAll('.toast-msg').forEach((t) => t.remove());
  if (_toastTimer) {
    clearTimeout(_toastTimer);
    _toastTimer = null;
  }
  const toast = document.createElement('div');
  toast.className = `toast-msg toast-${type}`;
  if (action) toast.classList.add('toast-msg--action');

  const text = document.createElement('span');
  text.className = 'toast-msg-text';
  text.innerText = msg;
  toast.appendChild(text);

  if (action) {
    const btn = document.createElement('button');
    btn.className = 'toast-msg-action';
    btn.innerText = action.label;
    btn.onclick = () => {
      _dismissToast(toast);
      try { action.callback(); } catch (e) { console.error('[toast] action error:', e); }
    };
    toast.appendChild(btn);
  }

  document.body.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('toast-msg--show'));
  _toastTimer = setTimeout(() => _dismissToast(toast), action ? 5000 : 3000);
}

function _dismissToast(toast) {
  if (!toast || !toast.parentNode) return;
  if (_toastTimer) { clearTimeout(_toastTimer); _toastTimer = null; }
  toast.classList.remove('toast-msg--show');
  setTimeout(() => toast.remove(), 300);
}

// === MODAL UTILITIES ===

export function closeModal() {
  document.getElementById('modal-container').innerHTML = '';
}

// === VOLUME MUTE TOGGLE ===

let savedVolume = 0.7;

export function toggleMute() {
  const btn = document.getElementById('btn-volume-icon');
  if (state.audio.volume > 0) {
    savedVolume = state.audio.volume;
    state.audio.volume = 0;
    if (btn) btn.innerHTML = '<i data-lucide="volume-x"></i>';
  } else {
    state.audio.volume = savedVolume;
    if (btn) btn.innerHTML = '<i data-lucide="volume-2"></i>';
  }

  // Update volume bar visual
  const volumeFill = document.querySelector('.volume-bar-fill');
  const volumeKnob = document.querySelector('.volume-knob');
  const pct = state.audio.volume * 100;
  if (volumeFill) volumeFill.style.width = `${pct}%`;
  if (volumeKnob) volumeKnob.style.left = `${pct}%`;

  lucide.createIcons();
}

// === VOLUME NUDGE (keyboard shortcuts) ===

export function nudgeVolume(delta) {
  state.audio.volume = Math.max(0, Math.min(1, state.audio.volume + delta));

  const volumeFill = document.querySelector('.volume-bar-fill');
  const volumeKnob = document.querySelector('.volume-knob');
  const pct = state.audio.volume * 100;
  if (volumeFill) volumeFill.style.width = `${pct}%`;
  if (volumeKnob) volumeKnob.style.left = `${pct}%`;
}

// === SLEEP TIMER ===
// Cycles Off → 15 → 30 → 60 min → Off. When it fires, playback pauses.
// ponytail: plain setTimeout — survives view changes (module-level), not reloads.

const SLEEP_STEPS_MIN = [15, 30, 60];
let sleepTimerId = null;
let sleepTimerMinutes = 0;

function _setSleepButtonState(active) {
  document.querySelectorAll('#fs-btn-sleep').forEach((btn) => {
    btn.classList.toggle('active', active);
    btn.style.color = active ? 'var(--accent, #1DB954)' : '';
  });
}

export function cycleSleepTimer() {
  if (sleepTimerId) {
    clearTimeout(sleepTimerId);
    sleepTimerId = null;
  }

  const idx = SLEEP_STEPS_MIN.indexOf(sleepTimerMinutes);
  const next = idx === -1 ? SLEEP_STEPS_MIN[0] : SLEEP_STEPS_MIN[idx + 1] || 0;
  sleepTimerMinutes = next;

  if (!next) {
    _setSleepButtonState(false);
    showToast('Sleep timer off');
    return;
  }

  sleepTimerId = setTimeout(
    () => {
      sleepTimerId = null;
      sleepTimerMinutes = 0;
      _setSleepButtonState(false);
      if (!state.audio.paused) state.audio.pause();
      showToast('Sleep timer: playback paused');
    },
    next * 60 * 1000
  );
  _setSleepButtonState(true);
  showToast(`Sleep timer: ${next} min`);
}

// === DRAGGABLE CONTROLS ===
// For progress bars and volume sliders with touch support

export function setupDraggable(element, callback) {
  if (!element) return;
  let isDragging = false;
  const knob = element.querySelector('.progress-knob, .volume-knob, .fs-progress-knob');

  const getPositionFromEvent = (e) => {
    const rect = element.getBoundingClientRect();
    let clientX = e.clientX;
    if (e.touches && e.touches.length > 0) {
      clientX = e.touches[0].clientX;
    }
    let pct = (clientX - rect.left) / rect.width;
    return Math.max(0, Math.min(1, pct));
  };

  const update = (e) => {
    const pct = getPositionFromEvent(e);
    const fill = element.querySelector('.progress-bar-fill, .volume-bar-fill, .fs-progress-fill');
    if (fill) fill.style.width = `${pct * 100}%`;
    if (knob) {
      knob.style.left = `${pct * 100}%`;
      knob.style.transform = `translate(-50%, -50%)`;
    }
    callback(pct, isDragging);
  };

  // Mouse events
  element.addEventListener('mousedown', (e) => {
    isDragging = true;
    update(e);
  });

  document.addEventListener('mousemove', (e) => {
    if (isDragging) {
      e.preventDefault();
      update(e);
    }
  });

  document.addEventListener('mouseup', (e) => {
    if (isDragging) {
      isDragging = false;
      callback(getPositionFromEvent(e), false);
    }
  });

  // Touch events for mobile
  element.addEventListener(
    'touchstart',
    (e) => {
      isDragging = true;
      update(e);
    },
    { passive: true }
  );

  element.addEventListener(
    'touchmove',
    (e) => {
      if (isDragging) {
        e.preventDefault();
        update(e);
      }
    },
    { passive: false }
  );

  element.addEventListener('touchend', (e) => {
    if (isDragging) {
      isDragging = false;
      const lastTouch = e.changedTouches[0];
      if (lastTouch) {
        const rect = element.getBoundingClientRect();
        let pct = (lastTouch.clientX - rect.left) / rect.width;
        pct = Math.max(0, Math.min(1, pct));
        callback(pct, false);
      }
    }
  });
}

// === FULLSCREEN PLAYER ===

export function toggleFullscreenPlayer() {
  const panel = document.getElementById('fullscreen-player');
  state.setIsFullscreenPlayerOpen(!state.isFullscreenPlayerOpen);

  if (state.isFullscreenPlayerOpen) {
    panel.classList.add('open');
    updateFullscreenPlayButton();
    refreshIcons(panel);
  } else {
    panel.classList.remove('open');
  }
}

export function updateFullscreenPlayButton() {
  const fsPlayBtn = document.getElementById('fs-play-pause-btn');
  const fsShuffle = document.getElementById('fs-btn-shuffle');
  const fsRepeat = document.getElementById('fs-btn-repeat');
  const fsFavorite = document.getElementById('fs-btn-favorite');

  if (fsPlayBtn) {
    fsPlayBtn.innerHTML = `<i data-lucide="${state.isPlaying ? 'pause' : 'play'}"></i>`;
    fsPlayBtn.classList.toggle('is-playing', state.isPlaying);
    fsPlayBtn.setAttribute('aria-label', state.isPlaying ? 'Pause' : 'Play');
    fsPlayBtn.title = state.isPlaying ? 'Pause' : 'Play';
  }

  if (fsShuffle) {
    fsShuffle.classList.toggle('active-control', state.shuffleMode);
  }

  if (fsRepeat) {
    fsRepeat.classList.toggle('active-control', state.repeatMode !== 'off');
    if (state.repeatMode === 'one') {
      fsRepeat.innerHTML = `<i data-lucide="repeat-1"></i>`;
    } else {
      fsRepeat.innerHTML = `<i data-lucide="repeat"></i>`;
    }
  }

  if (fsFavorite && state.currentTrack) {
    const trackId = state.currentTrack.db_id || state.currentTrack.id;
    const isFav = state.userFavorites.has(trackId);
    fsFavorite.classList.toggle('active', isFav);
    if (isFav) {
      fsFavorite.innerHTML = `<i data-lucide="heart" fill="currentColor"></i>`;
    } else {
      fsFavorite.innerHTML = `<i data-lucide="heart"></i>`;
    }
  }

  // Scoped to the fullscreen panel — refreshes ONLY the icons we just
  // rewrote, not every [data-lucide] in the document. Prevents the
  // double-pass race that left the FS repeat/shuffle button stale.
  const fsPanel = document.getElementById('fullscreen-player');
  refreshIcons(fsPanel || document);
}

// === PLAY BUTTON ===

export function updatePlayButton() {
  const btn = document.getElementById('play-pause-btn');
  if (btn) {
    btn.innerHTML = `<i data-lucide="${state.isPlaying ? 'pause' : 'play'}"></i>`;
    btn.classList.toggle('is-playing', state.isPlaying);
    btn.setAttribute('aria-label', state.isPlaying ? 'Pause' : 'Play');
    btn.title = state.isPlaying ? 'Pause' : 'Play';
  }
  updateFullscreenPlayButton();
  // updateFullscreenPlayButton already refreshes the fullscreen panel.
  // Bottom button is one element — scope to it.
  refreshIcons(btn || document);
}

// === PROGRESS UPDATE ===

export function updateUIProgress(current, total) {
  if (state.isSeeking) return;

  const bar = document.getElementById('progress-fill');
  const knob = document.querySelector('#progress-bar .progress-knob');
  const currentTime = document.getElementById('time-current');
  const totalTime = document.getElementById('time-total');
  const pct = (current / total) * 100 || 0;

  if (bar) bar.style.width = `${pct}%`;
  if (knob) knob.style.left = `${pct}%`;
  if (currentTime) currentTime.innerText = fmtTime(current);
  if (totalTime) totalTime.innerText = fmtTime(total);

  // Fullscreen player sync
  const fsBar = document.getElementById('fs-progress-fill');
  const fsKnob = document.querySelector('#fs-progress-bar .fs-progress-knob');
  const fsCurrent = document.getElementById('fs-time-current');
  const fsTotal = document.getElementById('fs-time-total');

  if (fsBar) fsBar.style.width = `${pct}%`;
  if (fsKnob) fsKnob.style.left = `${pct}%`;
  if (fsCurrent) fsCurrent.innerText = fmtTime(current);
  if (fsTotal) fsTotal.innerText = fmtTime(total);
}
