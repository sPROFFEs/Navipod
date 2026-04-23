/**
 * downloads.js - Download Manager
 * Queue downloads, poll status, and display jobs
 */

import * as state from './state.js';
import * as ui from './ui.js';

function getJobUiState(job) {
  const status = (job.status || '').toLowerCase();

  if (status === 'completed' || status === 'finished') {
    return { badge: 'completed', label: 'Completed', icon: 'check-circle', active: false };
  }
  if (status === 'failed' || status === 'error') {
    return { badge: 'failed', label: 'Failed', icon: 'alert-circle', active: false };
  }
  if (status === 'processing' || status === 'downloading') {
    return { badge: 'processing', label: 'Processing', icon: 'loader-2', active: true };
  }
  return { badge: 'pending', label: 'Queued', icon: 'clock-3', active: true };
}

function isDownloadsModalOpen() {
  const modal = document.getElementById('downloads-modal');
  return !!modal && !modal.classList.contains('hidden');
}

function isDeleteResponsesModalOpen() {
  const modal = document.getElementById('delete-responses-modal');
  return !!modal && !modal.classList.contains('hidden');
}

function formatDeleteStatus(status) {
  const normalized = String(status || '')
    .trim()
    .toLowerCase();
  if (normalized === 'approved') return { label: 'Approved', badge: 'finished' };
  if (normalized === 'rejected') return { label: 'Rejected', badge: 'error' };
  return { label: 'Pending', badge: 'pending' };
}

function formatDeleteDate(value) {
  if (!value) return '-';
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return '-';
  return dt.toLocaleString([], {
    hour: '2-digit',
    minute: '2-digit',
    day: '2-digit',
    month: '2-digit'
  });
}

function formatSourceLabel(source) {
  const normalized = String(source || '')
    .trim()
    .toLowerCase();
  const labels = {
    spotify: 'Spotify',
    youtube: 'YouTube',
    musicbrainz: 'MusicBrainz',
    lastfm: 'Last.fm',
    soundcloud: 'SoundCloud',
    audius: 'Audius',
    jamendo: 'Jamendo',
    external: 'External URL'
  };
  return (
    labels[normalized] || (normalized ? normalized.charAt(0).toUpperCase() + normalized.slice(1) : 'Unknown source')
  );
}

function formatEngineLabel(engine) {
  const normalized = String(engine || '')
    .trim()
    .toLowerCase();
  const labels = {
    spotiflac: 'SpotiFLAC',
    'spotdl-auth': 'spotDL (auth)',
    'spotdl-anonymous': 'spotDL (anonymous)',
    'spotdl-basic': 'spotDL (basic)',
    'yt-dlp': 'yt-dlp',
    'yt-dlp-spotify-fallback': 'yt-dlp Spotify fallback',
    dedupe: 'Library dedupe'
  };
  return labels[normalized] || (normalized ? normalized : 'Pending');
}

function buildDownloadResolutionLines(job) {
  const lines = [];
  const requested = [job.requested_artist, job.requested_title].filter(Boolean).join(' - ') || job.requested_title;
  const resolved =
    [job.resolved_artist, job.resolved_title].filter(Boolean).join(' - ') ||
    job.resolved_track_title ||
    job.resolved_title;

  lines.push(['Requested source', formatSourceLabel(job.requested_source || job.source)]);
  if (job.original_input_url && job.original_input_url !== job.url)
    lines.push(['Original input', job.original_input_url]);
  if (requested) lines.push(['Requested track', requested]);
  if (resolved) lines.push(['Resolved track', resolved]);
  if (job.resolved_track_id) lines.push(['Track ID', `#${job.resolved_track_id}`]);
  if (job.engine_used) lines.push(['Engine', formatEngineLabel(job.engine_used)]);
  if (job.resolution_mode) lines.push(['Resolution', job.resolution_mode]);
  if (job.fallback_reason) lines.push(['Fallback', job.fallback_reason]);
  if (job.error_type) lines.push(['Error type', job.error_type]);

  return lines
    .map(
      ([label, value]) => `
        <div class="job-resolution-row">
            <span>${ui.escHtml(label)}</span>
            <strong>${ui.escHtml(String(value || ''))}</strong>
        </div>
    `
    )
    .join('');
}

