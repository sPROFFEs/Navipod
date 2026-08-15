/** Live party rooms: discovery, membership, queue editing, and player sync. */

import * as state from './state.js';
import * as ui from './ui.js';
import * as player from './player.js';

let activeRoom = null;
let eventSource = null;
let autoplayBlocked = false;
let searchTimer = null;
let personalPlayback = null;
let reconnectProbeTimer = null;
let streamGeneration = 0;
let stateApplyChain = Promise.resolve();

function setPartyPlayerMode(enabled) {
  document.getElementById('fullscreen-player')?.classList.toggle('party-player', enabled);
  document.querySelector('.player-footer')?.classList.toggle('party-player', enabled);
}

async function request(path, options = {}) {
  const response = await fetch(`/api/party${path}`, {
    credentials: 'include',
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
  });
  let body = {};
  try {
    body = await response.json();
  } catch (_) {}
  if (!response.ok) {
    const error = new Error(body.error || `Request failed (HTTP ${response.status})`);
    error.status = response.status;
    throw error;
  }
  return body;
}

export async function fetchRooms() {
  const data = await request('/rooms');
  return data.rooms || [];
}

function roomCard(room, compact = false) {
  const track = room.current_track;
  return `
    <button class="party-card${compact ? ' party-card-compact' : ''}" onclick="loadView('party_room', ${room.id})">
      <span class="party-card-icon"><i data-lucide="radio-tower"></i></span>
      <span class="party-card-copy">
        <strong>${ui.escHtml(room.name)}</strong>
        <span>Hosted by ${ui.escHtml(room.owner_username)} · ${room.active_users}/${room.max_users} listening</span>
        <span class="party-card-track">${track ? `${ui.escHtml(track.title)} — ${ui.escHtml(track.artist)}` : `${room.queue_count ?? room.queue?.length ?? 0} songs ready`}</span>
      </span>
      <span class="party-status ${room.playback_status}">${room.playback_status}</span>
    </button>`;
}

export function renderHomeShelf(rooms) {
  return `
    <section class="shelf-section party-home-shelf">
      <div class="shelf-header">
        <h2 class="shelf-title">Party rooms</h2>
        <button class="party-text-action" onclick="loadView('party')">See all</button>
      </div>
      <div class="party-home-grid">${
        rooms?.length
          ? rooms
              .slice(0, 3)
              .map((room) => roomCard(room, true))
              .join('')
          : `<button class="party-card party-card-compact party-empty-card" onclick="loadView('party')"><span class="party-card-icon"><i data-lucide="plus"></i></span><span class="party-card-copy"><strong>Start a party</strong><span>Create the first shared listening room.</span></span></button>`
      }</div>
    </section>`;
}

export async function renderPartyList(container) {
  const rooms = await fetchRooms();
  const owned = rooms.find((room) => room.is_owner);
  container.innerHTML = `
    ${ui.homeTabsBar('party')}
    <section class="party-page-head">
      <div><span class="hero-kicker">Listen together</span><h1>Party rooms</h1>
      <p>Join a shared queue and stay on the same song with everyone in the room.</p></div>
      <button class="btn-primary party-create-btn" onclick="showCreatePartyModal()" ${owned ? 'disabled title="Delete your existing room first"' : ''}>
        <i data-lucide="plus"></i> Create room
      </button>
    </section>
    ${
      owned
        ? `<div class="party-owner-notice"><i data-lucide="info"></i><span>You already own <strong>${ui.escHtml(owned.name)}</strong>. Delete it before creating another room.</span></div>`
        : ''
    }
    <div class="party-room-list">
      ${rooms.length ? rooms.map((room) => roomCard(room)).join('') : `<div class="empty-state glass-panel"><i data-lucide="radio-tower" class="empty-icon"></i><p>No party rooms yet. Start the first one.</p></div>`}
    </div>`;
  ui.refreshIcons(container);
}

