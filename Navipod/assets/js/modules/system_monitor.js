import { escHtml } from './ui.js';

export function initSystemMonitor(root = document) {
  const monitor = root.querySelector('.system-monitor');
  if (!monitor || monitor.dataset.initialized === 'true') return;
  monitor.dataset.initialized = 'true';

  const cpuValue = root.getElementById ? root.getElementById('cpu-value') : document.getElementById('cpu-value');
  const cpuBar = document.getElementById('cpu-bar');
  const ramValue = document.getElementById('ram-value');
  const ramSub = document.getElementById('ram-sub');
  const ramBar = document.getElementById('ram-bar');
  const poolValue = document.getElementById('pool-value');
  const poolSub = document.getElementById('pool-sub');
  const poolBar = document.getElementById('pool-bar');
  const confirmBackdrop = document.getElementById('monitor-confirm-backdrop');
  const confirmTitle = document.getElementById('monitor-confirm-title');
  const confirmMessage = document.getElementById('monitor-confirm-message');
  const confirmCancel = document.getElementById('monitor-confirm-cancel');
  const confirmSubmit = document.getElementById('monitor-confirm-submit');
  const wrappedRegenerateForm = document.getElementById('wrapped-regenerate-form');
  const wrappedRegenerateSubmit = document.getElementById('wrapped-regenerate-submit');
  const userStatisticsPeriod = document.getElementById('user-statistics-period');
  const userStatisticsSort = document.getElementById('user-statistics-sort');
  const userStatisticsRefresh = document.getElementById('user-statistics-refresh');
  const userStatisticsStatus = document.getElementById('user-statistics-status');
  const userStatisticsBody = document.getElementById('user-statistics-body');
  const userStatisticsUsers = document.getElementById('user-statistics-users');
  const userStatisticsActive = document.getElementById('user-statistics-active');
  const userStatisticsListens = document.getElementById('user-statistics-listens');
  const userStatisticsTime = document.getElementById('user-statistics-time');
  const userStatisticsPrev = document.getElementById('user-statistics-prev');
  const userStatisticsNext = document.getElementById('user-statistics-next');
  const userStatisticsPage = document.getElementById('user-statistics-page');
  let pendingForm = null;
  let statsTimer = null;
  let userStatisticsTimer = null;
  let userStatisticsController = null;
  let userStatisticsOffset = 0;
  const userStatisticsLimit = 25;

  function stopPolling() {
    if (statsTimer) clearInterval(statsTimer);
    if (userStatisticsTimer) clearInterval(userStatisticsTimer);
    document.removeEventListener('visibilitychange', handleVisibilityChange);
    statsTimer = null;
    userStatisticsTimer = null;
    userStatisticsController?.abort();
    userStatisticsController = null;
  }

  async function refreshStats() {
    if (!document.body.contains(monitor)) {
      stopPolling();
      return;
    }
    try {
      const res = await fetch('/admin/api/system-stats', { credentials: 'same-origin' });
      if (!res.ok) return;
      const data = await res.json();
      if (data.error) return;

      if (cpuValue) cpuValue.textContent = data.cpu_usage;
      if (cpuBar) cpuBar.style.width = `${data.cpu_usage}%`;
      if (ramValue) ramValue.textContent = data.ram.percent;
      if (ramSub) ramSub.textContent = `${data.ram.used_gb}GB / ${data.ram.total_gb}GB`;
      if (ramBar) ramBar.style.width = `${data.ram.percent}%`;
      if (poolValue) poolValue.textContent = data.pool.percent;
      if (poolSub) poolSub.textContent = `${data.pool.used}GB used of ${data.pool.limit}GB`;
      if (poolBar) {
        poolBar.style.width = `${data.pool.percent}%`;
        poolBar.style.background = data.pool.percent > 90 ? 'var(--monitor-red)' : 'var(--monitor-blue)';
      }
    } catch (_error) {
      // Ignore transient polling failures.
    }
  }

  function formatListeningTime(seconds) {
    const totalMinutes = Math.max(0, Math.round(Number(seconds || 0) / 60));
    if (totalMinutes < 60) return `${totalMinutes}m`;
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
  }

  function formatDate(value) {
    if (!value) return 'Never';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? 'Unknown' : date.toLocaleString();
  }

  function renderUserStatistics(data) {
    const totals = data.totals || {};
    if (userStatisticsUsers) userStatisticsUsers.textContent = Number(totals.users || 0).toLocaleString();
    if (userStatisticsActive) userStatisticsActive.textContent = Number(totals.active_users || 0).toLocaleString();
    if (userStatisticsListens)
      userStatisticsListens.textContent = Number(totals.qualified_listens || 0).toLocaleString();
    if (userStatisticsTime) userStatisticsTime.textContent = formatListeningTime(totals.listening_seconds);

    const users = Array.isArray(data.users) ? data.users : [];
    if (userStatisticsBody) {
      userStatisticsBody.innerHTML = users.length
        ? users
            .map((user) => {
              const topTrack = user.top_track
                ? `${escHtml(user.top_track.title)} · ${escHtml(user.top_track.artist)}`
                : 'No qualified listens';
              const statusLabel = user.data_status === 'unavailable' ? 'Statistics unavailable' : '';
              return `<tr>
                <td data-label="User">
                  <strong>${escHtml(user.username)}</strong>
                  <span>${user.is_active ? 'Active' : 'Inactive'}${user.is_admin ? ' · Admin' : ''}${statusLabel ? ` · ${statusLabel}` : ''}</span>
                </td>
                <td data-label="Listening">
                  <strong>${formatListeningTime(user.listening_seconds)}</strong>
                  <span>${Number(user.qualified_listens || 0).toLocaleString()} listens · ${Number(user.unique_tracks || 0).toLocaleString()} tracks</span>
                </td>
                <td data-label="Collection">
                  <strong>${Number(user.playlist_count || 0).toLocaleString()} playlists</strong>
                  <span>${Number(user.favorite_count || 0).toLocaleString()} favorites</span>
                </td>
                <td data-label="Top music">
                  <strong>${topTrack}</strong>
                  <span>${user.top_artist ? `Top artist: ${escHtml(user.top_artist)}` : 'No top artist yet'}</span>
                </td>
                <td data-label="Last listen">
                  <strong>${escHtml(formatDate(user.last_listen_at))}</strong>
                  <span>Account access: ${escHtml(formatDate(user.last_access))}</span>
                </td>
              </tr>`;
            })
            .join('')
        : '<tr><td colspan="5" class="monitor-empty">No regular users found.</td></tr>';
    }

    const pagination = data.pagination || {};
    const total = Number(pagination.total || 0);
    const start = total ? Number(pagination.offset || 0) + 1 : 0;
    const end = Math.min(Number(pagination.offset || 0) + Number(pagination.limit || userStatisticsLimit), total);
    if (userStatisticsPage) userStatisticsPage.textContent = `${start}–${end} of ${total}`;
    if (userStatisticsPrev) userStatisticsPrev.disabled = userStatisticsOffset <= 0;
    if (userStatisticsNext) userStatisticsNext.disabled = userStatisticsOffset + userStatisticsLimit >= total;
    if (userStatisticsStatus) {
      userStatisticsStatus.textContent = `Updated ${formatDate(data.generated_at)} · refreshes every 30 seconds`;
      userStatisticsStatus.classList.remove('error');
    }
  }

  async function refreshUserStatistics(force = false) {
    if (!userStatisticsBody || document.hidden || (!force && !document.body.contains(monitor))) return;
    userStatisticsController?.abort();
    const controller = new AbortController();
    userStatisticsController = controller;
    if (userStatisticsRefresh) userStatisticsRefresh.disabled = true;
    if (userStatisticsStatus) userStatisticsStatus.textContent = 'Refreshing user statistics…';
    const period = userStatisticsPeriod?.value || '30d';
    const sort = userStatisticsSort?.value || 'listening_seconds';
    const order = sort === 'username' ? 'asc' : 'desc';
    const params = new URLSearchParams({
      period,
      sort,
      order,
      limit: String(userStatisticsLimit),
      offset: String(userStatisticsOffset)
    });
    try {
      const response = await fetch(`/admin/api/user-statistics?${params}`, {
        credentials: 'same-origin',
        signal: controller.signal
      });
      if (response.status === 401 || response.status === 403) {
        stopPolling();
        throw new Error('Admin session expired. Sign in again to refresh statistics.');
      }
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      renderUserStatistics(data);
    } catch (error) {
      if (error.name === 'AbortError') return;
      if (userStatisticsStatus) {
        userStatisticsStatus.textContent = error.message || 'Unable to load user statistics.';
        userStatisticsStatus.classList.add('error');
      }
    } finally {
      if (userStatisticsController === controller) {
        userStatisticsController = null;
        if (userStatisticsRefresh) userStatisticsRefresh.disabled = false;
      }
    }
  }

  function handleVisibilityChange() {
    if (!document.body.contains(monitor)) {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      stopPolling();
      return;
    }
    if (!document.hidden) {
      refreshStats();
      refreshUserStatistics(true);
    }
  }

  function closeConfirmModal() {
    if (!confirmBackdrop) return;
    confirmBackdrop.classList.remove('show');
    confirmBackdrop.setAttribute('aria-hidden', 'true');
    pendingForm = null;
  }

  function openConfirmModal(form) {
    if (!confirmBackdrop || !confirmTitle || !confirmMessage) {
      form.submit();
      return;
    }
    pendingForm = form;
    confirmTitle.textContent = form.dataset.title || 'Confirm action';
    confirmMessage.textContent = form.dataset.confirm || 'This action requires confirmation.';
    confirmBackdrop.classList.add('show');
    confirmBackdrop.setAttribute('aria-hidden', 'false');
  }

  monitor.querySelectorAll('.monitor-form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      openConfirmModal(form);
    });
  });

  confirmCancel?.addEventListener('click', closeConfirmModal);
  confirmBackdrop?.addEventListener('click', (event) => {
    if (event.target === confirmBackdrop) closeConfirmModal();
  });
  confirmSubmit?.addEventListener('click', () => {
    if (!pendingForm) return;
    const form = pendingForm;
    closeConfirmModal();
    form.submit();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && confirmBackdrop?.classList.contains('show')) {
      closeConfirmModal();
    }
  });

  wrappedRegenerateForm?.addEventListener('submit', () => {
    if (!wrappedRegenerateSubmit) return;
    wrappedRegenerateSubmit.disabled = true;
    wrappedRegenerateSubmit.innerHTML =
      '<i data-lucide="loader-2" style="animation: spin 1s linear infinite;"></i><span>Queuing...</span>';
    if (window.lucide) window.lucide.createIcons();
  });

  userStatisticsPeriod?.addEventListener('change', () => {
    userStatisticsOffset = 0;
    refreshUserStatistics(true);
  });
  userStatisticsSort?.addEventListener('change', () => {
    userStatisticsOffset = 0;
    refreshUserStatistics(true);
  });
  userStatisticsRefresh?.addEventListener('click', () => refreshUserStatistics(true));
  userStatisticsPrev?.addEventListener('click', () => {
    userStatisticsOffset = Math.max(0, userStatisticsOffset - userStatisticsLimit);
    refreshUserStatistics(true);
  });
  userStatisticsNext?.addEventListener('click', () => {
    userStatisticsOffset += userStatisticsLimit;
    refreshUserStatistics(true);
  });
  document.addEventListener('visibilitychange', handleVisibilityChange);

  refreshStats();
  refreshUserStatistics(true);
  statsTimer = window.setInterval(refreshStats, 10000);
  userStatisticsTimer = window.setInterval(refreshUserStatistics, 30000);
}
