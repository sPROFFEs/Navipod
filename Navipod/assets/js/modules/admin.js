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
        tone: 'danger',
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
        tone: 'warning',
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
                ${tracks.map((t) => `
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
                `).join('')}
            </div>`;
        if (typeof lucide !== 'undefined') lucide.createIcons();
    } catch (e) {
        container.innerHTML = `<div class="admin-feedback error">Error: ${ui.escHtml(e.message)}</div>`;
    }
}

export async function adminFindDuplicates() {
    const container = document.getElementById('library-results');
    if (!container) return;

    container.innerHTML = '<div class="admin-feedback">Scanning for duplicates...</div>';

    try {
        const res = await fetch('/admin/api/library/duplicates');
        const data = await res.json();

        if (!data.count) {
            container.innerHTML = '<div class="admin-feedback success"><strong>Clean library.</strong> No duplicates found.</div>';
            return;
        }

        container.innerHTML = `
            <div class="admin-duplicate-summary">${data.count} duplicate groups found</div>
            <div class="admin-duplicate-groups">
                ${data.groups.map((group) => `
                    <section class="admin-duplicate-group">
                        <div class="admin-duplicate-group-key">${ui.escHtml(group.key)}</div>
                        <div class="admin-results-list compact">
                            ${group.tracks.map((t) => `
                                <article class="admin-result-row compact">
                                    <div class="admin-result-main">
                                        <div class="admin-result-title">${ui.escHtml(t.title)}</div>
                                        <div class="admin-result-meta">${ui.escHtml(t.artist)}</div>
                                    </div>
                                    <div class="admin-result-side">
                                        <button onclick="showDeleteTrackModal(${t.id}, '${ui.escHtml(t.title).replace(/'/g, "\\'")}')" class="admin-icon-btn danger" title="Delete duplicate">
                                            <i data-lucide="trash-2" width="14" height="14"></i>
                                        </button>
                                    </div>
                                </article>
                            `).join('')}
                        </div>
                    </section>
                `).join('')}
            </div>`;
        if (typeof lucide !== 'undefined') lucide.createIcons();
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
