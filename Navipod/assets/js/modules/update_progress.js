function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

export function initUpdateProgress(root = document) {
    const shell = root.querySelector('[data-update-job-id]');
    if (!shell || shell.dataset.initialized === 'true') return;
    shell.dataset.initialized = 'true';

    const jobId = shell.dataset.updateJobId;
    const terminalStates = new Set(['completed', 'failed']);
    const statusNode = document.getElementById('job-status');
    const progressLabel = document.getElementById('job-progress-label');
    const progressBar = document.getElementById('job-progress-bar');
    const phaseNode = document.getElementById('job-phase');
    const messageNode = document.getElementById('job-message');
    const logContainer = document.getElementById('job-log');
    let redirectScheduled = false;
    let lastJobData = {
        status: shell.dataset.initialStatus || 'queued',
        details: {
            phase: shell.dataset.initialPhase || 'queued',
            progress: Number(shell.dataset.initialProgress || '0')
        }
    };

    function renderLogItems(logs) {
        if (!logContainer) return;
        logContainer.innerHTML = logs.map((item) => `
            <div class="update-log-item">
                <time>${escapeHtml(item.at)}</time>
                <div>${escapeHtml(item.message)}</div>
            </div>
        `).join('');
        logContainer.scrollTop = logContainer.scrollHeight;
    }

    function renderJob(data) {
        lastJobData = data;
        if (statusNode) {
            statusNode.textContent = data.status || 'unknown';
            statusNode.className = `update-status-pill ${data.status || ''}`;
        }
        const progressValue = data.status === 'completed' ? 100 : (data.details?.progress || 0);
        if (progressLabel) progressLabel.textContent = `${progressValue}%`;
        if (progressBar) progressBar.style.width = `${progressValue}%`;
        if (phaseNode) phaseNode.textContent = data.details?.phase || 'queued';
        if (messageNode) messageNode.textContent = data.message || 'Waiting...';
        renderLogItems(data.details?.logs || []);

        if (data.status === 'completed' && !redirectScheduled) {
            redirectScheduled = true;
            window.setTimeout(() => {
                window.location.href = '/admin/system?msg=Update applied successfully';
            }, 1200);
        }
    }

    function renderReconnectState() {
        if (!lastJobData || terminalStates.has(lastJobData.status)) return;
        if (!['recreate', 'cleanup', 'health'].includes(lastJobData.details?.phase || '')) return;
        if (messageNode) messageNode.textContent = 'Waiting for services to come back after restart...';
        if (phaseNode) phaseNode.textContent = lastJobData.details?.phase || 'recreate';
        const progressValue = Math.max(lastJobData.details?.progress || 0, 90);
        if (progressLabel) progressLabel.textContent = `${progressValue}%`;
        if (progressBar) progressBar.style.width = `${progressValue}%`;
    }

    async function pollJob() {
        try {
            const res = await fetch(`/admin/api/system/jobs/${jobId}`, { credentials: 'same-origin' });
            if (!res.ok) {
                renderReconnectState();
                window.setTimeout(pollJob, 3000);
                return;
            }
            const data = await res.json();
            renderJob(data);
            if (!terminalStates.has(data.status)) {
                window.setTimeout(pollJob, 2000);
            }
        } catch (_error) {
            renderReconnectState();
            window.setTimeout(pollJob, 3000);
        }
    }

    pollJob();
}
