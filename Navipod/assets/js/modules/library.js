/** Browsable library facets and smart-playlist controls. */

import * as api from './api.js';
import * as state from './state.js';
import * as ui from './ui.js';

let activeKind = 'playlists';

function playlistRow(pl) {
  const tracks = Number(pl.track_count || 0);
  const type = pl.is_smart ? 'Smart playlist' : pl.source_playlist_id ? 'Synced playlist' : 'Playlist';
  const icon = pl.is_smart ? 'sparkles' : pl.source_playlist_id ? 'refresh-cw' : 'list-music';
  const thumb = pl.thumbnail || '/static/img/default_cover.png';
  const hasThumb = pl.thumbnail && !pl.thumbnail.includes('default');
  return `
    <div class="library-row" onclick="loadView('playlist', ${pl.id})">
      <div class="library-row-cover">
        ${hasThumb ? `<img src="${ui.escHtml(thumb)}" loading="lazy" onerror="this.src='/static/img/default_cover.png'">` : `<i data-lucide="${icon}"></i>`}
      </div>
      <div class="library-row-meta">
        <div class="library-row-name">${ui.escHtml(pl.name || 'Playlist')}</div>
        <div class="library-row-sub">${type} · ${tracks} ${tracks === 1 ? 'song' : 'songs'}</div>
      </div>
      ${pl.is_smart ? `<button class="library-row-action" onclick="event.stopPropagation(); refreshSmartPlaylist(${pl.id})" title="Refresh rules"><i data-lucide="refresh-cw"></i></button>` : ''}
    </div>`;
}

