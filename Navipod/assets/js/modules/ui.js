/**
 * ui.js - UI Utilities
 * Toast notifications, modals, formatters, draggable controls
 */

import * as state from './state.js';

// === FORMATTERS ===

export function fmtTime(s) {
    if (!s || isNaN(s) || !isFinite(s)) return "0:00";
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


// === TOAST NOTIFICATIONS ===

export function showToast(msg, type = 'info') {
    document.querySelectorAll('.toast-msg').forEach(t => t.remove());
    const toast = document.createElement('div');
    toast.className = 'toast-msg';
    toast.style.cssText = `position:fixed;bottom:110px;left:50%;transform:translateX(-50%);background:${type === 'error' ? '#e74c3c' : type === 'success' ? '#1DB954' : '#333'};color:white;padding:12px 24px;border-radius:30px;font-weight:600;font-size:0.9rem;z-index:9999;box-shadow:0 5px 20px rgba(0,0,0,0.4);border: 1px solid rgba(255,255,255,0.1);max-width:350px;width:max-content;text-align:center;word-break:break-word;`;
    toast.innerText = msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
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
    element.addEventListener('touchstart', (e) => {
        isDragging = true;
        update(e);
    }, { passive: true });

    element.addEventListener('touchmove', (e) => {
        if (isDragging) {
            e.preventDefault();
            update(e);
        }
    }, { passive: false });

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
        lucide.createIcons();
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

    lucide.createIcons();
}


// === PLAY BUTTON ===

export function updatePlayButton() {
    const btn = document.getElementById('play-pause-btn');
    if (btn) {
        btn.innerHTML = `<i data-lucide="${state.isPlaying ? 'pause' : 'play'}"></i>`;
        btn.classList.toggle('is-playing', state.isPlaying);
    }
    updateFullscreenPlayButton();
    lucide.createIcons();
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
