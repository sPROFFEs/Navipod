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

    const contextCount = state.contextQueue.length;
    const contextPosition = state.contextIndex >= 0 ? state.contextIndex + 1 : 0;
    const modeLabel = state.shuffleMode
        ? (contextCount > 0 ? 'Shuffled context' : 'Global random shuffle')
        : (contextCount > 0 ? 'Context playback' : 'No playback context');

    const manualQueueHtml = state.userQueue.length > 0
        ? state.userQueue.map((t, i) => `
            <div class="queue-item">
                <img src="${t.thumbnail || '/static/img/default_cover.png'}" class="queue-img" loading="lazy" decoding="async" onerror="this.src='/static/img/default_cover.png'">
                <div class="queue-info">
                    <div class="queue-title">${ui.escHtml(t.title || 'Unknown')}</div>
                    <div class="queue-artist">${ui.escHtml(t.artist || 'Unknown')}${i === 0 ? ' - Next' : ''}</div>
                </div>
            </div>`).join('')
        : '<div class="queue-empty">No manual tracks queued.</div>';

    container.innerHTML = `
        <div class="queue-section-title">Manual Queue</div>
        ${manualQueueHtml}
        <div class="queue-context-card">
            <div class="queue-section-title">Playback Context</div>
            <div class="queue-context-row">
                <span>Mode</span>
                <strong>${ui.escHtml(modeLabel)}</strong>
            </div>
            <div class="queue-context-row">
                <span>Context tracks</span>
                <strong>${contextCount ? `${contextPosition || 1}/${contextCount}` : 'None'}</strong>
            </div>
        </div>
        <div class="queue-footnote">Manual queue plays before context and is restored after refresh.</div>`;
    lucide.createIcons();
}
