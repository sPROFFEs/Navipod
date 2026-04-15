function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function getPhaseLabel(phase, details = {}) {
    const rebuildRequired = !!details.rebuild_required;
    const labels = {
        queued: 'Queued',
        check: 'Checking updates',
        backup: 'Creating backup',
        fetch: 'Fetching remote revision',
        workspace: 'Updating workspace',
        migrate: 'Running migrations',
        build: rebuildRequired ? 'Building images' : 'Preparing services',
        recreate: rebuildRequired ? 'Restarting services' : 'Recreating services',
        health: 'Waiting for health check',
        cleanup: 'Cleaning up',
        done: 'Completed',
        error: 'Failed'
    };
    return labels[phase] || phase || 'Queued';
}

function getFriendlyMessage(data) {
    const phase = data.details?.phase || 'queued';
    const rebuildRequired = !!data.details?.rebuild_required;
    if (data.status === 'failed') {
        return data.message || 'Update failed';
    }
    if (phase === 'build' && rebuildRequired) {
        return 'Building updated containers. This can take several minutes.';
    }
    if (phase === 'recreate' && !rebuildRequired) {
        return 'Restarting services without rebuilding images.';
    }
    if (phase === 'health') {
        return 'Waiting for restarted services to become available.';
    }
    if (phase === 'cleanup') {
        return 'Finalizing update and cleaning temporary Docker data.';
    }
    return data.message || 'Waiting...';
}

export function initUpdateProgress(root = document) {
    const shell = root.querySelector('[data-update-job-id]');
    if (!shell || shell.dataset.initialized === 'true') return;
    shell.dataset.initialized = 'true';

    const jobId = shell.dataset.updateJobId;
    const jobEndpoint = shell.dataset.jobEndpoint || `/admin/api/system/jobs/${jobId}`;
    const successUrl = shell.dataset.jobSuccessUrl || '/admin/system?msg=Update applied successfully';
    const fallbackUrl = shell.dataset.jobFallbackUrl || '';
    const updaterMonitorUrl = shell.dataset.updaterMonitorUrl || '';
    const updaterJobEndpoint = shell.dataset.updaterJobEndpoint || '';
    const terminalStates = new Set(['completed', 'failed']);
    const statusNode = document.getElementById('job-status');
    const progressLabel = document.getElementById('job-progress-label');
    const progressBar = document.getElementById('job-progress-bar');
    const phaseNode = document.getElementById('job-phase');
    const messageNode = document.getElementById('job-message');
    const logContainer = document.getElementById('job-log');
    let redirectScheduled = false;
    let reconnectAttempts = 0;
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
        if (phaseNode) phaseNode.textContent = getPhaseLabel(data.details?.phase, data.details);
        if (messageNode) messageNode.textContent = getFriendlyMessage(data);
        renderLogItems(data.details?.logs || []);

        if (data.status === 'completed' && !redirectScheduled) {
            redirectScheduled = true;
            window.setTimeout(() => {
                window.location.href = successUrl;
            }, 1200);
        }
    }

    function renderReconnectState() {
        if (!lastJobData || terminalStates.has(lastJobData.status)) return;
        if (!['build', 'recreate', 'cleanup', 'health'].includes(lastJobData.details?.phase || '')) return;
        if (messageNode) messageNode.textContent = 'Waiting for services to come back after restart...';
        if (phaseNode) phaseNode.textContent = getPhaseLabel(lastJobData.details?.phase, lastJobData.details);
        const progressValue = Math.max(lastJobData.details?.progress || 0, 90);
        if (progressLabel) progressLabel.textContent = `${progressValue}%`;
        if (progressBar) progressBar.style.width = `${progressValue}%`;
    }

    async function maybeHandoffToUpdaterMonitor() {
        if (!updaterMonitorUrl || !updaterJobEndpoint) return;
        try {
            const res = await fetch(updaterJobEndpoint, {
                credentials: 'same-origin',
                cache: 'no-store',
                headers: { 'Accept': 'application/json' }
            });
            if (!res.ok) return;
            const contentType = res.headers.get('content-type') || '';
            if (!contentType.includes('application/json')) return;
            window.location.replace(updaterMonitorUrl);
        } catch (_error) {
            // Stay on the legacy monitor when updater is not publicly reachable.
        }
    }

    async function pollJob() {
        try {
            const res = await fetch(jobEndpoint, {
                credentials: 'same-origin',
                cache: 'no-store',
                headers: { 'Accept': 'application/json' }
            });
            if (!res.ok) {
                reconnectAttempts += 1;
                renderReconnectState();
                await maybeRedirectToMonitor();
                window.setTimeout(pollJob, 3000);
                return;
            }
            const contentType = res.headers.get('content-type') || '';
            if (!contentType.includes('application/json')) {
                reconnectAttempts += 1;
                renderReconnectState();
                await maybeRedirectToMonitor();
                window.setTimeout(pollJob, 3000);
                return;
            }
            const data = await res.json();
            reconnectAttempts = 0;
            renderJob(data);
            if (!terminalStates.has(data.status)) {
                window.setTimeout(pollJob, 2000);
            }
        } catch (_error) {
            reconnectAttempts += 1;
            renderReconnectState();
            await maybeRedirectToMonitor();
            window.setTimeout(pollJob, 3000);
        }
    }

    async function maybeRedirectToMonitor() {
        if (redirectScheduled) return;
        if (!fallbackUrl) return;
        if (reconnectAttempts < 3) return;
        if (!lastJobData || terminalStates.has(lastJobData.status)) return;
        if (!['build', 'recreate', 'health', 'cleanup'].includes(lastJobData.details?.phase || '')) return;
        try {
            const res = await fetch(fallbackUrl, {
                credentials: 'same-origin',
                cache: 'no-store',
            });
            if (res.ok) {
                redirectScheduled = true;
                window.location.href = `${fallbackUrl}${fallbackUrl.includes('?') ? '&' : '?'}msg=Update progress resumed after restart`;
            }
        } catch (_error) {
            // Keep polling the job endpoint.
        }
    }

    maybeHandoffToUpdaterMonitor().finally(() => {
        pollJob();
    });
}
