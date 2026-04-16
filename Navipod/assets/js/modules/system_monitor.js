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
  let pendingForm = null;
  let statsTimer = null;

  async function refreshStats() {
    if (!document.body.contains(monitor)) {
      if (statsTimer) clearInterval(statsTimer);
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

  refreshStats();
  statsTimer = window.setInterval(refreshStats, 10000);
}
