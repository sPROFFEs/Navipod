import { initSystemMonitor } from './system_monitor.js';
import { initUpdateProgress } from './update_progress.js';

function initUpdateToast() {
    const toast = document.getElementById('update-available-toast');
    if (!toast || toast.dataset.initialized === 'true') return;
    toast.dataset.initialized = 'true';

    const toastMessage = document.getElementById('update-toast-message');
    const dismissButton = document.getElementById('update-toast-dismiss');
    const openButton = document.getElementById('update-toast-open');
    let pollTimer = null;
    let pollAttempts = 0;
    const maxPollAttempts = 12;

    async function loadUpdateNotification() {
        try {
            const response = await fetch('/admin/api/update-notification', { credentials: 'same-origin' });
            if (response.status === 401 || response.status === 403) return false;
            if (!response.ok) return true;
            const data = await response.json();
            if (!data.update_available || !data.remote_full_commit) return true;

            const dismissKey = `navipod-dismissed-update:${data.remote_full_commit}`;
            if (localStorage.getItem(dismissKey) === '1') return false;

            const versionLabel = data.remote_version || data.remote_release_version || data.remote_commit || 'unknown';
            toastMessage.textContent = `Update available ${versionLabel}`;
            toast.style.display = 'block';

            if (pollTimer) {
                clearInterval(pollTimer);
                pollTimer = null;
            }

            dismissButton.onclick = () => {
                localStorage.setItem(dismissKey, '1');
                toast.style.display = 'none';
            };
            openButton.onclick = () => {
                toast.style.display = 'none';
                window.location.href = '/admin/system';
            };
            return false;
        } catch (_error) {
            // Ignore notification failures.
            return true;
        }
    }

    async function startNotificationPolling() {
        const shouldContinue = await loadUpdateNotification();
        if (!shouldContinue || pollTimer) return;

        pollTimer = window.setInterval(async () => {
            pollAttempts += 1;
            const keepPolling = await loadUpdateNotification();
            if (!keepPolling || pollAttempts >= maxPollAttempts) {
                clearInterval(pollTimer);
                pollTimer = null;
            }
        }, 5000);
    }

    startNotificationPolling();
}

function initAdminSystem() {
    initUpdateToast();
    initSystemMonitor(document);
    initUpdateProgress(document);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAdminSystem);
} else {
    initAdminSystem();
}