export function showCreateModal() {
  const playlists = state.userPlaylists || [];
  document.getElementById('modal-container').innerHTML = `
    <div class="modal-overlay" onclick="if(event.target===this) closeModal()">
      <form class="modal-box party-create-modal" onsubmit="createPartyRoom(event)">
        <div class="modal-header"><h2>Create a party room</h2><button type="button" class="modal-close" onclick="closeModal()"><i data-lucide="x"></i></button></div>
        <label class="party-field"><span>Room name</span><input id="party-room-name" maxlength="80" placeholder="${ui.escHtml(window.USER_DATA?.username || 'My')}’s Party"></label>
        <label class="party-field"><span>User limit</span><select id="party-room-limit">${Array.from(
          { length: 14 },
          (_, i) => i + 2
        )
          .map((n) => `<option value="${n}" ${n === 5 ? 'selected' : ''}>${n} people</option>`)
          .join('')}</select><small>Maximum 15 listeners, including you.</small></label>
        <label class="party-field"><span>Start with a playlist</span><select id="party-room-playlist"><option value="">Start with an empty queue</option>${playlists
          .map((playlist) => `<option value="${playlist.id}">${ui.escHtml(playlist.name)}</option>`)
          .join('')}</select></label>
        <label class="party-check"><input id="party-guests-queue" type="checkbox" checked><span><strong>Let guests add songs</strong><small>You still control playback and can remove songs.</small></span></label>
        <button class="btn-primary party-submit" type="submit">Create and open room</button>
      </form>
    </div>`;
  ui.refreshIcons(document.getElementById('modal-container'));
}

export async function createRoom(event) {
  event?.preventDefault();
  const button = event?.submitter;
  if (button) button.disabled = true;
  try {
    const playlistValue = document.getElementById('party-room-playlist').value;
    const data = await request('/rooms', {
      method: 'POST',
      body: JSON.stringify({
        name: document.getElementById('party-room-name').value.trim() || null,
        max_users: Number(document.getElementById('party-room-limit').value),
        allow_guests_queue: document.getElementById('party-guests-queue').checked,
        playlist_id: playlistValue ? Number(playlistValue) : null
      })
    });
    ui.closeModal();
    await window.loadView('party_room', data.room.id);
  } catch (error) {
    ui.showToast(error.message, 'error');
    if (button) button.disabled = false;
  }
}

function closeStream() {
  streamGeneration += 1;
  if (eventSource) eventSource.close();
  eventSource = null;
  clearTimeout(reconnectProbeTimer);
  reconnectProbeTimer = null;
}

function connect(roomId) {
  if (eventSource && activeRoom?.id === roomId) return;
  closeStream();
  const generation = streamGeneration;
  eventSource = new EventSource(`/api/party/rooms/${roomId}/events`);
  eventSource.addEventListener('state', (event) => {
    stateApplyChain = stateApplyChain
      .then(async () => {
        if (generation !== streamGeneration) return;
        const payload = JSON.parse(event.data);
        if (payload.type === 'deleted') {
          leave(false);
          ui.showToast('The party room was deleted', 'error');
          window.loadView('party');
          return;
        }
        if (!payload.room) return;
        const previous = activeRoom;
        const permissions = activeRoom
          ? { is_owner: activeRoom.is_owner, can_add_songs: activeRoom.can_add_songs }
          : {};
        const nextRoom = { ...(activeRoom || {}), ...payload.room, ...permissions };
        activeRoom = nextRoom;
        const wasBlocked = autoplayBlocked;
        const synced = await player.syncPartyPlayback(nextRoom);
        if (generation !== streamGeneration || activeRoom?.id !== nextRoom.id) return;
        autoplayBlocked = !synced;
        const shouldPaint =
          !previous ||
          previous.revision !== activeRoom.revision ||
          previous.active_users !== activeRoom.active_users ||
          previous.playback_status !== activeRoom.playback_status ||
          previous.current_track?.db_id !== activeRoom.current_track?.db_id ||
          wasBlocked !== autoplayBlocked;
        if (
          shouldPaint &&
          state.currentViewName === 'party_room' &&
          Number(state.currentViewParam) === Number(roomId)
        ) {
          paintRoom(document.getElementById('view-container'));
        }
      })
      .catch((error) => console.error('Failed to apply party state', error));
  });
  eventSource.onerror = () => {
    const status = document.getElementById('party-connection-status');
    if (status) status.textContent = 'Reconnecting…';
    clearTimeout(reconnectProbeTimer);
    reconnectProbeTimer = setTimeout(async () => {
      if (!activeRoom || Number(activeRoom.id) !== Number(roomId)) return;
      try {
        await request(`/rooms/${roomId}`);
      } catch (error) {
        if (error.status === 404) {
          leave(false);
          ui.showToast('The party room was deleted', 'error');
          window.loadView('party');
        } else if (error.status === 401) {
          window.location.assign('/login');
        }
      }
    }, 1200);
  };
}