function ensureDownloadPolling() {
  if (!state.downloadPolling) {
    state.setDownloadPolling(setInterval(refreshJobs, 3000));
  }
}

export function initDownloadHud() {
  refreshJobs();
  ensureDownloadPolling();
}

async function refreshDeleteResponsesBadge() {
  try {
    const res = await fetch(`${state.API}/tracks/delete-requests/unseen-count`);
    if (!res.ok) return;
    const payload = await res.json();
    const unseen = Number(payload.unseen_count || 0);
    const badge = document.getElementById('delete-responses-badge');
    if (!badge) return;
    if (unseen > 0) {
      badge.textContent = String(unseen);
      badge.style.display = 'block';
    } else {
      badge.style.display = 'none';
    }
  } catch (e) {
    // Silent by design: this badge is secondary UI.
  }
}

// === OPEN/CLOSE MODAL ===

export function openDownloadsModal() {
  const modal = document.getElementById('downloads-modal');
  if (modal) modal.classList.remove('hidden');
  refreshJobs();
  refreshDeleteResponsesBadge();
  ensureDownloadPolling();
}

export function closeDownloadsModal() {
  const modal = document.getElementById('downloads-modal');
  if (modal) modal.classList.add('hidden');
  if (state.downloadPolling) {
    clearInterval(state.downloadPolling);
    state.setDownloadPolling(null);
  }
}

export async function openDeleteResponsesModal() {
  const modal = document.getElementById('delete-responses-modal');
  if (modal) modal.classList.remove('hidden');
  await refreshDeleteResponses();
  await acknowledgeDeleteResponses();
  await refreshDeleteResponsesBadge();
  ensureDownloadPolling();
}

export function closeDeleteResponsesModal() {
  const modal = document.getElementById('delete-responses-modal');
  if (modal) modal.classList.add('hidden');
}

async function acknowledgeDeleteResponses() {
  try {
    await fetch(`${state.API}/tracks/delete-requests/ack`, { method: 'POST' });
  } catch (e) {
    // No-op: badge will refresh later.
  }
}

