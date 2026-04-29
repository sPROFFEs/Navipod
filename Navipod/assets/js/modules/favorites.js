/**
 * favorites.js - Favorites CRUD
 * Like/unlike tracks with optimistic updates
 */

import * as state from './state.js';
import * as ui from './ui.js';
import * as api from './api.js';

// === TOGGLE FAVORITE FROM CURRENT TRACK ===

export function toggleFavoriteCurrent() {
  if (!state.currentTrack) return;
  const id = state.currentTrack.db_id || state.currentTrack.id;
  if (!id) return;
  toggleFavorite(id, document.getElementById('player-like-btn'));
}

// === TOGGLE FROM FULLSCREEN PLAYER ===

export function toggleFavoriteFromPlayer() {
  if (!state.currentTrack) return;
  const id = state.currentTrack.db_id || state.currentTrack.id;
  if (!id) return;

  const fsBtn = document.getElementById('fs-btn-favorite');
  toggleFavorite(id, fsBtn);
}

// === MAIN TOGGLE FUNCTION ===

export async function toggleFavorite(trackId, btn) {
  if (!trackId) return;

  const isLiked = state.userFavorites.has(trackId);

  // Optimistic toggle
  if (isLiked) state.userFavorites.delete(trackId);
  else state.userFavorites.add(trackId);

  const updateElement = (el, active) => {
    if (!el) return;
    el.classList.toggle('liked', active);
    el.innerHTML = active
      ? `<i data-lucide="heart" fill="var(--accent)" style="color:var(--accent)"></i>`
      : `<i data-lucide="heart"></i>`;
  };

  // Update player button
  const playerBtn = document.getElementById('player-like-btn');
  if (state.currentTrack && (state.currentTrack.id == trackId || state.currentTrack.db_id == trackId)) {
    updateElement(playerBtn, !isLiked);
  }

  // Update track list buttons
  let activeRow = null;
  if (state.currentViewList) {
    state.currentViewList.forEach((t, idx) => {
      if (t.id == trackId || t.db_id == trackId) {
        const row = document.querySelector(`.track-row[data-idx="${idx}"]`);
        if (row) {
          const heartBtn = row.querySelector('button[onclick*="toggleFavorite"]');
          if (heartBtn) updateElement(heartBtn, !isLiked);
          activeRow = row;
        }
      }
    });
  }

  if (btn) updateElement(btn, !isLiked);
  if (window.lucide) lucide.createIcons();

  try {
    let res;
    if (!isLiked) {
      res = await fetch(`${state.API}/favorites/${trackId}`, { method: 'POST' });
    } else {
      res = await fetch(`${state.API}/favorites/${trackId}`, { method: 'DELETE' });
      // 404 on DELETE means the track was already not favorited — that IS the
      // desired state after an unlike, so treat it as success instead of
      // retrying with POST (which would incorrectly re-add the favorite).
      if (res.status === 404) {
        state.userFavorites.delete(trackId);
        if (btn) updateElement(btn, false);
        if (window.lucide) lucide.createIcons();
        return;
      }
    }

    if (!res.ok) throw new Error('Failed');
    const data = await res.json();

    const finalState = data.liked;
    if (finalState) state.userFavorites.add(trackId);
    else state.userFavorites.delete(trackId);

    // Sync UI again
    if (state.currentTrack && (state.currentTrack.id == trackId || state.currentTrack.db_id == trackId)) {
      updateElement(playerBtn, finalState);
    }
    if (state.currentViewList) {
      state.currentViewList.forEach((t, idx) => {
        if (t.id == trackId || t.db_id == trackId) {
          const row = document.querySelector(`.track-row[data-idx="${idx}"]`);
          if (row) {
            const heartBtn = row.querySelector('button[onclick*="toggleFavorite"]');
            if (heartBtn) updateElement(heartBtn, finalState);
          }
        }
      });
    }
    if (btn) updateElement(btn, finalState);
    if (window.lucide) lucide.createIcons();
  } catch (e) {
    console.error('Toggle error:', e);
    // Revert
    if (isLiked) state.userFavorites.add(trackId);
    else state.userFavorites.delete(trackId);
    updateElement(btn, isLiked);
    ui.showToast('Failed to update ' + e.message, 'error');
  }

  // Visual removal in favorites view
  if (state.currentViewName === 'favorites' && state.userFavorites.has(trackId) === false) {
    if (activeRow) {
      activeRow.style.transition = 'opacity 0.3s, transform 0.3s';
      activeRow.style.opacity = '0';
      activeRow.style.transform = 'translateX(20px)';
      setTimeout(() => (activeRow.style.display = 'none'), 300);
    }
  }
}

// === RENDER FAVORITES VIEW ===
//
// Treat Liked Songs as a special, immutable playlist:
//   - Shares the playlist-style header (large cover + title + Play / Shuffle).
//   - The cover is the same purple→pink gradient with a big white heart that
//     the sidebar uses, so the visual identity is consistent everywhere.
//   - No rename / delete / publish / share controls — favorites are managed
//     per-track via the heart icon, never as a whole playlist.
export async function renderFavorites(container) {
  let favs = [];
  try {
    favs = await (await fetch(`${state.API}/favorites`)).json();
    favs = Array.isArray(favs) ? favs : [];
    state.setCurrentViewList(favs);
  } catch (e) {
    favs = [];
  }

  const trackCount = favs.length;
  const subtitle = trackCount === 1 ? '1 song' : `${trackCount} songs`;

  container.innerHTML = `
    <div class="playlist-header-section">
        <div class="playlist-cover-large playlist-cover-favorites">
            <i data-lucide="heart"></i>
        </div>
        <div class="playlist-info">
            <p class="playlist-type">Playlist</p>
            <div class="playlist-title-row">
                <h1 class="playlist-title">Liked Songs</h1>
            </div>
            <p class="playlist-stats">${subtitle}</p>
            <div class="playlist-actions">
                ${trackCount > 0 ? `
                <button onclick="playPlaylistInOrder()" class="btn-primary-lg playlist-action-btn"
                        title="Play" aria-label="Play">
                    <i data-lucide="play" width="20" height="20"></i>
                    <span class="playlist-btn-label">Play</span>
                </button>
                <button onclick="playPlaylistShuffle()" class="btn-secondary-lg playlist-action-btn"
                        title="Shuffle" aria-label="Shuffle">
                    <i data-lucide="shuffle" width="20" height="20"></i>
                    <span class="playlist-btn-label">Shuffle</span>
                </button>` : ''}
            </div>
        </div>
    </div>
    ${trackCount > 0
      ? `<div class="track-list">${favs
            .map((t, i) =>
              window.createTrackRow
                ? window.createTrackRow({ ...t, is_local: true, source: 'local', db_id: t.id }, i)
                : ''
            )
            .join('')}</div>`
      : `<div class="empty-state glass-panel">
            <i data-lucide="heart" class="empty-icon"></i>
            <p>Like some tracks to see them here!</p>
         </div>`}
  `;
  lucide.createIcons();
}