export async function renderPartyRoom(container, roomId) {
  const data = await request(`/rooms/${roomId}/join`, { method: 'POST', body: '{}' });
  if (!activeRoom) {
    personalPlayback = {
      track: state.currentTrack,
      currentTime: Number(state.audio.currentTime || 0),
      userQueue: [...state.userQueue],
      contextQueue: [...state.contextQueue],
      originalContextQueue: [...state.originalContextQueue],
      contextIndex: state.contextIndex,
      shuffleMode: state.shuffleMode,
      repeatMode: state.repeatMode
    };
    state.audio.pause();
  }
  if (activeRoom?.id !== data.room.id) {
    closeStream();
    activeRoom = data.room;
    autoplayBlocked = false;
  } else {
    activeRoom = { ...activeRoom, ...data.room };
  }
  setPartyPlayerMode(true);
  paintRoom(container);
  connect(data.room.id);
}

function paintRoom(container) {
  if (!container || !activeRoom) return;
  const room = activeRoom;
  const current = room.current_track;
  const participants = room.participants || [];
  container.innerHTML = `
    ${ui.homeTabsBar('party')}
    <section class="party-room-hero">
      <div class="party-now-cover">${current ? `<img src="${current.thumbnail}" onerror="this.src='/static/img/default_cover.png'">` : `<i data-lucide="radio-tower"></i>`}</div>
      <div class="party-room-title"><span class="hero-kicker">Live party · <span id="party-connection-status">Connected</span></span><h1>${ui.escHtml(room.name)}</h1>
        <p>Hosted by ${ui.escHtml(room.owner_username)} · ${room.active_users}/${room.max_users} listening</p>
        <div class="party-participants">${participants.map((p) => `<span><i data-lucide="user-round"></i>${ui.escHtml(p.username)}</span>`).join('') || '<span>Connecting listeners…</span>'}</div>
      </div>
      <button class="party-leave-btn" onclick="leavePartyRoom()"><i data-lucide="log-out"></i> Leave</button>
    </section>
    ${
      autoplayBlocked && room.playback_status === 'playing'
        ? `<button class="party-autoplay-banner" onclick="resumePartyAudio()"><i data-lucide="volume-2"></i><span>Your browser blocked autoplay. Tap to start listening.</span></button>`
        : ''
    }
    <section class="party-layout">
      <div class="party-main-column">
        <div class="party-now-playing glass-panel">
          <span>Now playing</span><h2>${current ? ui.escHtml(current.title) : 'Queue is waiting'}</h2><p>${current ? ui.escHtml(current.artist) : 'Add a song to get started.'}</p>
          ${
            room.is_owner
              ? `<div class="party-owner-controls"><button onclick="partyControl('previous')"><i data-lucide="skip-back"></i></button><button class="party-control-primary" onclick="partyControl('${['playing', 'loading'].includes(room.playback_status) ? 'pause' : 'play'}')" title="${room.playback_status === 'loading' ? 'Cancel loading' : ''}"><i data-lucide="${['playing', 'loading'].includes(room.playback_status) ? 'pause' : 'play'}"></i></button><button onclick="partyControl('next')"><i data-lucide="skip-forward"></i></button></div>`
              : `<div class="party-following"><i data-lucide="waves"></i> Playback follows the host</div>`
          }
        </div>
        <div class="party-queue-head"><div><h2>Shared queue</h2><p>${room.queue.length} ${room.queue.length === 1 ? 'song' : 'songs'}</p></div></div>
        <div class="party-queue">${
          room.queue.length
            ? room.queue
                .map(
                  (item, index) => `<div class="party-queue-row${index === room.current_index ? ' current' : ''}">
                    <span class="party-queue-index">${index === room.current_index ? '<i data-lucide="volume-2"></i>' : index + 1}</span>
                    <img src="${item.track.thumbnail}" loading="lazy" onerror="this.src='/static/img/default_cover.png'">
                    <span class="party-queue-copy"><strong>${ui.escHtml(item.track.title)}</strong><small>${ui.escHtml(item.track.artist)}${item.added_by ? ` · added by ${ui.escHtml(item.added_by)}` : ''}</small></span>
                    ${room.is_owner ? `<button class="party-row-remove" onclick="removePartyTrack(${item.item_id})" aria-label="Remove"><i data-lucide="x"></i></button>` : ''}
                  </div>`
                )
                .join('')
            : '<div class="party-empty-queue">The shared queue is empty.</div>'
        }</div>
      </div>
      <aside class="party-side-column">
        ${
          room.can_add_songs
            ? `<div class="party-add-panel glass-panel"><h2>Add songs</h2><p>Search tracks already in this Navipod library.</p><div class="party-search"><input id="party-track-search" placeholder="Song or artist" oninput="searchPartyTracks(this.value)"><i data-lucide="search"></i></div><div id="party-search-results" class="party-search-results"></div></div>`
            : `<div class="party-locked-panel glass-panel"><i data-lucide="lock"></i><h3>Host-managed queue</h3><p>${ui.escHtml(room.owner_username)} has disabled guest additions.</p></div>`
        }
        ${room.is_owner ? `<button class="party-delete-btn" onclick="deletePartyRoom()"><i data-lucide="trash-2"></i> Delete room</button>` : ''}
      </aside>
    </section>`;
  ui.refreshIcons(container);
}