export async function refreshDeleteResponses() {
  const container = document.getElementById('delete-responses-list');
  if (!container || !isDeleteResponsesModalOpen()) return;

  try {
    const res = await fetch(`${state.API}/tracks/delete-requests/mine`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    const items = Array.isArray(payload.items) ? payload.items : [];
    if (!items.length) {
      container.innerHTML =
        '<p style="text-align:center; color: var(--text-sub, #999); margin-top:20px;">No delete requests yet.</p>';
      return;
    }

    container.innerHTML = items
      .map((item) => {
        const status = formatDeleteStatus(item.status);
        const reviewText =
          status.badge === 'pending'
            ? 'Waiting for admin review.'
            : item.review_note
              ? ui.escHtml(item.review_note)
              : status.badge === 'finished'
                ? 'The track was removed from library.'
                : 'Request rejected.';

        return `
          <div class="job-item">
              <div class="job-header">
                  <div>
                      <div class="job-title">${ui.escHtml(item.track_title || 'Unknown Track')}</div>
                      <div class="job-detail" style="margin-top:4px; font-size:0.78rem; color: var(--text-sub, #aaa);">
                          ${ui.escHtml(item.track_artist || 'Unknown Artist')}
                      </div>
                  </div>
                  <span class="status-badge ${status.badge}">${status.label}</span>
              </div>
              <div class="job-resolution">
                  <div class="job-resolution-row">
                      <span>Reason sent</span>
                      <strong>${ui.escHtml(item.reason || '-')}</strong>
                  </div>
                  <div class="job-resolution-row">
                      <span>Admin response</span>
                      <strong>${reviewText}</strong>
                  </div>
              </div>
              <div class="job-footer">
                  <div class="job-footer-left">
                      <i data-lucide="messages-square" width="14" height="14"></i>
                      <span>Requested: ${formatDeleteDate(item.requested_at)}</span>
                  </div>
                  <span>${status.badge === 'pending' ? 'Pending' : `Reviewed: ${formatDeleteDate(item.reviewed_at)}`}</span>
              </div>
          </div>`;
      })
      .join('');
    lucide.createIcons();
  } catch (e) {
    container.innerHTML =
      '<p style="text-align:center; color: #ff9d9d; margin-top:20px;">Could not load request responses.</p>';
  }
}

// === REFRESH JOBS LIST ===

export async function refreshJobs() {
  try {
    refreshDeleteResponsesBadge();
    if (isDeleteResponsesModalOpen()) refreshDeleteResponses();

    const res = await fetch(`${state.API}/jobs`);
    const jobs = await res.json();
    const container = document.getElementById('jobs-list');
    const badge = document.getElementById('download-badge');

    const activeCount = jobs.filter((j) => {
      const status = (j.status || '').toLowerCase();
      return status === 'downloading' || status === 'processing' || status === 'pending';
    }).length;
    if (badge) {
      if (activeCount > 0) {
        badge.innerText = activeCount;
        badge.style.display = 'block';
      } else {
        badge.style.display = 'none';
      }
    }

    if (!container) return;
    if (!isDownloadsModalOpen()) return;

    if (!jobs.length) {
      container.innerHTML = '<p style="text-align:center; color: #666; margin-top:20px;">No downloads yet.</p>';
      return;
    }

    container.innerHTML = jobs
      .map((j) => {
        const uiState = getJobUiState(j);
        const statusIcon = uiState.icon;
        const isSpinning = uiState.active ? 'style="animation: spin 2s linear infinite;"' : '';
        const detail = ui.escHtml(j.error || j.detail || j.filename || j.url || '');
        const title = ui.escHtml(
          j.resolved_track_title || j.resolved_title || j.track_title || j.filename || j.url || 'Untitled download'
        );
        const source = ui.escHtml(formatSourceLabel(j.source));
        const resolutionLines = buildDownloadResolutionLines(j);

        return `
            <div class="job-item">
                <div class="job-header">
                    <div>
                        <div class="job-title">${title}</div>
                        <div class="job-detail" style="margin-top:4px; font-size:0.78rem; color: var(--text-sub, #aaa);">
                            Source: ${source}
                        </div>
                    </div>
                    <span class="status-badge ${uiState.badge}">${uiState.label}</span>
                </div>
                <div class="job-progress-bg">
                    <div class="job-progress-fill" style="width: ${j.progress}%"></div>
                </div>
                <div class="job-detail" style="margin-top:8px; font-size:0.82rem; color: ${j.error ? '#ff9d9d' : 'var(--text-sub, #aaa)'};">
                    ${detail}
                </div>
                ${resolutionLines ? `<div class="job-resolution">${resolutionLines}</div>` : ''}
                <div class="job-footer">
                    <div class="job-footer-left">
                        <i data-lucide="${statusIcon}" width="14" height="14" ${isSpinning}></i>
                        <span>${Math.max(0, Math.min(100, j.progress || 0))}% completion</span>
                    </div>
                    <span>${new Date(j.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                </div>
            </div>`;
      })
      .join('');
    lucide.createIcons();
  } catch (e) {
    console.error('Refresh jobs failed:', e);
  }
}

// === HANDLE MODAL DOWNLOAD ===

export async function handleModalDownload() {
  const input = document.getElementById('dl-modal-url');
  const url = input?.value?.trim();
  if (!url) return;

  // Check for duplicate
  try {
    const dupCheck = await fetch(`${state.API}/check-duplicate?url=${encodeURIComponent(url)}`);
    const dupData = await dupCheck.json();
    if (dupData.exists) {
      ui.showToast(`Already in library: "${dupData.track.title}" by ${dupData.track.artist}`, 'info');
      if (input) input.value = '';
      return;
    }
  } catch (e) {
    /* continue if check fails */
  }

  ui.showToast('Queuing download...');
  try {
    const res = await fetch(`${state.API}/download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, title: '', artist: '', album: '', source: '' })
    });
    if (res.ok) {
      if (input) input.value = '';
      const payload = await res.json();
      ui.showToast(payload.message || 'Download queued', 'success');
      ensureDownloadPolling();
      refreshJobs();
    } else {
      const err = await res.json();
      ui.showToast(err.error || 'Failed', 'error');
    }
  } catch (e) {
    ui.showToast('Network error', 'error');
  }
}

// === TRIGGER DOWNLOAD FROM SEARCH ===

export async function triggerDownload(trackData) {
  let track = trackData;
  if (typeof track === 'string') {
    try {
      track = JSON.parse(decodeURIComponent(atob(trackData)));
    } catch (e) {
      console.error('Failed to decode track data', e);
      return;
    }
  }

  ui.showToast(`Downloading: ${track.title || 'Track'}...`);
  try {
    const res = await fetch(`${state.API}/download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: track.id || track.url,
        title: track.title,
        artist: track.artist,
        album: track.album,
        source: track.source
      })
    });
    if (res.ok) {
      const payload = await res.json();
      ui.showToast(payload.message || 'Download queued', 'success');
      openDownloadsModal();
    } else {
      const err = await res.json();
      ui.showToast(err.error || 'Failed', 'error');
    }
  } catch (e) {
    ui.showToast('Network error', 'error');
  }
}

