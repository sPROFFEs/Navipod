/**
 * admin.js - Admin Functions
 * User management and admin actions
 */

import * as state from './state.js';
import * as ui from './ui.js';

function showAdminConfirmDialog({ title, message, confirmLabel = 'Continue', tone = 'danger' }) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'admin-confirm-overlay';

    const panel = document.createElement('div');
    panel.className = 'admin-confirm-panel';

    const confirmToneClass = tone === 'danger' ? 'danger' : 'warning';

    panel.innerHTML = `
            <div class="admin-confirm-body">
                <h3>${ui.escHtml(title)}</h3>
                <p>${ui.escHtml(message)}</p>
            </div>
            <div class="admin-confirm-actions">
                <button type="button" data-role="cancel" class="admin-secondary-btn">Cancel</button>
                <button type="button" data-role="confirm" class="admin-danger-btn ${confirmToneClass}">${ui.escHtml(confirmLabel)}</button>
            </div>
        `;

    function close(result) {
      overlay.remove();
      resolve(result);
    }

    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) close(false);
    });

    panel.querySelector('[data-role="cancel"]').addEventListener('click', () => close(false));
    panel.querySelector('[data-role="confirm"]').addEventListener('click', () => close(true));

    overlay.appendChild(panel);
    document.body.appendChild(overlay);
  });
}

// === TOGGLE PASSWORD RESET ROW ===

export function toggleReset(id) {
  const row = document.getElementById(`reset-row-${id}`);
  if (!row) return;

  const isHidden = row.style.display === 'none' || window.getComputedStyle(row).display === 'none';
  if (isHidden) {
    row.style.display = '';
  } else {
    row.style.display = 'none';
  }
}

// === GENERIC ADMIN ACTION ===

export async function adminAction(url, formData) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      body: formData
    });

    const data = await res.json();

    if (data.error) {
      ui.showToast(data.error, 'error');
    } else if (res.ok) {
      if (data.msg) ui.showToast(data.msg, 'success');
      else ui.showToast('Action successful', 'success');
      if (window.loadView) window.loadView('settings_admin');
    } else {
      ui.showToast('Server error', 'error');
    }
  } catch (e) {
    console.error(e);
    ui.showToast('Network error or invalid response', 'error');
  }
}

// === FORM HANDLERS ===

export async function handleAdminForm(event, url) {
  event.preventDefault();
  const formData = new FormData(event.target);
  await adminAction(url, formData);
}

export async function deleteUser(userId, username) {
  const confirmed = await showAdminConfirmDialog({
    title: 'Delete user',
    message: `Delete user "${username}"? This action is irreversible.`,
    confirmLabel: 'Delete',
    tone: 'danger'
  });
  if (!confirmed) return;
  const formData = new FormData();
  formData.append('user_id', userId);
  await adminAction('/admin/users/delete', formData);
}

export async function createUser(event) {
  event.preventDefault();
  const formData = new FormData(event.target);
  await adminAction('/admin/users/create', formData);
}

export async function resetPassword(userId, username) {
  const confirmed = await showAdminConfirmDialog({
    title: 'Reset password',
    message: `Reset the password for user "${username}"? A new random password will be generated.`,
    confirmLabel: 'Reset password',
    tone: 'warning'
  });
  if (!confirmed) return;
  const formData = new FormData();
  formData.append('user_id', userId);
  await adminAction('/admin/users/reset-password', formData);
}

// === LIBRARY MANAGEMENT ===

export async function adminSearchLibrary() {
  const input = document.getElementById('library-search-input');
  const q = input ? input.value.trim() : '';
  const container = document.getElementById('library-results');
  if (!container) return;

  container.innerHTML = '<div class="admin-feedback">Searching...</div>';

  try {
    const res = await fetch(`/admin/api/library/search?q=${encodeURIComponent(q)}`);
    const tracks = await res.json();

    if (!tracks.length) {
      container.innerHTML = '<div class="admin-feedback">No tracks found.</div>';
      return;
    }

    container.innerHTML = `
            <div class="admin-results-list">
                ${tracks
                  .map(
                    (t) => `
                    <article class="admin-result-row">
                        <div class="admin-result-main">
                            <div class="admin-result-title">${ui.escHtml(t.title)}</div>
                            <div class="admin-result-meta">${ui.escHtml(t.artist)}</div>
                        </div>
                        <div class="admin-result-side">
                            <span class="admin-source-badge">${ui.escHtml(t.source_provider || 'unknown')}</span>
                            <button onclick="showDeleteTrackModal(${t.id}, '${ui.escHtml(t.title).replace(/'/g, "\\'")}')" class="admin-icon-btn danger" title="Delete track">
                                <i data-lucide="trash-2" width="16" height="16"></i>
                            </button>
                        </div>
                    </article>
                `
                  )
                  .join('')}
            </div>`;
    if (typeof lucide !== 'undefined') lucide.createIcons();
  } catch (e) {
    container.innerHTML = `<div class="admin-feedback error">Error: ${ui.escHtml(e.message)}</div>`;
  }
}

