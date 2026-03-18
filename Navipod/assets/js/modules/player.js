/**
 * player.js - Audio Playback Engine
 * Local audio, YouTube preview, and media session integration
 */

import * as state from './state.js';
import * as ui from './ui.js';
import * as api from './api.js';

// Background playback lock reference
let _wakeLock = null;

// Acquire a Web Lock to prevent Android Chrome from suspending the tab
async function acquirePlaybackLock() {
    // Release any existing lock
    releasePlaybackLock();

    try {
        if ('locks' in navigator) {
            navigator.locks.request('navipod-playback', { mode: 'exclusive' }, () => {
                // This promise stays pending as long as we hold the lock
                return new Promise(resolve => {
                    _wakeLock = resolve;
                });
            });
        }
    } catch (e) {
        console.log('[BG-PLAY] Web Lock not available:', e);
    }
}

function releasePlaybackLock() {
    if (_wakeLock) {
        _wakeLock();
        _wakeLock = null;
    }
}

// === YOUTUBE API INITIALIZATION ===

export function initYoutubeAPI() {
    if (!window.YT) {
        const tag = document.createElement('script');
        tag.src = "https://www.youtube.com/iframe_api";
        const firstScriptTag = document.getElementsByTagName('script')[0];
        firstScriptTag.parentNode.insertBefore(tag, firstScriptTag);
    }
}

export function setupYouTubePlayer() {
    state.setYtPlayer(new YT.Player('youtube-player-hidden', {
        height: '200', width: '200',
        playerVars: { 'playsinline': 1, 'controls': 0, 'disablekb': 1 },
        events: {
            'onReady': () => {
                state.setYtReady(true);
                console.log("YT Preview API Ready");
            },
            'onStateChange': (e) => {
                if (e.data == YT.PlayerState.PLAYING) {
                    if (window.ytTimer) clearTimeout(window.ytTimer);
                    window.ytTimer = setTimeout(() => {
                        if (state.ytPlayer?.stopVideo) state.ytPlayer.stopVideo();
                        state.setIsPlaying(false);
                        ui.updatePlayButton();
                        if (window.ytProgressInterval) clearInterval(window.ytProgressInterval);
                    }, 30000); // 30s preview

                    if (window.ytProgressInterval) clearInterval(window.ytProgressInterval);
                    window.ytProgressInterval = setInterval(() => {
                        if (state.ytPlayer && state.ytReady && state.ytPlayer.getCurrentTime) {
                            ui.updateUIProgress(state.ytPlayer.getCurrentTime(), 30);
                        }
                    }, 500);
                } else {
                    if (window.ytProgressInterval) clearInterval(window.ytProgressInterval);
                }
            }
        }
    }));
}


// === PLAYER UI UPDATE ===

export function updatePlayerUI(track) {
    if (!track) return;
    document.getElementById('player-title').textContent = track.title || 'Unknown';
    document.getElementById('player-artist').textContent = track.artist || 'Unknown';
    document.getElementById('player-cover').src = track.thumbnail || '/static/img/default_cover.png';

    const footer = document.querySelector('.player-footer');
    if (footer) footer.classList.remove('player-hidden');

    const mainView = document.querySelector('.main-view');
    if (mainView) mainView.classList.add('has-player');

    // Restore buttons that may have been hidden by preview mode
    const likeBtn = document.getElementById('player-like-btn');
    const addBtn = document.getElementById('player-add-btn');
    if (likeBtn) {
        likeBtn.style.display = 'inline-flex';
        const isLiked = state.userFavorites.has(track.db_id || track.id);
        likeBtn.classList.toggle('liked', isLiked);
        if (isLiked) likeBtn.innerHTML = `<i data-lucide="heart" fill="var(--accent)" style="color:var(--accent)"></i>`;
        else likeBtn.innerHTML = `<i data-lucide="heart"></i>`;
    }
    if (addBtn) addBtn.style.display = 'inline-flex';

    // Restore fullscreen player buttons
    const fsFavorite = document.getElementById('fs-btn-favorite');
    if (fsFavorite) {
        fsFavorite.style.opacity = '';
        fsFavorite.style.pointerEvents = '';
    }

    // Sync Fullscreen Player
    const fsTitle = document.getElementById('fs-title');
    const fsArtist = document.getElementById('fs-artist');
    const fsCover = document.getElementById('fs-cover');
    if (fsTitle) fsTitle.textContent = track.title || 'Unknown';
    if (fsArtist) fsArtist.textContent = track.artist || 'Unknown';
    if (fsCover) fsCover.src = track.thumbnail || '/static/img/default_cover.png';

    lucide.createIcons();
}