export async function control(action, expectedItemId = null) {
  if (!activeRoom) return;
  if (!activeRoom.is_owner) {
    if (action === 'play' && activeRoom.playback_status === 'playing') return resumeAudio();
    if (!['ended', 'ready'].includes(action)) {
      ui.showToast('Only the host controls party playback', 'error');
      return;
    }
    // The server accepts transition signals from guests only after the owner
    // has left; normal playback control remains host-owned.
  }
  try {
    await request(`/rooms/${activeRoom.id}/control`, {
      method: 'POST',
      body: JSON.stringify({
        action,
        position_ms: Math.round(state.audio.currentTime * 1000),
        expected_item_id: expectedItemId
      })
    });
  } catch (error) {
    ui.showToast(error.message, 'error');
  }
}

export function seekTo(seconds) {
  if (!activeRoom?.is_owner) {
    ui.showToast('Only the host can seek', 'error');
    player.syncPartyPlayback(activeRoom);
    return;
  }
  request(`/rooms/${activeRoom.id}/control`, {
    method: 'POST',
    body: JSON.stringify({ action: 'seek', position_ms: Math.max(0, Math.round(seconds * 1000)) })
  }).catch((error) => ui.showToast(error.message, 'error'));
}

export function handleEnded() {
  if (!activeRoom) return;
  const item = activeRoom.queue?.[activeRoom.current_index];
  if (!item) return;
  if (activeRoom.is_owner || !(activeRoom.participants || []).some((user) => user.id === activeRoom.owner_id)) {
    control('ended', item.item_id);
    return;
  }
  // The owner may be reconnecting. Keep host authority through the server's
  // eight-second grace window, then retry only if this exact item is current.
  const roomId = activeRoom.id;
  const itemId = item.item_id;
  setTimeout(() => {
    const current = activeRoom?.queue?.[activeRoom.current_index];
    if (activeRoom?.id === roomId && !activeRoom.is_owner && current?.item_id === itemId) {
      control('ended', itemId);
    }
  }, 8500);
}