// === DOWNLOAD CONFIRMATION MODAL ===

export function showDownloadConfirmModal(track) {
  const encoded = btoa(encodeURIComponent(JSON.stringify(track)));
  const html = `<div class="modal-overlay" onclick="closeModal()">
        <div class="modal" onclick="event.stopPropagation()">
            <h2 style="margin-bottom: 16px;">Download Track</h2>
            <p style="color: var(--text-sub); margin-bottom: 24px;">
                Download <strong style="color: white;">${ui.escHtml(track.title)}</strong> by ${ui.escHtml(track.artist)}?
            </p>
            <div class="modal-actions">
                <button class="modal-btn-cancel" onclick="closeModal()">Cancel</button>
                <button class="modal-btn-primary" onclick="executeDownload(this, '${encoded}')">
                    <i data-lucide="download"></i> Download
                </button>
            </div>
        </div>
    </div>`;
  document.getElementById('modal-container').innerHTML = html;
  lucide.createIcons();
}

export async function executeDownload(btn, encodedTrack) {
  btn.disabled = true;
  btn.innerHTML = '<i data-lucide="loader-2" style="animation: spin 1s linear infinite;"></i> Starting...';
  lucide.createIcons();

  try {
    const track = JSON.parse(decodeURIComponent(atob(encodedTrack)));
    const res = await fetch(`${state.API}/download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: track.id || track.url,
        title: track.title,
        artist: track.artist,
        album: track.album,
        source: track.source
      })
    });

    if (res.ok) {
      const payload = await res.json();
      ui.showToast(payload.message || 'Download queued', 'success');
      ui.closeModal();
      openDownloadsModal();
    } else {
      const err = await res.json();
      ui.showToast(err.error || 'Failed', 'error');
    }
  } catch (e) {
    ui.showToast('Error: ' + e.message, 'error');
  }

  btn.disabled = false;
  btn.innerHTML = '<i data-lucide="download"></i> Download';
  lucide.createIcons();
}
