/**
 * queue.js - Queue Management
 * User queue, context queue, shuffle, and repeat modes
 */

import * as state from './state.js';
import * as ui from './ui.js';
import * as player from './player.js';

// === ADD TO QUEUE ===

export function addToQueue(dataOrTrack) {
    let track;
    if (typeof dataOrTrack === 'string') {
        try {
            track = JSON.parse(decodeURIComponent(atob(dataOrTrack)));
        } catch (e) {
            return;
        }
    } else {
        track = dataOrTrack;
    }

    const queue = [...state.userQueue, track];
    state.setUserQueue(queue);
    player.persistPlaybackSession();
    ui.showToast(`Added to queue: ${track.title}`, "success");
    renderQueue();

    // If nothing playing, start playing
    if (!state.currentTrack && !state.isPlaying) {
        player.playNext();
    }
}

export function addToQueueCurrent() {
    if (state.currentTrack) addToQueue(state.currentTrack);
}


// === SHUFFLE MODE ===

export function toggleShuffle() {
    state.setShuffleMode(!state.shuffleMode);
    const btn = document.getElementById('btn-shuffle');

    if (state.shuffleMode) {
        if (btn) btn.classList.add('active-control');
        ui.showToast("Shuffle On (Global Pool)");

        if (!state.currentTrack && !state.isPlaying) {
            player.fetchRandomTrackAndPlay();
        }
    } else {
        if (btn) btn.classList.remove('active-control');
        ui.showToast("Shuffle Off");
    }

    player.persistPlaybackSession();
    ui.updateFullscreenPlayButton();
}


// === REPEAT MODE ===

export function toggleRepeat() {
    const btn = document.getElementById('btn-repeat');

    if (state.repeatMode === 'off') {
        state.setRepeatMode('all');
        if (btn) {
            btn.innerHTML = `<i data-lucide="repeat" style="color:var(--accent);"></i>`;
            btn.classList.add('active-control');
        }
    } else if (state.repeatMode === 'all') {
        state.setRepeatMode('one');
        if (btn) {
            btn.innerHTML = `<i data-lucide="repeat-1" style="color:var(--accent);"></i>`;
            btn.classList.add('active-control');
        }
    } else {
        state.setRepeatMode('off');
        if (btn) {
            btn.innerHTML = `<i data-lucide="repeat"></i>`;
            btn.classList.remove('active-control');
        }
    }
    lucide.createIcons();

    if (state.audio) {
        state.audio.loop = (state.repeatMode === 'one');
        if (state.repeatMode === 'one') ui.showToast("Repeat One");
        else if (state.repeatMode === 'all') ui.showToast("Repeat All");
        else ui.showToast("Repeat Off");
    }

    player.persistPlaybackSession();
    ui.updateFullscreenPlayButton();
}


// === QUEUE PANEL ===

export function toggleQueue() {
    const panel = document.getElementById('queue-panel');
    state.setIsQueueOpen(!state.isQueueOpen);

    if (state.isQueueOpen) {
        panel.classList.add('open');
        renderQueue();
    } else {
        panel.classList.remove('open');
    }
}

export function renderQueue() {
    const container = document.getElementById('queue-list');
    if (!container) return;

    let html = '';

    // User Queue (high priority)
    if (state.userQueue.length > 0) {
        html += `<div style="padding:10px 16px; font-size:0.75rem; color:var(--accent); font-weight:700; text-transform:uppercase;">Queue</div>`;
        html += state.userQueue.map((t, i) => `
        <div class="queue-item">
            <img src="${t.thumbnail || '/static/img/default_cover.png'}" class="queue-img" onerror="this.src='/static/img/default_cover.png'">
            <div class="queue-info">
                <div class="queue-title">${ui.escHtml(t.title || 'Unknown')}</div>
                <div class="queue-artist">${ui.escHtml(t.artist || 'Unknown')}</div>
            </div>
        </div>`).join('');
    }

    // Context Queue
    if (state.contextQueue.length > 0) {
        html += `<div style="padding:10px 16px; font-size:0.75rem; color:var(--text-sub); font-weight:700; text-transform:uppercase; margin-top:10px;">Next Up</div>`;

        const upcoming = [];
        let limit = 20;
        let idx = state.contextIndex;

        // Now Playing
        if (idx !== -1 && state.contextQueue[idx]) {
            const t = state.contextQueue[idx];
            html = `<div style="padding:10px 16px; font-size:0.75rem; color:var(--text-sub); font-weight:700; text-transform:uppercase;">Now Playing</div>
             <div class="queue-item active" style="margin-bottom:10px;">
                <img src="${t.thumbnail || '/static/img/default_cover.png'}" class="queue-img" onerror="this.src='/static/img/default_cover.png'">
                <div class="queue-info">
                    <div class="queue-title" style="color:var(--accent);">${ui.escHtml(t.title || 'Unknown')}</div>
                    <div class="queue-artist">${ui.escHtml(t.artist || 'Unknown')}</div>
                </div>
                <i data-lucide="bar-chart-2" style="color:var(--accent); width:16px;"></i>
            </div>` + html;
        }

        // Upcoming tracks
        for (let i = 1; i <= limit; i++) {
            let target = idx + i;
            if (target >= state.contextQueue.length) {
                if (state.repeatMode === 'all') target = target % state.contextQueue.length;
                else break;
            }
            upcoming.push(state.contextQueue[target]);
        }

        html += upcoming.map((t, i) => `
        <div class="queue-item">
            <img src="${t.thumbnail || '/static/img/default_cover.png'}" class="queue-img" onerror="this.src='/static/img/default_cover.png'">
            <div class="queue-info">
                <div class="queue-title">${ui.escHtml(t.title || 'Unknown')}</div>
                <div class="queue-artist">${ui.escHtml(t.artist || 'Unknown')}</div>
            </div>
        </div>`).join('');
    }

    if (!html) html = '<div style="padding:20px; text-align:center; color:#666;">Queue is empty</div>';

    container.innerHTML = html;
    lucide.createIcons();
}