// === UPDATE PLAYER FOR PREVIEW (Non-local tracks) ===

export function updatePlayerUIForPreview(track) {
    if (!track) return;

    document.getElementById('player-title').textContent = track.title || 'Preview';
    document.getElementById('player-artist').textContent = track.artist || 'Unknown';
    document.getElementById('player-cover').src = track.thumbnail || '/static/img/default_cover.png';

    const footer = document.querySelector('.player-footer');
    if (footer) footer.classList.remove('player-hidden');

    const mainView = document.querySelector('.main-view');
    if (mainView) mainView.classList.add('has-player');

    // Hide like button for non-local previews
    const likeBtn = document.getElementById('player-like-btn');
    if (likeBtn && !track.is_local) {
        likeBtn.style.display = 'none';
    }

    // Sync Fullscreen Player
    const fsTitle = document.getElementById('fs-title');
    const fsArtist = document.getElementById('fs-artist');
    const fsCover = document.getElementById('fs-cover');
    if (fsTitle) fsTitle.textContent = track.title || 'Preview';
    if (fsArtist) fsArtist.textContent = track.artist || 'Unknown';
    if (fsCover) fsCover.src = track.thumbnail || '/static/img/default_cover.png';

    lucide.createIcons();
}


// === MAIN PLAY TRACK FUNCTION ===

export function playTrack(track) {
    if (!track) return;

    // Stop YouTube preview if playing
    if (state.ytPlayer && state.ytPlayer.stopVideo) state.ytPlayer.stopVideo();

    state.setCurrentTrack(track);
    updatePlayerUI(track);

    // Highlight in lists
    document.querySelectorAll('.track-row').forEach(row => row.classList.remove('active-track'));
    if (state.currentViewList) {
        const idx = state.currentViewList.findIndex(t => t.id === track.id);
        if (idx !== -1) {
            const row = document.querySelector(`.track-row[data-idx="${idx}"]`);
            if (row) row.classList.add('active-track');
        }
    }

    if (window.renderQueue) window.renderQueue();

    if (track.db_id) {
        state.audio.src = `/api/stream/${track.db_id}`;
        state.audio.load();
        state.audio.play().then(() => {
            state.setIsPlaying(true);
            ui.updatePlayButton();
            document.title = `${track.title} - Navipod`;

            // Acquire Web Lock to prevent tab suspension on Android
            acquirePlaybackLock();

            // Media Session API
            if ('mediaSession' in navigator) {
                navigator.mediaSession.metadata = new MediaMetadata({
                    title: track.title,
                    artist: track.artist,
                    album: track.album || '',
                    artwork: [{ src: track.thumbnail || '/static/img/default_cover.png', sizes: '512x512', type: 'image/png' }]
                });
                navigator.mediaSession.playbackState = 'playing';
                navigator.mediaSession.setActionHandler('play', () => {
                    state.audio.play();
                    navigator.mediaSession.playbackState = 'playing';
                });
                navigator.mediaSession.setActionHandler('pause', () => {
                    state.audio.pause();
                    navigator.mediaSession.playbackState = 'paused';
                });
                navigator.mediaSession.setActionHandler('previoustrack', playPrev);
                navigator.mediaSession.setActionHandler('nexttrack', playNext);
                navigator.mediaSession.setActionHandler('seekbackward', () => {
                    state.audio.currentTime = Math.max(0, state.audio.currentTime - 10);
                });
                navigator.mediaSession.setActionHandler('seekforward', () => {
                    state.audio.currentTime = Math.min(state.audio.duration, state.audio.currentTime + 10);
                });
                try {
                    navigator.mediaSession.setActionHandler('seekto', (details) => {
                        if (details.seekTime != null) {
                            state.audio.currentTime = details.seekTime;
                        }
                    });
                } catch (e) { /* seekto not supported on all browsers */ }
            }

        }).catch(e => {
            if (e.name === 'AbortError') return;
            console.error("Playback error:", e);
            ui.showToast("Playback failed", "error");
        });
    } else if (track.is_radio) {
        // Radio playback handled separately
    } else {
        ui.showToast("Track not in library", "error");
    }
}


