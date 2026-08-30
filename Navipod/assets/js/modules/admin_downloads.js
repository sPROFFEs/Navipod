const PROVIDER_ORIGIN = 'https://api.zarz.moe';
const FLOW_TIMEOUT_MS = 5 * 60 * 1000;

let activeFlow = null;
let flowTimer = null;
let popupPollTimer = null;

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
  const card = root.querySelector(`[data-provider-card="${CSS.escape(provider.provider)}"]`);
  if (!card) return;
  const state = card.querySelector('[data-provider-state]');
  const expiry = card.querySelector('[data-provider-expiry]');
  const connect = card.querySelector('[data-provider-connect]');
  const disconnect = card.querySelector('[data-provider-disconnect]');
  if (state) {
    state.textContent = String(provider.status || 'unknown').replaceAll('_', ' ');
    state.className = `download-provider-state ${provider.status || 'unknown'}`;
  }
  if (expiry) expiry.textContent = formatExpiry(provider.expires_at);
  if (connect) connect.hidden = Boolean(provider.connected);
  if (disconnect) disconnect.hidden = !provider.connected;
}

async function refreshProviders(root, quiet = false) {
  if (!quiet) setStatus(root, 'Refreshing provider sessions…');
  const response = await fetch('/admin/api/downloader/providers', {
    credentials: 'same-origin',
    cache: 'no-store',
  });
  const payload = await responseJson(response);
  for (const provider of payload.providers || []) applyProviderState(root, provider);
  if (!quiet) setStatus(root, 'Provider status is current.', 'success');
}

function clearActiveFlow() {
  if (flowTimer) window.clearTimeout(flowTimer);
  if (popupPollTimer) window.clearInterval(popupPollTimer);
  flowTimer = null;
  popupPollTimer = null;
  activeFlow = null;
}

async function startProviderConnection(root, provider) {
  if (activeFlow) throw new Error('Finish or close the current provider verification first.');
  const popup = window.open('about:blank', 'navipod-provider-auth', 'popup,width=520,height=760,resizable=yes,scrollbars=yes');
  if (!popup) throw new Error('The verification popup was blocked. Allow popups for Navipod and try again.');
  popup.document.title = 'Starting provider verification…';
  popup.document.body.textContent = 'Starting secure provider verification…';
  setStatus(root, `Starting ${provider} verification…`);

  try {
    const response = await fetch(`/admin/api/downloader/providers/${encodeURIComponent(provider)}/start`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    const payload = await responseJson(response);
    if (payload.status === 'connected') {
      popup.close();
      await refreshProviders(root, true);
      setStatus(root, `${payload.provider?.label || provider} is already connected.`, 'success');
      return;
    }
    if (!payload.verification_url || !payload.flow_token) throw new Error('Provider returned an incomplete verification flow.');
    const verificationUrl = new URL(payload.verification_url);
    if (
      verificationUrl.origin !== PROVIDER_ORIGIN ||
      verificationUrl.pathname !== '/v2/challenge' ||
      [...verificationUrl.searchParams.keys()].some((key) => key !== 'id') ||
      !/^chl_[A-Za-z0-9_-]{8,128}$/.test(verificationUrl.searchParams.get('id') || '')
    ) {
      throw new Error('Provider returned an untrusted verification address.');
    }
    activeFlow = { provider, flowToken: payload.flow_token, popup, completing: false };
    flowTimer = window.setTimeout(() => {
      if (activeFlow?.popup && !activeFlow.popup.closed) activeFlow.popup.close();
      clearActiveFlow();
      setStatus(root, 'Provider verification expired. Start the connection again.', 'error');
    }, FLOW_TIMEOUT_MS);
    popupPollTimer = window.setInterval(() => {
      if (!activeFlow || activeFlow.completing || !activeFlow.popup.closed) return;
      clearActiveFlow();
      setStatus(root, 'Provider verification was closed before it completed.', 'error');
    }, 750);
    popup.location.replace(verificationUrl.href);
    setStatus(root, `Complete ${payload.label || provider} verification in the popup.`, 'pending');
  } catch (error) {
    popup.close();
    clearActiveFlow();
    throw error;
  }
}

async function completeProviderConnection(root, grant) {
  const flow = activeFlow;
  if (!flow || flow.completing) return;
  flow.completing = true;
  setStatus(root, `Securing ${flow.provider} session…`, 'pending');
  try {
    const response = await fetch(`/admin/api/downloader/providers/${encodeURIComponent(flow.provider)}/complete`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ grant, flow_token: flow.flowToken }),
    });
    const payload = await responseJson(response);
    await refreshProviders(root, true);
    setStatus(root, `${payload.provider?.label || flow.provider} connected successfully.`, 'success');
  } finally {
    if (flow.popup && !flow.popup.closed) flow.popup.close();
    clearActiveFlow();
  }
}

async function disconnectProvider(root, provider) {
  if (!window.confirm(`Disconnect ${provider} from SpotiFLAC? Lossless downloads will skip it until reconnected.`)) return;
  setStatus(root, `Disconnecting ${provider}…`, 'pending');
  const response = await fetch(`/admin/api/downloader/providers/${encodeURIComponent(provider)}`, {
    method: 'DELETE',
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
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
  root.querySelectorAll('[data-provider-connect]').forEach((button) => {
    button.addEventListener('click', () => {
      startProviderConnection(root, button.dataset.providerConnect).catch((error) => setStatus(root, error.message, 'error'));
    });
  });
  root.querySelectorAll('[data-provider-disconnect]').forEach((button) => {
    button.addEventListener('click', () => {
      disconnectProvider(root, button.dataset.providerDisconnect).catch((error) => setStatus(root, error.message, 'error'));
    });
  });

  window.addEventListener('message', (event) => {
    if (
      event.origin !== PROVIDER_ORIGIN ||
      !activeFlow ||
      event.source !== activeFlow.popup ||
      event.data?.type !== 'zarz_grant'
    ) {
      return;
    }
    const grant = typeof event.data.grant === 'string' ? event.data.grant.trim() : '';
    if (grant.length < 16 || grant.length > 4096 || /\s/.test(grant)) {
      setStatus(root, 'Provider returned an invalid verification grant.', 'error');
      return;
    }
    completeProviderConnection(root, grant).catch((error) => setStatus(root, error.message, 'error'));
  });
}