function renderDuplicateScanStatus(container, job) {
  const details = job?.details || {};
  const progress = Number.isFinite(Number(details.progress)) ? Number(details.progress) : 0;
  const message = job?.message || 'Scanning for duplicates...';
  const phase = details.phase ? `Phase: ${ui.escHtml(details.phase)}` : '';

  container.innerHTML = `
        <div class="admin-feedback">
            <strong>${ui.escHtml(message)}</strong>
            <div class="admin-progress-mini" aria-label="Duplicate scan progress">
                <span style="width:${Math.max(0, Math.min(100, progress))}%"></span>
            </div>
            <div class="admin-feedback-meta">${progress}% ${phase}</div>
        </div>`;
}

function bindDuplicateDeleteHandlers(container) {
  if (container.dataset.duplicateDeleteBound === '1') return;
  container.dataset.duplicateDeleteBound = '1';
  container.addEventListener('click', (event) => {
    const button = event.target.closest('[data-action="delete-duplicate"]');
    if (!button || !container.contains(button)) return;
    const trackId = Number(button.dataset.trackId);
    if (!Number.isFinite(trackId)) return;
    showDeleteTrackModal(trackId, button.dataset.trackTitle || 'Track');
  });
}

function renderDuplicateScanResult(container, data) {
  if (!data?.count) {
    container.innerHTML =
      '<div class="admin-feedback success"><strong>Clean library.</strong> No duplicates found.</div>';
    return;
  }

  const truncatedNote = data.truncated
    ? `<div class="admin-feedback">Showing ${data.returned_count || data.groups.length} of ${data.count} groups. Refine cleanup in batches to keep the admin panel responsive.</div>`
    : '';

  container.innerHTML = `
        <div class="admin-duplicate-summary">${data.count} duplicate groups found</div>
        ${truncatedNote}
        <div class="admin-duplicate-groups">
            ${data.groups
              .map(
                (group) => `
                <section class="admin-duplicate-group">
                    <div class="admin-duplicate-group-key">${ui.escHtml(group.key)}</div>
                    <div class="admin-results-list compact">
                        ${group.tracks
                          .map(
                            (t) => `
                            <article class="admin-result-row compact">
                                <div class="admin-result-main">
                                    <div class="admin-result-title">${ui.escHtml(t.title)}</div>
                                    <div class="admin-result-meta">${ui.escHtml(t.artist)}</div>
                                </div>
                                <div class="admin-result-side">
                                    <button type="button" data-action="delete-duplicate" data-track-id="${t.id}" data-track-title="${ui.escHtml(t.title)}" class="admin-icon-btn danger" title="Delete duplicate">
                                        <i data-lucide="trash-2" width="14" height="14"></i>
                                    </button>
                                </div>
                            </article>
                        `
                          )
                          .join('')}
                    </div>
                </section>
            `
              )
              .join('')}
        </div>`;
  bindDuplicateDeleteHandlers(container);
  if (typeof lucide !== 'undefined') lucide.createIcons();
}

async function pollDuplicateScanJob(jobId, container, attempt = 0) {
  const res = await fetch(`/admin/api/system/jobs/${jobId}`);
  const job = await res.json();
  if (!res.ok) throw new Error(job.error || `HTTP ${res.status}`);

  if (job.status === 'completed') {
    renderDuplicateScanResult(container, job.details?.result);
    return;
  }

  if (job.status === 'failed') {
    throw new Error(job.message || job.details?.error || 'Duplicate scan failed');
  }

  renderDuplicateScanStatus(container, job);
  if (attempt >= 180) throw new Error('Duplicate scan timed out');
  window.setTimeout(() => {
    pollDuplicateScanJob(jobId, container, attempt + 1).catch((e) => {
      container.innerHTML = `<div class="admin-feedback error">Error: ${ui.escHtml(e.message)}</div>`;
    });
  }, 1500);
}