function facetRow(kind, facet) {
  const singular = kind === 'artists' ? 'artist' : kind === 'albums' ? 'album' : 'genre';
  const icon = kind === 'artists' ? 'user-round' : kind === 'albums' ? 'disc-3' : 'tags';
  const encoded = encodeURIComponent(facet.name).replace(/'/g, '%27');
  return `
    <button class="library-row library-facet-row" onclick="openLibraryFacet('${singular}', decodeURIComponent('${encoded}'))">
      <span class="library-row-cover"><i data-lucide="${icon}"></i></span>
      <span class="library-row-meta">
        <span class="library-row-name">${ui.escHtml(facet.name)}</span>
        <span class="library-row-sub">${facet.track_count} ${facet.track_count === 1 ? 'song' : 'songs'}</span>
      </span>
      <i data-lucide="chevron-right"></i>
    </button>`;
}

function shell(content) {
  return `
    <section class="library-shell">
      <header class="library-head">
        <h1 class="library-title">Your Library</h1>
        <div class="library-head-actions">
          <button class="library-icon-btn" onclick="loadView('search')" title="Search"><i data-lucide="search"></i></button>
          <button class="library-icon-btn" onclick="showCreateSmartPlaylistModal()" title="New smart playlist"><i data-lucide="sparkles"></i></button>
          <button class="library-icon-btn" onclick="showCreatePlaylistModal()" title="New playlist"><i data-lucide="plus"></i></button>
        </div>
      </header>
      <div class="library-filters">
        ${['playlists', 'artists', 'albums', 'genres'].map((kind) => `<button class="library-filter${kind === activeKind ? ' active' : ''}" onclick="switchLibraryKind('${kind}')">${kind[0].toUpperCase()}${kind.slice(1)}</button>`).join('')}
      </div>
      ${content}
    </section>`;
}

export async function renderLibrary(container, kind = activeKind) {
  activeKind = kind;
  try {
    if (kind === 'playlists') {
      const playlists = await api.fetchPlaylists();
      state.setUserPlaylists(playlists);
      container.innerHTML = shell(
        playlists.length
          ? `<div class="library-list">${playlists.map(playlistRow).join('')}</div>`
          : '<div class="empty-state"><p>No playlists yet. Use + or create a smart playlist.</p></div>'
      );
    } else {
      const facets = await api.fetchLibraryFacets(kind);
      container.innerHTML = shell(
        facets.length
          ? `<div class="library-list">${facets.map((facet) => facetRow(kind, facet)).join('')}</div>`
          : `<div class="empty-state"><p>No ${kind} with metadata found.</p></div>`
      );
    }
    ui.refreshIcons(container);
  } catch (error) {
    container.innerHTML = '<div class="empty-state glass-panel"><p>Failed to load your library.</p></div>';
    console.error('[LIBRARY]', error);
  }
}

export async function switchLibraryKind(kind) {
  const container = document.getElementById('view-container');
  if (container) await renderLibrary(container, kind);
}

export async function openLibraryFacet(key, value) {
  const container = document.getElementById('view-container');
  if (!container) return;
  try {
    const tracks = await api.fetchLibraryTracks({ [key]: value });
    state.setCurrentViewList(tracks);
    container.innerHTML = `
      <section class="library-shell">
        <button class="library-back" onclick="switchLibraryKind('${key === 'artist' ? 'artists' : key === 'album' ? 'albums' : 'genres'}')"><i data-lucide="arrow-left"></i> Library</button>
        <h1 class="library-title">${ui.escHtml(value)}</h1>
        <p class="library-facet-count">${tracks.length} songs</p>
        <div class="track-list">${tracks
          .map(
            (track, index) => `
          <button class="library-track-row" onclick="playFromView(${index})">
            <img src="${track.thumbnail}" loading="lazy" onerror="this.src='/static/img/default_cover.png'">
            <span><strong>${ui.escHtml(track.title)}</strong><small>${ui.escHtml(track.artist)} · ${ui.escHtml(track.album || '')}${track.year ? ` · ${track.year}` : ''}</small></span>
            <i data-lucide="play"></i>
          </button>`
          )
          .join('')}</div>
      </section>`;
    ui.refreshIcons(container);
  } catch (error) {
    ui.showToast(error.message, 'error');
  }
}

export function showCreateSmartPlaylistModal() {
  document.getElementById('modal-container').innerHTML = `
    <div class="modal-overlay" onclick="if(event.target===this) closeModal()">
      <div class="modal smart-playlist-modal">
        <h2>New smart playlist</h2>
        <p class="modal-subtitle">Matching songs update whenever you refresh the playlist.</p>
        <input id="smart-name" class="modal-input" maxlength="100" placeholder="Playlist name">
        <div class="smart-rule-grid">
          <input id="smart-artist" class="modal-input" placeholder="Artist (optional)">
          <input id="smart-album" class="modal-input" placeholder="Album (optional)">
          <input id="smart-genre" class="modal-input" placeholder="Genre (optional)">
          <input id="smart-year-min" class="modal-input" type="number" min="1000" max="9999" placeholder="Released after year">
          <input id="smart-year-max" class="modal-input" type="number" min="1000" max="9999" placeholder="Released before year">
          <input id="smart-days" class="modal-input" type="number" min="0" placeholder="Added in last N days">
          <input id="smart-min-plays" class="modal-input" type="number" min="0" placeholder="Minimum play count">
          <input id="smart-unplayed" class="modal-input" type="number" min="0" placeholder="Not played for N days">
          <input id="smart-limit" class="modal-input" type="number" min="1" max="500" value="50" aria-label="Maximum songs">
          <select id="smart-sort" class="modal-input"><option value="newest">Newest added</option><option value="artist">Artist</option><option value="album">Album</option><option value="most_played">Most played</option><option value="least_played">Least played</option></select>
          <label class="smart-check"><input id="smart-favorites" type="checkbox"> Favorites only</label>
        </div>
        <div class="modal-actions"><button class="btn-secondary" onclick="closeModal()">Cancel</button><button class="btn-primary" onclick="createSmartPlaylist()">Create</button></div>
      </div>
    </div>`;
}

function optionalNumber(id) {
  const value = document.getElementById(id)?.value;
  return value === '' || value == null ? null : Number(value);
}

export async function createSmartPlaylist() {
  const name = document.getElementById('smart-name')?.value.trim();
  if (!name) return ui.showToast('Enter a playlist name.', 'error');
  try {
    const result = await api.createSmartPlaylistApi({
      name,
      rules: {
        artist: document.getElementById('smart-artist')?.value.trim() || '',
        album: document.getElementById('smart-album')?.value.trim() || '',
        genre: document.getElementById('smart-genre')?.value.trim() || '',
        year_min: optionalNumber('smart-year-min'),
        year_max: optionalNumber('smart-year-max'),
        added_within_days: optionalNumber('smart-days'),
        min_plays: optionalNumber('smart-min-plays'),
        not_played_days: optionalNumber('smart-unplayed'),
        favorite_only: Boolean(document.getElementById('smart-favorites')?.checked),
        limit: optionalNumber('smart-limit') || 50,
        sort: document.getElementById('smart-sort')?.value || 'newest'
      }
    });
    ui.closeModal();
    ui.showToast(`Created with ${result.track_count} songs.`, 'success');
    await switchLibraryKind('playlists');
  } catch (error) {
    ui.showToast(error.message, 'error');
  }
}

export async function refreshSmartPlaylist(playlistId) {
  try {
    const result = await api.refreshSmartPlaylistApi(playlistId);
    ui.showToast(`Updated: ${result.track_count} songs.`, 'success');
    await switchLibraryKind('playlists');
  } catch (error) {
    ui.showToast(error.message, 'error');
  }
}
