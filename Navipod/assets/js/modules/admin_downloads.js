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

async function stopAuthBrowser(root) {
  await responseJson(
    await fetch('/admin/api/downloader/auth-browser', { method: 'DELETE', credentials: 'same-origin' })
  );
  const modal = root.querySelector('#auth-browser-modal');
  if (modal) {
    modal.hidden = true;
    modal.setAttribute('aria-hidden', 'true');
  }
  const frame = root.querySelector('[data-auth-browser-frame]');
  if (frame) frame.src = 'about:blank';
  setStatus(root, 'Verification browser stopped.', 'success');
}

async function startAuthBrowser(root, provider) {
  setStatus(root, `Starting ${provider} verification browser…`, 'pending');
  const response = await fetch(`/admin/api/downloader/providers/${encodeURIComponent(provider)}/auth-browser/start`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { Accept: 'application/json' }
  });
  const payload = await responseJson(response);
  if (payload.status === 'connected') {
    await refreshProviders(root, true);
    setStatus(root, `${provider} is already connected.`, 'success');
    return;
  }
  const modal = root.querySelector('#auth-browser-modal');
  const frame = root.querySelector('[data-auth-browser-frame]');
  if (!modal || !frame || !payload.novnc_url) throw new Error('Verification browser URL was not returned');
  modal.dataset.provider = provider;
  modal.hidden = false;
  modal.setAttribute('aria-hidden', 'false');
  modal.querySelector('#auth-browser-title').textContent = `${provider} provider verification`;
  frame.src = payload.novnc_url;
  const remaining = payload.browser_session?.remaining_seconds || 600;
  const countdown = modal.querySelector('[data-auth-browser-countdown]');
  if (countdown)
    countdown.textContent = `Session expires in ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`;
  setStatus(root, 'Complete verification in the browser window.', 'success');
}

async function checkAuthBrowser(root) {
  const modal = root.querySelector('#auth-browser-modal');
  const provider = modal?.dataset.provider;
  if (!provider) return;
  setStatus(root, 'Checking provider verification…', 'pending');
  const payload = await responseJson(
    await fetch(`/admin/api/downloader/providers/${encodeURIComponent(provider)}/auth-browser/check`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' }
    })
  );
  if (payload.connected) {
    await stopAuthBrowser(root);
    await refreshProviders(root, true);
    setStatus(root, `${provider} verified successfully.`, 'success');
  } else setStatus(root, 'Verification is not complete yet.', 'pending');
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
  root.querySelectorAll('[data-provider-verify]').forEach((button) => {
    button.addEventListener('click', () =>
      startAuthBrowser(root, button.dataset.providerVerify).catch((error) => setStatus(root, error.message, 'error'))
    );
  });
  root
    .querySelector('[data-auth-browser-close]')
    ?.addEventListener('click', () => stopAuthBrowser(root).catch((error) => setStatus(root, error.message, 'error')));
  root
    .querySelector('[data-auth-browser-stop]')
    ?.addEventListener('click', () => stopAuthBrowser(root).catch((error) => setStatus(root, error.message, 'error')));
  root
    .querySelector('[data-auth-browser-check]')
    ?.addEventListener('click', () => checkAuthBrowser(root).catch((error) => setStatus(root, error.message, 'error')));
}