// === PREVIEW PLAYBACK ===

export async function playPreview(data) {
    let track = data;
    if (typeof data === 'string') {
        try {
            track = JSON.parse(decodeURIComponent(atob(data)));
        } catch (e) {
            return;
        }
    }

    state.setCurrentTrack(track);
    updatePlayerUIForPreview(track);

    // Stop current audio
    state.audio.pause();
    state.audio.src = '';

    // Use proxy endpoint
    const previewUrl = `${state.API}/playback/preview?url=${encodeURIComponent(track.id)}`;

    try {
        state.audio.src = previewUrl;
        state.audio.load();
        await state.audio.play();
        state.setIsPlaying(true);
        ui.updatePlayButton();
    } catch (e) {
        console.error("Preview playback failed:", e);
        ui.showToast("Preview not available", "error");
    }
}


// === PLAY FROM VIEW LIST ===

export function playFromView(index) {
    if (!state.currentViewList || !state.currentViewList[index]) return;

    state.setContextQueue([...state.currentViewList]);
    state.setOriginalContextQueue([...state.currentViewList]);
    state.setContextIndex(index);

    console.log(`[PLAYER] Context set with ${state.contextQueue.length} tracks. Playing #${index}`);
    playTrack(state.contextQueue[state.contextIndex]);
    if (window.renderQueue) window.renderQueue();
}


// === NAVIGATION ===

export async function playNext() {
    // 1. Priority: User Queue
    if (state.userQueue.length > 0) {
        const nextTrack = state.userQueue.shift();
        state.setUserQueue([...state.userQueue]);
        playTrack(nextTrack);
        if (window.renderQueue) window.renderQueue();
        return;
    }

    // 2. Global Shuffle Auto-Fetch
    if (state.shuffleMode && state.contextQueue.length === 0) {
        await fetchRandomTrackAndPlay();
        return;
    }

    // 3. Regular Context Queue
    if (state.contextQueue.length === 0) return;

    let nextIdx = state.contextIndex + 1;
    if (nextIdx >= state.contextQueue.length) {
        if (state.repeatMode === 'all') {
            nextIdx = 0;
        } else if (state.shuffleMode) {
            await fetchRandomTrackAndPlay();
            return;
        } else {
            return;
        }
    }

    state.setContextIndex(nextIdx);
    playTrack(state.contextQueue[state.contextIndex]);
}

export function playPrev() {
    const now = Date.now();
    const timeSinceLastClick = now - state.lastPrevClickTime;
    state.setLastPrevClickTime(now);

    if (state.audio.currentTime <= 3 || timeSinceLastClick < 1500) {
        if (state.contextQueue.length === 0) return;

        let prevIdx = state.contextIndex - 1;
        if (prevIdx < 0) {
            if (state.repeatMode === 'all') prevIdx = state.contextQueue.length - 1;
            else prevIdx = 0;
        }

        state.setContextIndex(prevIdx);
        playTrack(state.contextQueue[state.contextIndex]);
    } else {
        state.audio.currentTime = 0;
    }
}

export async function fetchRandomTrackAndPlay() {
    try {
        const track = await api.fetchRandomTrack();
        if (!track) throw new Error("Empty library");
        playTrack(track);
        ui.showToast("Playing random track 🎲");
    } catch (e) {
        ui.showToast("No tracks to shuffle", "error");
    }
}