export function togglePlayback() {
  if (!activeRoom) return;
  control(['playing', 'loading'].includes(activeRoom.playback_status) ? 'pause' : 'play');
}

export async function resumeAudio() {
  if (!activeRoom) return;
  autoplayBlocked = !(await player.syncPartyPlayback(activeRoom));
  paintRoom(document.getElementById('view-container'));
}

export function searchTracks(query) {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const results = document.getElementById('party-search-results');
    if (!results || !activeRoom) return;
    try {
      const data = await request(`/rooms/${activeRoom.id}/tracks?q=${encodeURIComponent(query)}`);
      results.innerHTML = data.tracks
        .map(
          (track) =>
            `<button class="party-search-row" onclick="addPartyTrack(${track.db_id})"><img src="${track.thumbnail}" onerror="this.src='/static/img/default_cover.png'"><span><strong>${ui.escHtml(track.title)}</strong><small>${ui.escHtml(track.artist)}</small></span><i data-lucide="plus"></i></button>`
        )
        .join('');
      ui.refreshIcons(results);
    } catch (error) {
      results.innerHTML = `<p class="party-search-error">${ui.escHtml(error.message)}</p>`;
    }
  }, 250);
}

export async function addTrack(trackId) {
  if (!activeRoom) return;
  try {
    await request(`/rooms/${activeRoom.id}/queue`, {
      method: 'POST',
      body: JSON.stringify({ track_id: trackId })
    });
    ui.showToast('Added to the party queue', 'success');
  } catch (error) {
    ui.showToast(error.message, 'error');
  }
}

export async function removeTrack(itemId) {
  if (!activeRoom) return;
  try {
    await request(`/rooms/${activeRoom.id}/queue/${itemId}`, { method: 'DELETE' });
  } catch (error) {
    ui.showToast(error.message, 'error');
  }
}

export async function deleteRoom() {
  if (!activeRoom || !window.confirm(`Delete “${activeRoom.name}”? This disconnects every listener.`)) return;
  try {
    await request(`/rooms/${activeRoom.id}`, { method: 'DELETE' });
    leave(false);
    await window.loadView('party');
  } catch (error) {
    ui.showToast(error.message, 'error');
  }
}

export function leave(navigate = true) {
  closeStream();
  // Pause while the controller is still active so the party track is never
  // written into the listener's personal playback session.
  state.audio.pause();
  activeRoom = null;
  autoplayBlocked = false;
  setPartyPlayerMode(false);
  restorePersonalPlayback();
  if (navigate) window.loadView('party');
}

function restorePersonalPlayback() {
  const snapshot = personalPlayback;
  personalPlayback = null;
  if (!snapshot) return;
  state.setUserQueue(snapshot.userQueue);
  state.setContextQueue(snapshot.contextQueue);
  state.setOriginalContextQueue(snapshot.originalContextQueue);
  state.setContextIndex(snapshot.contextIndex);
  state.setShuffleMode(snapshot.shuffleMode);
  state.setRepeatMode(snapshot.repeatMode);
  if (!snapshot.track?.db_id) {
    state.setCurrentTrack(null);
    state.audio.removeAttribute('src');
    state.audio.load();
    player.syncPlayerShellVisibility(null);
    return;
  }
  player.playTrack(snapshot.track, { autoplay: false });
  const restorePosition = () => {
    try {
      state.audio.currentTime = Math.min(snapshot.currentTime, state.audio.duration || snapshot.currentTime);
    } catch (_) {}
    player.persistPlaybackSession();
  };
  if (state.audio.readyState >= 1) restorePosition();
  else state.audio.addEventListener('loadedmetadata', restorePosition, { once: true });
}

export const controller = {
  isActive: () => Boolean(activeRoom),
  control,
  togglePlayback,
  seekTo,
  handleEnded
};