export async function adminFindDuplicates() {
  const container = document.getElementById('library-results');
  if (!container) return;

  container.innerHTML = '<div class="admin-feedback">Starting duplicate scan...</div>';

  try {
    const res = await fetch('/admin/api/library/duplicates/jobs', { method: 'POST' });
    const data = await res.json();
    if (!res.ok || !data.job_id) throw new Error(data.error || `HTTP ${res.status}`);
    await pollDuplicateScanJob(data.job_id, container);
  } catch (e) {
    container.innerHTML = `<div class="admin-feedback error">Error: ${ui.escHtml(e.message)}</div>`;
  }
}

export function showDeleteTrackModal(id, title) {
  const modal = document.createElement('div');
  modal.className = 'modal-overlay';
  modal.onclick = () => modal.remove();
  modal.innerHTML = `
        <div class="modal" onclick="event.stopPropagation()">
            <h2 style="margin-bottom: 16px;">Delete Track</h2>
            <p style="color: var(--text-sub); margin-bottom: 24px;">Permanently delete <strong style="color: white;">${ui.escHtml(title)}</strong> from the library and disk?</p>
            <div class="modal-actions">
                <button class="modal-btn-cancel" onclick="this.closest('.modal-overlay').remove()">Cancel</button>
                <button class="modal-btn-danger" onclick="adminDeleteTrack(${id}); this.closest('.modal-overlay').remove();">Delete</button>
            </div>
        </div>`;
  document.body.appendChild(modal);
}

export async function adminDeleteTrack(id) {
  try {
    const res = await fetch(`/admin/api/library/track/${id}`, { method: 'DELETE' });
    const data = await res.json();

    if (data.success) {
      ui.showToast('Track deleted successfully', 'success');
      adminSearchLibrary();
    } else {
      ui.showToast(data.message || 'Failed to delete track', 'error');
    }
  } catch (e) {
    ui.showToast(`Error: ${e.message}`, 'error');
  }
}

// === FEDERATION PANEL ======================================================
//
// admin.html is loaded via renderExternalView (DOMParser + replaceChildren),
// which silently drops inline <script> elements. So all the federation panel
// logic lives here, exposed via window.* and bootstrapped by an init call
// from views.js when the admin view mounts.

let _fedRefreshTimer = null;