// === SETUP PLAYER EVENT LISTENERS ===

export function setupPlayer() {
    const playBtn = document.getElementById('play-pause-btn');
    const progressBar = document.getElementById('progress-bar');
    const volumeBar = document.getElementById('volume-bar');

    if (playBtn) {
        playBtn.addEventListener('click', () => {
            if (!state.currentTrack) return;
            if (state.audio.paused && state.ytPlayer && state.ytPlayer.stopVideo) state.ytPlayer.stopVideo();
            state.audio.paused ? state.audio.play() : state.audio.pause();
        });
    }

    state.audio.addEventListener('play', () => {
        state.setIsPlaying(true);
        ui.updatePlayButton();
        state.audio._endHandled = false;
        acquirePlaybackLock();
        if ('mediaSession' in navigator) {
            navigator.mediaSession.playbackState = 'playing';
        }
    });

    state.audio.addEventListener('pause', () => {
        state.setIsPlaying(false);
        ui.updatePlayButton();
        if ('mediaSession' in navigator) {
            navigator.mediaSession.playbackState = 'paused';
        }
    });

    state.audio.addEventListener('ended', () => {
        state.setIsPlaying(false);
        ui.updatePlayButton();
        state.audio._endHandled = true;
        releasePlaybackLock();
        if ('mediaSession' in navigator) {
            navigator.mediaSession.playbackState = 'none';
        }
        playNext();
    });

    state.audio.addEventListener('timeupdate', () => {
        ui.updateUIProgress(state.audio.currentTime, state.audio.duration);

        if (state.audio.duration && state.audio.currentTime >= state.audio.duration - 0.5) {
            if (!state.audio._endHandled) {
                state.audio._endHandled = true;
                console.log('[BG-PLAY] Fallback triggered, advancing to next');
                playNext();
            }
        }
    });

    state.audio.addEventListener('error', () => ui.showToast("Audio error", "error"));

    if (progressBar) {
        ui.setupDraggable(progressBar, (pct, isDragging) => {
            if (isDragging) {
                state.setIsSeeking(true);
            } else {
                state.setIsSeeking(false);
                if (state.audio.duration) {
                    state.audio.currentTime = pct * state.audio.duration;
                }
            }
        });
    }

    if (volumeBar) {
        ui.setupDraggable(volumeBar, (pct) => {
            state.audio.volume = Math.max(0, Math.min(1, pct));
        });
    }

    // Fullscreen player progress bar
    const fsProgressBar = document.getElementById('fs-progress-bar');
    if (fsProgressBar) {
        ui.setupDraggable(fsProgressBar, (pct, isDragging) => {
            if (isDragging) {
                state.setIsSeeking(true);
            } else {
                state.setIsSeeking(false);
                if (state.audio.duration) {
                    state.audio.currentTime = pct * state.audio.duration;
                }
            }
        });
    }

    state.audio.volume = 0.7;

    // Initialize volume bar visual position
    const volumeFill = document.querySelector('.volume-bar-fill');
    const volumeKnob = document.querySelector('.volume-knob');
    if (volumeFill) volumeFill.style.width = '70%';
    if (volumeKnob) {
        volumeKnob.style.left = '70%';
        volumeKnob.style.transform = 'translate(-50%, -50%)';
    }

    // --- BACKGROUND PLAYBACK: Visibility change handler ---
    // When user returns to the tab, check if the track ended while backgrounded
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible' && state.currentTrack) {
            // If audio ended while in background, the 'ended' event may not have fired
            if (state.audio.ended && !state.audio._endHandled) {
                console.log('[BG-PLAY] Track ended while backgrounded, advancing...');
                state.audio._endHandled = true;
                playNext();
            }
            // Update MediaSession state
            if ('mediaSession' in navigator) {
                navigator.mediaSession.playbackState = state.audio.paused ? 'paused' : 'playing';
            }
        }
    });
}
