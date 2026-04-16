/**
 * player.js - Audio Playback Engine
 * Local audio, YouTube preview, and media session integration
 */

import * as state from './state.js';
import * as ui from './ui.js';
import * as api from './api.js';

// Background playback lock reference
let _wakeLock = null;
let _activeListenSession = null;
let _sessionPersistInterval = null;
let _remoteQueueSaveTimer = null;

const PLAYBACK_SESSION_KEY = 'navipod.playback.session.v1';
const PLAYBACK_SESSION_MAX_AGE_MS = 24 * 60 * 60 * 1000;
const PLAYBACK_AUTO_RESUME_MAX_AGE_MS = 30 * 60 * 1000;
const PLAYBACK_PROGRESS_SAVE_INTERVAL_MS = 10000;
const PLAYBACK_REMOTE_SAVE_DEBOUNCE_MS = 1500;

// Acquire a Web Lock to prevent Android Chrome from suspending the tab
async function acquirePlaybackLock() {
  // Release any existing lock
  releasePlaybackLock();

  try {
    if ('locks' in navigator) {
      navigator.locks.request('navipod-playback', { mode: 'exclusive' }, () => {
        // This promise stays pending as long as we hold the lock
        return new Promise((resolve) => {
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

function buildPlaybackSessionSnapshot() {
  const hasCurrentTrack = Boolean(state.currentTrack?.db_id);
  const hasManualQueue = state.userQueue.length > 0;
  const hasContextQueue = state.contextQueue.length > 0;
  if (!hasCurrentTrack && !hasManualQueue && !hasContextQueue && !state.shuffleMode) return null;

  return {
    savedAt: Date.now(),
    currentTrack: state.currentTrack,
    userQueue: state.userQueue,
    contextQueue: state.contextQueue,
    originalContextQueue: state.originalContextQueue,
    contextIndex: state.contextIndex,
    repeatMode: state.repeatMode,
    shuffleMode: state.shuffleMode,
    currentViewName: state.currentViewName,
    currentViewParam: state.currentViewParam,
    currentTime: Number(state.audio.currentTime || 0),
    duration: Number(state.audio.duration || state.currentTrack?.duration || 0),
    wasPlaying: Boolean(state.isPlaying && !state.audio.paused)
  };
}

function queueStatePayloadFromSnapshot(snapshot) {
  if (!snapshot) return null;
  return {
    manual_queue: snapshot.userQueue || [],
    context_queue: snapshot.contextQueue || [],
    original_context_queue: snapshot.originalContextQueue || [],
    current_track: snapshot.currentTrack || null,
    current_view_name: snapshot.currentViewName || 'home',
    current_view_param: snapshot.currentViewParam ?? null,
    context_index: snapshot.contextIndex ?? -1,
    shuffle_mode: Boolean(snapshot.shuffleMode),
    repeat_mode: snapshot.repeatMode || 'off',
    current_time: Number(snapshot.currentTime || 0),
    duration: Number(snapshot.duration || 0),
    was_playing: Boolean(snapshot.wasPlaying),
    persist_enabled: true
  };
}

function scheduleRemoteQueueStateSave(snapshot) {
  if (_remoteQueueSaveTimer) clearTimeout(_remoteQueueSaveTimer);
  _remoteQueueSaveTimer = window.setTimeout(() => {
    _remoteQueueSaveTimer = null;
    if (snapshot) {
      api.savePlaybackQueueState(queueStatePayloadFromSnapshot(snapshot));
    } else {
      api.clearPlaybackQueueState();
    }
  }, PLAYBACK_REMOTE_SAVE_DEBOUNCE_MS);
}

function remotePayloadToSnapshot(payload) {
  if (!payload || payload.error) return null;
  const hasRemoteData =
    payload.updated_at ||
    payload.current_track ||
    (payload.manual_queue || []).length ||
    (payload.context_queue || []).length;
  if (!hasRemoteData) return null;
  const parsedUpdatedAt = payload.updated_at ? Date.parse(payload.updated_at) : NaN;

  return {
    savedAt: Number.isFinite(parsedUpdatedAt) ? parsedUpdatedAt : Date.now(),
    currentTrack: payload.current_track || null,
    userQueue: Array.isArray(payload.manual_queue) ? payload.manual_queue : [],
    contextQueue: Array.isArray(payload.context_queue) ? payload.context_queue : [],
    originalContextQueue: Array.isArray(payload.original_context_queue) ? payload.original_context_queue : [],
    contextIndex: Number.isInteger(payload.context_index) ? payload.context_index : -1,
    repeatMode: payload.repeat_mode || 'off',
    shuffleMode: Boolean(payload.shuffle_mode),
    currentViewName: payload.current_view_name || 'home',
    currentViewParam: payload.current_view_param ?? null,
    currentTime: Number(payload.current_time || 0),
    duration: Number(payload.duration || 0),
    wasPlaying: Boolean(payload.was_playing)
  };
}

function applyQueueSnapshot(snapshot) {
  state.setUserQueue(Array.isArray(snapshot.userQueue) ? snapshot.userQueue : []);
  state.setContextQueue(Array.isArray(snapshot.contextQueue) ? snapshot.contextQueue : []);
  state.setOriginalContextQueue(Array.isArray(snapshot.originalContextQueue) ? snapshot.originalContextQueue : []);
  state.setContextIndex(Number.isInteger(snapshot.contextIndex) ? snapshot.contextIndex : -1);
  state.setRepeatMode(snapshot.repeatMode || 'off');
  state.setShuffleMode(Boolean(snapshot.shuffleMode));
  state.setCurrentViewName(snapshot.currentViewName || 'home');
  state.setCurrentViewParam(snapshot.currentViewParam ?? null);
}

export function persistPlaybackSession({ syncRemote = true } = {}) {
  try {
    const snapshot = buildPlaybackSessionSnapshot();
    if (!snapshot) {
      localStorage.removeItem(PLAYBACK_SESSION_KEY);
      if (syncRemote) scheduleRemoteQueueStateSave(null);
      return;
    }
    localStorage.setItem(PLAYBACK_SESSION_KEY, JSON.stringify(snapshot));
    if (syncRemote) scheduleRemoteQueueStateSave(snapshot);
  } catch (e) {
    console.warn('[BG-PLAY] Failed to persist playback session:', e);
  }
}

function clearPersistedPlaybackSession() {
  try {
    localStorage.removeItem(PLAYBACK_SESSION_KEY);
    scheduleRemoteQueueStateSave(null);
  } catch (e) {
    console.warn('[BG-PLAY] Failed to clear playback session:', e);
  }
}

function startPlaybackSessionPersistence() {
  stopPlaybackSessionPersistence();
  _sessionPersistInterval = window.setInterval(() => {
    persistPlaybackSession();
  }, PLAYBACK_PROGRESS_SAVE_INTERVAL_MS);
}

function stopPlaybackSessionPersistence() {
  if (_sessionPersistInterval) {
    clearInterval(_sessionPersistInterval);
    _sessionPersistInterval = null;
  }
}

async function requestPersistentStorage() {
  try {
    if (navigator.storage?.persist) {
      await navigator.storage.persist();
    }
  } catch (e) {
    console.warn('[BG-PLAY] Persistent storage request failed:', e);
  }
}

function applyPlaybackModes() {
  if (state.audio) {
    state.audio.loop = state.repeatMode === 'one';
  }
}

function syncTransportControlButtons() {
  const shuffleBtn = document.getElementById('btn-shuffle');
  const repeatBtn = document.getElementById('btn-repeat');

  if (shuffleBtn) {
    shuffleBtn.classList.toggle('active-control', state.shuffleMode);
  }

  if (repeatBtn) {
    repeatBtn.classList.toggle('active-control', state.repeatMode !== 'off');
    repeatBtn.innerHTML =
      state.repeatMode === 'one' ? `<i data-lucide="repeat-1"></i>` : `<i data-lucide="repeat"></i>`;
  }

  lucide.createIcons();
}

function clampResumeTime(timeSeconds, durationSeconds) {
  const current = Number(timeSeconds || 0);
  const duration = Number(durationSeconds || 0);
  if (!Number.isFinite(current) || current < 0) return 0;
  if (!Number.isFinite(duration) || duration <= 0) return current;
  return Math.min(current, Math.max(0, duration - 1));
}

export async function restorePlaybackSession() {
  try {
    let serverSnapshot = null;
    try {
      serverSnapshot = remotePayloadToSnapshot(await api.fetchPlaybackQueueState());
    } catch (e) {
      console.warn('[BG-PLAY] Failed to fetch remote playback queue state:', e);
    }

    const raw = localStorage.getItem(PLAYBACK_SESSION_KEY);
    let localSnapshot = null;
    if (raw) {
      try {
        localSnapshot = JSON.parse(raw);
      } catch (e) {
        localStorage.removeItem(PLAYBACK_SESSION_KEY);
      }
    }
    const snapshot = serverSnapshot || localSnapshot;
    if (!snapshot) return null;

    applyQueueSnapshot(snapshot);

    if (!snapshot?.currentTrack?.db_id) {
      syncTransportControlButtons();
      if (window.renderQueue) window.renderQueue();
      return null;
    }

    const ageMs = Date.now() - Number(snapshot.savedAt || 0);
    if (!Number.isFinite(ageMs) || ageMs < 0 || ageMs > PLAYBACK_SESSION_MAX_AGE_MS) {
      clearPersistedPlaybackSession();
      return null;
    }

    state.setCurrentTrack(snapshot.currentTrack);
    state.setIsPlaying(false);

    updatePlayerUI(snapshot.currentTrack);
    applyPlaybackModes();
    syncTransportControlButtons();
    document.title = `${snapshot.currentTrack.title || 'Navipod'} - Navipod`;

    state.audio.src = `/api/stream/${snapshot.currentTrack.db_id}`;
    state.audio.load();

    const resumeTime = clampResumeTime(snapshot.currentTime, snapshot.duration);
    const shouldAttemptResume = Boolean(snapshot.wasPlaying) && ageMs <= PLAYBACK_AUTO_RESUME_MAX_AGE_MS;

    const restorePosition = () => {
      if (resumeTime > 0) {
        try {
          state.audio.currentTime = resumeTime;
        } catch (e) {
          console.warn('[BG-PLAY] Failed to restore playback position:', e);
        }
      }

      if (shouldAttemptResume) {
        state.audio
          .play()
          .then(() => {
            state.setIsPlaying(true);
            ui.updatePlayButton();
            startPlaybackSessionPersistence();
            acquirePlaybackLock();
          })
          .catch(() => {
            state.setIsPlaying(false);
            ui.updatePlayButton();
            persistPlaybackSession();
          });
      } else {
        ui.updatePlayButton();
        persistPlaybackSession();
      }
    };

    state.audio.addEventListener('loadedmetadata', restorePosition, { once: true });
    if (window.renderQueue) window.renderQueue();

    return {
      view: snapshot.currentViewName || 'home',
      param: snapshot.currentViewParam ?? null
    };
  } catch (e) {
    console.warn('[BG-PLAY] Failed to restore playback session:', e);
    clearPersistedPlaybackSession();
    return null;
  }
}

// === YOUTUBE API INITIALIZATION ===

export function initYoutubeAPI() {
  if (!window.YT) {
    const tag = document.createElement('script');
    tag.src = 'https://www.youtube.com/iframe_api';
    const firstScriptTag = document.getElementsByTagName('script')[0];
    firstScriptTag.parentNode.insertBefore(tag, firstScriptTag);
  }
}

export function setupYouTubePlayer() {
  state.setYtPlayer(
    new YT.Player('youtube-player-hidden', {
      height: '200',
      width: '200',
      playerVars: { playsinline: 1, controls: 0, disablekb: 1 },
      events: {
        onReady: () => {
          state.setYtReady(true);
          console.log('YT Preview API Ready');
        },
        onStateChange: (e) => {
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
    })
  );
}

// === PLAYER UI UPDATE ===

export function updatePlayerUI(track) {
  if (!track) return;
  document.getElementById('player-title').textContent = track.title || 'Unknown';
  document.getElementById('player-artist').textContent = track.artist || 'Unknown';
  document.getElementById('player-cover').src = track.thumbnail || '/static/img/default_cover.png';

  syncPlayerShellVisibility(track);

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

  syncPlayerShellVisibility(track);

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

  finalizeListenSession('track_switch');

  // Stop YouTube preview if playing
  if (state.ytPlayer && state.ytPlayer.stopVideo) state.ytPlayer.stopVideo();

  state.setCurrentTrack(track);
  updatePlayerUI(track);
  persistPlaybackSession();

  // Highlight in lists
  document.querySelectorAll('.track-row').forEach((row) => row.classList.remove('active-track'));
  if (state.currentViewList) {
    const idx = state.currentViewList.findIndex((t) => t.id === track.id);
    if (idx !== -1) {
      const row = document.querySelector(`.track-row[data-idx="${idx}"]`);
      if (row) row.classList.add('active-track');
    }
  }

  if (window.renderQueue) window.renderQueue();

  if (track.db_id) {
    beginListenSession(track);
    state.audio.src = `/api/stream/${track.db_id}`;
    state.audio.load();
    state.audio
      .play()
      .then(() => {
        state.setIsPlaying(true);
        ui.updatePlayButton();
        document.title = `${track.title} - Navipod`;

        // Acquire Web Lock to prevent tab suspension on Android
        acquirePlaybackLock();
        startPlaybackSessionPersistence();
        persistPlaybackSession();

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
          } catch (e) {
            /* seekto not supported on all browsers */
          }
        }
      })
      .catch((e) => {
        if (e.name === 'AbortError') return;
        console.error('Playback error:', e);
        ui.showToast('Playback failed', 'error');
        persistPlaybackSession();
      });
  } else if (track.is_radio) {
    // Radio playback handled separately
  } else {
    ui.showToast('Track not in library', 'error');
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

  finalizeListenSession('preview_switch');
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
    console.error('Preview playback failed:', e);
    ui.showToast('Preview not available', 'error');
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
  if (state.contextQueue.length === 0) {
    clearFinishedPlaybackState();
    return;
  }

  let nextIdx = state.contextIndex + 1;
  if (nextIdx >= state.contextQueue.length) {
    if (state.repeatMode === 'all') {
      nextIdx = 0;
    } else if (state.shuffleMode) {
      await fetchRandomTrackAndPlay();
      return;
    } else {
      clearFinishedPlaybackState();
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
    if (!track) throw new Error('Empty library');
    playTrack(track);
    ui.showToast('Playing random track 🎲');
  } catch (e) {
    ui.showToast('No tracks to shuffle', 'error');
  }
}

export function syncPlayerShellVisibility(track = state.currentTrack) {
  const hasTrack = Boolean(track);
  const footer = document.querySelector('.player-footer');
  const mainView = document.querySelector('.main-view');

  if (footer) {
    footer.classList.toggle('player-hidden', !hasTrack);
  }

  if (mainView) {
    mainView.classList.toggle('has-player', hasTrack);
  }
}

function hasUpcomingTrack() {
  if (state.userQueue.length > 0) return true;
  if (state.shuffleMode && state.contextQueue.length === 0) return true;
  if (state.contextQueue.length === 0) return false;

  const nextIdx = state.contextIndex + 1;
  if (nextIdx < state.contextQueue.length) return true;
  if (state.repeatMode === 'all') return state.contextQueue.length > 0;
  if (state.shuffleMode) return true;
  return false;
}

function clearFinishedPlaybackState() {
  finalizeListenSession('finished');
  state.setCurrentTrack(null);
  state.setIsPlaying(false);
  releasePlaybackLock();
  stopPlaybackSessionPersistence();
  clearPersistedPlaybackSession();
  if ('mediaSession' in navigator) {
    navigator.mediaSession.playbackState = 'none';
  }
  syncPlayerShellVisibility(null);
  ui.updatePlayButton();
  ui.updateUIProgress(0, 0);
}

function currentContextPayload() {
  return {
    contextType: state.currentViewName || 'home',
    contextKey: state.currentTrack?.mix_key ? String(state.currentTrack.mix_key) : ''
  };
}

function beginListenSession(track) {
  if (!track?.db_id) {
    _activeListenSession = null;
    return;
  }

  const { contextType, contextKey } = currentContextPayload();
  _activeListenSession = {
    trackId: Number(track.db_id),
    durationSeconds: Number(track.duration || 0),
    maxPositionSeconds: 0,
    contextType,
    contextKey
  };
}

function updateListenProgress() {
  if (!_activeListenSession) return;
  const currentTime = Number(state.audio.currentTime || 0);
  if (Number.isFinite(currentTime) && currentTime > _activeListenSession.maxPositionSeconds) {
    _activeListenSession.maxPositionSeconds = currentTime;
  }
}

function finalizeListenSession(reason = 'stopped') {
  if (!_activeListenSession) return;

  updateListenProgress();

  const session = _activeListenSession;
  _activeListenSession = null;

  const playedSeconds = Math.max(0, Number(session.maxPositionSeconds || 0));
  const durationSeconds =
    Number.isFinite(state.audio.duration) && state.audio.duration > 0
      ? Number(state.audio.duration)
      : Number(session.durationSeconds || 0);
  const completed =
    durationSeconds > 0 ? playedSeconds >= Math.max(durationSeconds * 0.85, durationSeconds - 8) : reason === 'ended';
  const skippedEarly = !completed && playedSeconds > 0 && playedSeconds < Math.min(30, durationSeconds || 30);

  if (playedSeconds < 8 && !completed && !skippedEarly) {
    return;
  }

  api.recordListenEvent({
    track_id: session.trackId,
    played_seconds: playedSeconds,
    duration_seconds: durationSeconds || null,
    completed,
    skipped_early: skippedEarly,
    context_type: session.contextType || '',
    context_key: session.contextKey || ''
  });
}

// === SETUP PLAYER EVENT LISTENERS ===

export function setupPlayer() {
  const playBtn = document.getElementById('play-pause-btn');
  const progressBar = document.getElementById('progress-bar');
  const volumeBar = document.getElementById('volume-bar');

  syncPlayerShellVisibility();
  requestPersistentStorage();
  applyPlaybackModes();

  if (playBtn) {
    playBtn.addEventListener('click', () => {
      if (!state.currentTrack) return;
      if (state.audio.paused && state.ytPlayer && state.ytPlayer.stopVideo) state.ytPlayer.stopVideo();
      state.audio.paused ? state.audio.play() : state.audio.pause();
    });
  }

  state.audio.addEventListener('play', () => {
    state.setIsPlaying(true);
    syncPlayerShellVisibility();
    ui.updatePlayButton();
    state.audio._endHandled = false;
    acquirePlaybackLock();
    startPlaybackSessionPersistence();
    persistPlaybackSession();
    if ('mediaSession' in navigator) {
      navigator.mediaSession.playbackState = 'playing';
    }
  });

  state.audio.addEventListener('pause', () => {
    state.setIsPlaying(false);
    syncPlayerShellVisibility();
    ui.updatePlayButton();
    updateListenProgress();
    stopPlaybackSessionPersistence();
    persistPlaybackSession();
    if ('mediaSession' in navigator) {
      navigator.mediaSession.playbackState = 'paused';
    }
  });

  state.audio.addEventListener('ended', () => {
    state.setIsPlaying(false);
    ui.updatePlayButton();
    state.audio._endHandled = true;
    stopPlaybackSessionPersistence();
    finalizeListenSession('ended');
    if ('mediaSession' in navigator) {
      navigator.mediaSession.playbackState = hasUpcomingTrack() ? 'playing' : 'none';
    }
    playNext();
    syncPlayerShellVisibility();
  });

  state.audio.addEventListener('timeupdate', () => {
    updateListenProgress();
    ui.updateUIProgress(state.audio.currentTime, state.audio.duration);

    if (state.audio.duration && state.audio.currentTime >= state.audio.duration - 0.5) {
      if (!state.audio._endHandled) {
        state.audio._endHandled = true;
        console.log('[BG-PLAY] Fallback triggered, advancing to next');
        if ('mediaSession' in navigator) {
          navigator.mediaSession.playbackState = hasUpcomingTrack() ? 'playing' : 'none';
        }
        playNext();
      }
    }
  });

  state.audio.addEventListener('error', () => {
    finalizeListenSession('error');
    stopPlaybackSessionPersistence();
    persistPlaybackSession();
    ui.showToast('Audio error', 'error');
  });

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
    if (document.visibilityState === 'hidden') {
      persistPlaybackSession();
      return;
    }
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

  window.addEventListener('pagehide', () => {
    persistPlaybackSession();
    finalizeListenSession('pagehide');
  });

  document.addEventListener('freeze', () => {
    persistPlaybackSession();
  });

  document.addEventListener('resume', () => {
    if (state.currentTrack) {
      persistPlaybackSession();
    }
  });

  window.addEventListener('pageshow', () => {
    if (state.currentTrack) {
      ui.updatePlayButton();
      persistPlaybackSession();
    }
  });

  window.addEventListener('beforeunload', () => {
    persistPlaybackSession();
  });
}