function _fedEscape(str) {
  return (str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function _fedFmtDate(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}
function _fedStatusBadge(status) {
  const cls = ({
    healthy: 'fed-status-healthy',
    degraded: 'fed-status-degraded',
    offline: 'fed-status-offline',
    unknown: 'fed-status-unknown',
    online: 'fed-status-healthy',
    idle: 'fed-status-degraded',
    never: 'fed-status-unknown',
    revoked: 'fed-status-revoked',
  })[status] || 'fed-status-unknown';
  return `<span class="fed-status-badge ${cls}">${_fedEscape(status || 'unknown')}</span>`;
}

function _fedRenderInbound(inst) {
  const pct = inst.sync_total > 0
    ? Math.min(100, Math.round((inst.sync_done / inst.sync_total) * 100)) : null;
  const errorLine = inst.last_error ? `<div class="fed-row-error">⚠ ${_fedEscape(inst.last_error)}</div>` : '';
  const progressBar = pct !== null
    ? `<div class="fed-progress-bar"><div class="fed-progress-fill" style="width:${pct}%"></div></div>` : '';
  return `
    <div class="fed-row" data-id="${inst.id}">
      <div class="fed-row-head">
        <strong>${_fedEscape(inst.name)}</strong>
        ${_fedStatusBadge(inst.status)}
        <span class="fed-row-url">${_fedEscape(inst.base_url)}</span>
      </div>
      <div class="fed-row-meta">
        <span>Last seen: ${_fedFmtDate(inst.last_seen_at)}</span>
        <span>Last sync: ${_fedFmtDate(inst.last_sync_at)}</span>
        ${pct !== null ? `<span>${pct}% (${inst.sync_done}/${inst.sync_total})</span>` : ''}
        <span>State: ${_fedEscape(inst.sync_state)}</span>
      </div>
      ${progressBar}
      ${errorLine}
      <div class="fed-row-actions">
        <button class="admin-submit" onclick="federationSyncNow(${inst.id})">
          <i data-lucide="refresh-cw" width="14" height="14"></i> Sync now
        </button>
        <button class="admin-submit" onclick="federationToggleEnabled(${inst.id}, ${!inst.enabled})">
          <i data-lucide="${inst.enabled ? 'pause' : 'play'}" width="14" height="14"></i>
          ${inst.enabled ? 'Disable' : 'Enable'}
        </button>
        <button class="admin-danger-btn" onclick="federationDeleteInstance(${inst.id})">
          <i data-lucide="trash-2" width="14" height="14"></i> Remove
        </button>
      </div>
    </div>`;
}

// Distinguish the three real states we can observe so the user can
// debug what's going on instead of always seeing a generic empty
// message:
//   - 401  → session expired (very common after a docker recreate);
//            keep the previous list rendered and show a banner asking
//            for a refresh.
//   - 5xx / network → transient (concierge still booting?). Same
//                     thing: keep the prior list, show a transient
//                     banner, and the next 5s tick may recover.
//   - 200 + []      → genuinely no peers configured.
//   - 200 + items   → render normally.
//
// Critically, on transient failures we DO NOT clear the list. The
// previous bug was clobbering the rendered rows on the first hiccup
// after a recreate, making it look like the peers had been deleted.
function _fedShowBanner(el, message, level = 'warn') {
  // Insert (or update) a banner ABOVE the existing list rather than
  // wiping the list. Banner auto-clears on the next successful poll.
  let banner = el.querySelector('.fed-status-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.className = 'fed-status-banner';
    el.prepend(banner);
  }
  banner.dataset.level = level;
  banner.textContent = message;
}
function _fedClearBanner(el) {
  const banner = el.querySelector('.fed-status-banner');
  if (banner) banner.remove();
}

async function _fedRefreshInbound() {
  const el = document.getElementById('federation-instances-list');
  if (!el) return;
  try {
    const res = await fetch('/api/admin/federation/instances', { credentials: 'include' });
    if (res.status === 401 || res.status === 403) {
      _fedShowBanner(el, 'Session expired — refresh the page to re-authenticate.', 'auth');
      return;
    }
    if (!res.ok) {
      _fedShowBanner(el, `Could not refresh peer list (HTTP ${res.status}). Retrying in 5s…`, 'warn');
      return;
    }
    const items = await res.json();
    _fedClearBanner(el);
    el.innerHTML = items.length
      ? items.map(_fedRenderInbound).join('')
      : '<div class="admin-empty-state">No peers configured yet.</div>';
    if (window.lucide) lucide.createIcons();
  } catch (e) {
    // Network error (concierge still booting, etc.) — keep whatever's
    // currently rendered and show a transient banner. Do NOT replace
    // the rendered rows with an empty state.
    console.warn('[FED] inbound refresh failed', e);
    _fedShowBanner(el, 'Network error reaching the server. Retrying in 5s…', 'warn');
  }
}

function _fedRenderOutbound(peer) {
  const urlLine = peer.peer_url
    ? `<span class="fed-row-url">${_fedEscape(peer.peer_url)}</span>`
    : `<span class="fed-row-url fed-row-url-missing">no URL set</span>`;
  const ipLine = peer.last_seen_ip
    ? `<span>From: <code>${_fedEscape(peer.last_seen_ip)}</code></span>` : '';
  const ua = peer.last_seen_user_agent
    ? `<span class="fed-row-ua" title="${_fedEscape(peer.last_seen_user_agent)}">UA: ${_fedEscape(peer.last_seen_user_agent.slice(0, 40))}${peer.last_seen_user_agent.length > 40 ? '…' : ''}</span>` : '';
  const revokedBanner = peer.revoked
    ? `<div class="fed-row-error">Token revoked${peer.revoked_at ? ' on ' + _fedFmtDate(peer.revoked_at) : ''}.</div>` : '';
  const actions = peer.revoked
    ? `<button class="admin-danger-btn" onclick="federationDeleteOutbound(${peer.id})">
         <i data-lucide="trash-2" width="14" height="14"></i> Delete record
       </button>`
    : `<button class="admin-danger-btn" onclick="federationRevokeOutbound(${peer.id})">
         <i data-lucide="ban" width="14" height="14"></i> Revoke token
       </button>
       <button class="admin-danger-btn" onclick="federationDeleteOutbound(${peer.id})">
         <i data-lucide="trash-2" width="14" height="14"></i> Delete
       </button>`;
  return `
    <div class="fed-row" data-id="${peer.id}">
      <div class="fed-row-head">
        <strong>${_fedEscape(peer.name)}</strong>
        ${_fedStatusBadge(peer.status)}
        ${urlLine}
      </div>
      <div class="fed-row-meta">
        <span>Last seen: ${_fedFmtDate(peer.last_seen_at)}</span>
        ${ipLine}
        ${ua}
        <span>Issued: ${_fedFmtDate(peer.created_at)}</span>
      </div>
      ${revokedBanner}
      <div class="fed-row-actions">${actions}</div>
    </div>`;
}

async function _fedRefreshOutbound() {
  const el = document.getElementById('federation-outbound-list');
  if (!el) return;
  try {
    const res = await fetch('/api/admin/federation/outbound', { credentials: 'include' });
    if (res.status === 401 || res.status === 403) {
      _fedShowBanner(el, 'Session expired — refresh the page to re-authenticate.', 'auth');
      return;
    }
    if (!res.ok) {
      _fedShowBanner(el, `Could not refresh issued-token list (HTTP ${res.status}). Retrying in 5s…`, 'warn');
      return;
    }
    const items = await res.json();
    _fedClearBanner(el);
    el.innerHTML = items.length
      ? items.map(_fedRenderOutbound).join('')
      : '<div class="admin-empty-state">No tokens issued yet.</div>';
    if (window.lucide) lucide.createIcons();
  } catch (e) {
    // Same protection as inbound: keep prior list visible, surface
    // the transient error in a banner, let the 5s poll retry.
    console.warn('[FED] outbound refresh failed', e);
    _fedShowBanner(el, 'Network error reaching the server. Retrying in 5s…', 'warn');
  }
}

export async function federationAddInstance(e) {
  e.preventDefault();
  const name = document.getElementById('fed-add-name').value.trim();
  const base_url = document.getElementById('fed-add-url').value.trim();
  const api_token = document.getElementById('fed-add-token').value.trim();
  const res = await fetch('/api/admin/federation/instances', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, base_url, api_token, enabled: true }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert('Failed to add peer: ' + (err.detail || res.status));
    return;
  }
  document.getElementById('fed-add-name').value = '';
  document.getElementById('fed-add-url').value = '';
  document.getElementById('fed-add-token').value = '';
  const created = await res.json();
  try { await fetch('/api/admin/federation/instances/' + created.id + '/sync', { method: 'POST', credentials: 'include' }); } catch {}
  _fedRefreshInbound();
}

export async function federationSyncNow(id) {
  await fetch('/api/admin/federation/instances/' + id + '/sync', { method: 'POST', credentials: 'include' });
  setTimeout(_fedRefreshInbound, 1500);
}

export async function federationToggleEnabled(id, enable) {
  await fetch('/api/admin/federation/instances/' + id, {
    method: 'PATCH',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: enable }),
  });
  _fedRefreshInbound();
}

