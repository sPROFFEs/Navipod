/**
 * admin.js - Admin Functions
 * User management and admin actions
 */

import * as state from './state.js';
import * as ui from './ui.js';

function showAdminConfirmDialog({ title, message, confirmLabel = 'Continue', tone = 'danger' }) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.style.cssText = `
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.55);
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            z-index: 9999;
        `;

        const panel = document.createElement('div');
        panel.style.cssText = `
            width: min(100%, 460px);
            background: #12141a;
            border: 1px solid #2b313d;
            border-radius: 12px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.35);
            color: #f3f4f6;
        `;

        const confirmBg = tone === 'danger' ? '#341717' : '#3b2413';
        const confirmBorder = tone === 'danger' ? '#7f1d1d' : '#7c3d12';

        panel.innerHTML = `
            <div style="padding: 20px; display: grid; gap: 10px;">
                <h3 style="margin: 0; font-size: 1.1rem;">${ui.escHtml(title)}</h3>
                <p style="margin: 0; color: #9aa3b2; line-height: 1.45;">${ui.escHtml(message)}</p>
            </div>
            <div style="display: flex; justify-content: flex-end; gap: 10px; padding: 0 20px 20px;">
                <button type="button" data-role="cancel" style="border: 1px solid #2a2e38; background: #17191f; color: #f3f4f6; border-radius: 10px; min-height: 42px; padding: 0 14px; cursor: pointer;">Cancel</button>
                <button type="button" data-role="confirm" style="border: 1px solid ${confirmBorder}; background: ${confirmBg}; color: #f3f4f6; border-radius: 10px; min-height: 42px; padding: 0 14px; cursor: pointer;">${ui.escHtml(confirmLabel)}</button>
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

    if (row.style.display === 'none') {
        row.style.display = 'table-row';
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
            ui.showToast(data.error, "error");
        } else if (res.ok) {
            if (data.msg) ui.showToast(data.msg, "success");
            else ui.showToast("Action successful", "success");
            if (window.loadView) window.loadView('settings_admin');
        } else {
            ui.showToast("Server error", "error");
        }
    } catch (e) {
        console.error(e);
        ui.showToast("Network error or invalid response", "error");
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

    container.innerHTML = '<p style="color: var(--text-sub); text-align: center;">Searching...</p>';

    try {
        const res = await fetch(`/admin/api/library/search?q=${encodeURIComponent(q)}`);
        const tracks = await res.json();

        if (!tracks.length) {
            container.innerHTML = '<p style="color: var(--text-sub); text-align: center;">No tracks found.</p>';
            return;
        }

        container.innerHTML = `<table class="admin-table" style="font-size: 0.9rem;">
            <thead><tr>
                <th class="admin-th" style="padding: 12px;">Title</th>
                <th class="admin-th" style="padding: 12px;">Artist</th>
                <th class="admin-th" style="padding: 12px;">Source</th>
                <th class="admin-th" style="padding: 12px; text-align: right;">Actions</th>
            </tr></thead>
            <tbody>${tracks.map(t => `
                <tr class="admin-tr">
                    <td class="admin-td" style="padding: 12px;">${ui.escHtml(t.title)}</td>
                    <td class="admin-td" style="padding: 12px; color: var(--text-sub);">${ui.escHtml(t.artist)}</td>
                    <td class="admin-td" style="padding: 12px;"><span style="background: rgba(var(--accent-rgb),0.15); color: var(--accent); padding: 2px 8px; border-radius: 4px; font-size: 0.8rem;">${t.source_provider}</span></td>
                    <td class="admin-td" style="padding: 12px; text-align: right;">
                        <button onclick="showDeleteTrackModal(${t.id}, '${ui.escHtml(t.title).replace(/'/g, "\\'")}')" class="btn-icon" style="color: #ef4444;">
                            <i data-lucide="trash-2" width="16" height="16"></i>
                        </button>
                    </td>
                </tr>
            `).join('')}</tbody>
        </table>`;
        if (typeof lucide !== 'undefined') lucide.createIcons();
    } catch (e) {
        container.innerHTML = `<p style="color: #ef4444; text-align: center;">Error: ${e.message}</p>`;
    }
}

export async function adminFindDuplicates() {
    const container = document.getElementById('library-results');
    if (!container) return;

    container.innerHTML = '<p style="color: var(--text-sub); text-align: center;">Scanning for duplicates...</p>';

    try {
        const res = await fetch('/admin/api/library/duplicates');
        const data = await res.json();

        if (!data.count) {
            container.innerHTML = '<p style="color: #22c55e; text-align: center;"><strong>✓ No duplicates found!</strong> Your library is clean.</p>';
            return;
        }

        container.innerHTML = `<div style="margin-bottom: 16px;">
            <span style="color: #ef4444; font-weight: 700;">${data.count} duplicate groups found</span>
        </div>
        ${data.groups.map(group => `
            <div style="background: rgba(239,68,68,0.05); border: 1px solid rgba(239,68,68,0.2); border-radius: 8px; padding: 16px; margin-bottom: 16px;">
                <div style="color: var(--text-sub); font-size: 0.8rem; margin-bottom: 12px;">${group.key}</div>
                <table class="admin-table" style="font-size: 0.85rem;">
                    <tbody>${group.tracks.map(t => `
                        <tr class="admin-tr">
                            <td class="admin-td" style="padding: 8px;">${ui.escHtml(t.title)}</td>
                            <td class="admin-td" style="padding: 8px; color: var(--text-sub);">${ui.escHtml(t.artist)}</td>
                            <td class="admin-td" style="padding: 8px; text-align: right;">
                                <button onclick="showDeleteTrackModal(${t.id}, '${ui.escHtml(t.title).replace(/'/g, "\\'")}')" class="btn-icon" style="color: #ef4444;">
                                    <i data-lucide="trash-2" width="14" height="14"></i>
                                </button>
                            </td>
                        </tr>
                    `).join('')}</tbody>
                </table>
            </div>
        `).join('')}`;
        if (typeof lucide !== 'undefined') lucide.createIcons();
    } catch (e) {
        container.innerHTML = `<p style="color: #ef4444; text-align: center;">Error: ${e.message}</p>`;
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
            adminSearchLibrary(); // Refresh results
        } else {
            ui.showToast(data.message || 'Failed to delete track', 'error');
        }
    } catch (e) {
        ui.showToast('Error: ' + e.message, 'error');
    }
}
