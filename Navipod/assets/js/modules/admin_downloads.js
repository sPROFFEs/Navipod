function setStatus(root, message, type = '') {
  const status = root.querySelector('#download-manager-status');
  if (!status) return;
  status.textContent = message || '';
  status.className = `download-manager-live-status ${type}`.trim();
}

async function responseJson(response) {
  let payload = {};
  try {
    payload = await response.json();
  } catch (_error) {}
  if (!response.ok) throw new Error(payload.detail || `Request failed with HTTP ${response.status}`);
  return payload;
}

function formatExpiry(value) {
  if (!value) return 'Not connected';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function applyProviderState(root, provider) {
  const card = [...root.querySelectorAll('[data-provider-card]')].find(
    (candidate) => candidate.dataset.providerCard === provider.provider
  );
  if (!card) return;
  const state = card.querySelector('[data-provider-state]');
  const expiry = card.querySelector('[data-provider-expiry]');
  const hostVerification = card.querySelector('[data-provider-host-verification]');
  const disconnect = card.querySelector('[data-provider-disconnect]');
  if (state) {
    state.textContent = String(provider.status || 'unknown').replaceAll('_', ' ');
    state.className = `download-provider-state ${provider.status || 'unknown'}`;
  }
  if (expiry) expiry.textContent = formatExpiry(provider.expires_at);
  if (hostVerification) hostVerification.hidden = Boolean(provider.connected);
  if (disconnect) disconnect.hidden = !provider.connected;
}

async function refreshProviders(root, quiet = false) {
  if (!quiet) setStatus(root, 'Refreshing provider sessions…');
  const response = await fetch('/admin/api/downloader/providers', {
    credentials: 'same-origin',
    cache: 'no-store'
  });
  const payload = await responseJson(response);
  for (const provider of payload.providers || []) applyProviderState(root, provider);
  if (!quiet) setStatus(root, 'Provider status is current.', 'success');
}

async function disconnectProvider(root, provider) {
  if (!window.confirm(`Disconnect ${provider} from SpotiFLAC? Lossless downloads will skip it until reconnected.`))
    return;
  setStatus(root, `Disconnecting ${provider}…`, 'pending');
  const response = await fetch(`/admin/api/downloader/providers/${encodeURIComponent(provider)}`, {
    method: 'DELETE',
    credentials: 'same-origin',
    headers: { Accept: 'application/json' }
  });
  await responseJson(response);
  await refreshProviders(root, true);
  setStatus(root, `${provider} disconnected.`, 'success');
}

export function initDownloadManager(scope = document) {
  const root = scope.querySelector?.('#download-manager-root');
  if (!root || root.dataset.initialized === 'true') return;
  root.dataset.initialized = 'true';

  root.querySelector('#download-provider-refresh')?.addEventListener('click', () => {
    refreshProviders(root).catch((error) => setStatus(root, error.message, 'error'));
  });
  root.querySelectorAll('[data-provider-disconnect]').forEach((button) => {
    button.addEventListener('click', () => {
      disconnectProvider(root, button.dataset.providerDisconnect).catch((error) =>
        setStatus(root, error.message, 'error')
      );
    });
  });
}