export async function federationDeleteInstance(id) {
  if (!confirm('Remove this peer? Its mirrored catalog rows will be deleted from this instance.')) return;
  await fetch('/api/admin/federation/instances/' + id, { method: 'DELETE', credentials: 'include' });
  _fedRefreshInbound();
}

export async function federationIssueToken(e) {
  e.preventDefault();
  const name = document.getElementById('fed-issue-name').value.trim();
  const peer_url = document.getElementById('fed-issue-url').value.trim();
  if (!name) { alert('Peer name is required.'); return; }

  const res = await fetch('/api/admin/federation/outbound', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, peer_url: peer_url || null }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert('Failed to issue token: ' + (err.detail || res.status));
    return;
  }
  const data = await res.json();
  const display = document.getElementById('federation-token-display');
  const value = document.getElementById('federation-token-value');
  if (display && value) {
    value.textContent = data.token;
    display.hidden = false;
  }
  document.getElementById('fed-issue-name').value = '';
  document.getElementById('fed-issue-url').value = '';
  _fedRefreshOutbound();
}

export async function federationRevokeOutbound(id) {
  if (!confirm('Revoke this token? The remote will lose access immediately. The row stays so you can still see when the peer was last online.')) return;
  await fetch('/api/admin/federation/outbound/' + id + '/revoke', { method: 'POST', credentials: 'include' });
  _fedRefreshOutbound();
}

export async function federationDeleteOutbound(id) {
  if (!confirm('Delete this token record permanently? You will lose the history of when the peer was online.')) return;
  await fetch('/api/admin/federation/outbound/' + id, { method: 'DELETE', credentials: 'include' });
  _fedRefreshOutbound();
}

// Called by views.js when the admin view mounts. Tears down on unmount
// so the periodic poll doesn't leak when the user navigates away.
export function initAdminFederationPanel(container) {
  if (!container || !container.querySelector('#federation-panel')) return;
  _fedRefreshInbound();
  _fedRefreshOutbound();
  if (_fedRefreshTimer) clearInterval(_fedRefreshTimer);
  _fedRefreshTimer = setInterval(() => {
    if (!document.getElementById('federation-panel')) {
      clearInterval(_fedRefreshTimer);
      _fedRefreshTimer = null;
      return;
    }
    _fedRefreshInbound();
    _fedRefreshOutbound();
  }, 5000);
}
