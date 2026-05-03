/**
 * audio_engine.js - Optional Web Audio chain for ReplayGain & crossfade
 *
 * Why a separate module?
 *   The base playback path uses the raw <audio> element directly so it
 *   works on every browser, every codec, with MediaSession + Range
 *   support for Android background playback. We do NOT want to put
 *   that at risk for an optional volume-tweak feature.
 *
 *   This module hooks the Web Audio graph IN FRONT of the audio
 *   element ONLY when the user opts in. If anything fails (browser
 *   quirks, autoplay-policy oddities, etc.) the chain is silently
 *   discarded and playback continues unmodified.
 *
 * Chain
 *   <audio> ─► MediaElementSource ─► gainReplay ─► gainFade ─► destination
 *
 *   gainReplay  — set per-track from /api/track/{id}/gain
 *   gainFade    — driven by player.js to fade in/out across track
 *                  boundaries when the user enables crossfade.
 *
 * Settings (localStorage)
 *   navipod.replaygain.enabled  — boolean, default false
 *   navipod.crossfade.seconds   — integer 0..12, default 0 (off)
 */

import * as state from './state.js';

const RG_KEY = 'navipod.replaygain.enabled';
const XF_KEY = 'navipod.crossfade.seconds';

let _ctx = null;
let _source = null;
let _gainReplay = null;
let _gainFade = null;
let _initialized = false;
let _initFailed = false;

// iOS detection (covers iPhone, iPad in legacy mode, and iPadOS which
// disguises itself as MacIntel with touch points). Browser-only check;
// no UA spoofing concerns because we degrade gracefully either way.
//
// On iOS, calling `createMediaElementSource(audio)` permanently routes
// the audio element's output through the Web Audio graph. When iOS
// suspends the AudioContext (which it does aggressively on background,
// lock screen, app-switcher) the graph mutes — and even though the
// <audio> element keeps trying to play, no sound comes out. There's no
// reliable way to keep the AudioContext alive across iOS background
// the way Android does. The least-bad option is to NOT initialize the
// chain on iOS and accept that ReplayGain + Crossfade won't work
// there. Background playback (the bigger feature) keeps working.
const _IS_IOS = (() => {
  if (typeof navigator === 'undefined') return false;
  const ua = navigator.userAgent || '';
  if (/iPad|iPhone|iPod/.test(ua)) return true;
  // iPadOS 13+ ships a desktop-class user agent — sniff via touch points.
  if (navigator.platform === 'MacIntel' && (navigator.maxTouchPoints || 0) > 1) return true;
  return false;
})();

// Pre-amp keeps tracks from clipping after positive gain. Spotify uses
// -1 dB; we follow suit so a fully-tagged library tagged at -14 LUFS
// peaks comfortably below 0 dBFS.
const PREAMP_DB = -1;

function dbToLinear(db) {
  return Math.pow(10, db / 20);
}

export function isReplayGainEnabled() {
  try { return localStorage.getItem(RG_KEY) === '1'; } catch { return false; }
}

export function setReplayGainEnabled(on) {
  try { localStorage.setItem(RG_KEY, on ? '1' : '0'); } catch {}
  if (on) ensureInitialized();
}

export function getCrossfadeSeconds() {
  try {
    const n = parseInt(localStorage.getItem(XF_KEY) || '0', 10);
    return Math.max(0, Math.min(12, n));
  } catch { return 0; }
}

export function setCrossfadeSeconds(seconds) {
  const n = Math.max(0, Math.min(12, parseInt(seconds, 10) || 0));
  try { localStorage.setItem(XF_KEY, String(n)); } catch {}
  if (n > 0) ensureInitialized();
}

// === INIT ==================================================================

export function ensureInitialized() {
  if (_initialized || _initFailed) return _initialized;
  if (!state.audio) return false;
  // Hard skip on iOS — see the _IS_IOS comment above. We mark as
  // failed so subsequent calls short-circuit cheaply.
  if (_IS_IOS) {
    _initFailed = true;
    _notifyInitFailure(
      'ReplayGain / Crossfade are disabled on iOS — they would break background playback. ' +
      'Regular playback continues unaffected.'
    );
    return false;
  }
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) {
      _initFailed = true;
      _notifyInitFailure('No Web Audio support in this browser.');
      return false;
    }
    _ctx = new Ctx();
    // createMediaElementSource is one-shot per element. If the audio
    // element has been replaced (some flows do `state.setAudio(...)`)
    // this throws "HTMLMediaElement already connected". The catch
    // surfaces a clear toast instead of silently disabling the
    // feature, so the user knows their toggle didn't take effect.
    _source = _ctx.createMediaElementSource(state.audio);
    _gainReplay = _ctx.createGain();
    _gainFade = _ctx.createGain();
    _gainReplay.gain.value = 1.0;
    _gainFade.gain.value = 1.0;
    _source.connect(_gainReplay);
    _gainReplay.connect(_gainFade);
    _gainFade.connect(_ctx.destination);
    _initialized = true;
    return true;
  } catch (e) {
    console.warn('[AUDIO_ENGINE] Init failed, falling back to direct playback', e);
    _initFailed = true;
    _notifyInitFailure(
      'ReplayGain / Crossfade not available — this browser refused the audio routing setup. Playback continues unaffected.'
    );
    return false;
  }
}

// Show the user a toast ONCE when init fails so they understand why
// the playback toggles they enabled aren't taking effect. Subsequent
// calls are no-ops.
let _initFailureNotified = false;
function _notifyInitFailure(msg) {
  if (_initFailureNotified) return;
  _initFailureNotified = true;
  if (typeof window.showToast === 'function') {
    try { window.showToast(msg, 'error'); } catch {}
  }
}

// Some browsers suspend the AudioContext until a user gesture — call
// this from a click handler before applying gain.
export async function resumeIfSuspended() {
  if (_ctx && _ctx.state === 'suspended') {
    try { await _ctx.resume(); } catch {}
  }
}

// === REPLAYGAIN ============================================================

export async function applyReplayGain(trackId) {
  if (!isReplayGainEnabled()) {
    if (_gainReplay) _gainReplay.gain.value = 1.0;
    return;
  }
  if (!ensureInitialized()) return;
  if (!trackId) return;

  try {
    const res = await fetch(`${state.API}/track/${trackId}/gain`);
    if (!res.ok) return;
    const data = await res.json();
    const gainDb = (Number(data.gain_db) || 0) + PREAMP_DB;
    const peak = Number(data.peak) || 1.0;

    // Clip protection: if applying the requested gain would push the
    // peak above ~0.99 we shave the gain down so the loudest sample
    // sits just under digital max. This is the standard ReplayGain
    // algorithm.
    let linear = dbToLinear(gainDb);
    if (peak * linear > 0.99) {
      linear = 0.99 / peak;
    }

    // Smooth ramp so the gain change isn't audible as a click when a
    // track is already mid-playback. Re-check `_ctx` because Safari
    // can close it on visibility change between the fetch await and
    // here.
    if (!_ctx || !_gainReplay) return;
    const now = _ctx.currentTime;
    _gainReplay.gain.cancelScheduledValues(now);
    _gainReplay.gain.linearRampToValueAtTime(linear, now + 0.05);
  } catch (e) {
    console.warn('[AUDIO_ENGINE] ReplayGain fetch failed', e);
  }
}

// === CROSSFADE / FADE ENVELOPES ============================================
//
// Real crossfade requires a second decoder. That's a future change —
// for now this implements a *graceful* fade-out at the end of a track
// and a fade-in on the next track using the existing audio element.
// It's not a true overlap, but it eliminates the harsh hard-cut between
// tracks that bothers users on continuous-mix albums.
//
// player.js drives this by calling fadeOut() in the `ended`/near-end
// handler and fadeIn() at the start of the next track's playback.

export function fadeIn(seconds = null) {
  if (!ensureInitialized() || !_ctx || !_gainFade) return;
  const dur = seconds ?? getCrossfadeSeconds();
  if (dur <= 0) {
    _gainFade.gain.value = 1.0;
    return;
  }
  const now = _ctx.currentTime;
  _gainFade.gain.cancelScheduledValues(now);
  _gainFade.gain.setValueAtTime(0.0001, now);
  _gainFade.gain.exponentialRampToValueAtTime(1.0, now + dur);
}

export function fadeOut(seconds = null) {
  if (!ensureInitialized() || !_ctx || !_gainFade) return;
  const dur = seconds ?? getCrossfadeSeconds();
  if (dur <= 0) return;
  const now = _ctx.currentTime;
  _gainFade.gain.cancelScheduledValues(now);
  // exponentialRampToValueAtTime won't accept exactly 0, so use a
  // tiny non-zero target and follow up with setValueAtTime to fully
  // mute at the end of the curve.
  _gainFade.gain.setValueAtTime(_gainFade.gain.value || 1.0, now);
  _gainFade.gain.exponentialRampToValueAtTime(0.0001, now + dur);
  _gainFade.gain.setValueAtTime(0.0001, now + dur + 0.01);
}

// Reset envelopes to neutral. Called by player.js when seeking or
// switching to a non-faded path so we never leave the gain stuck low.
export function resetFade() {
  // Both nullable: _ctx (context creation failed or was closed) and
  // _gainFade (chain never built). Without these guards a player.js
  // call after init failure would throw and bubble to the user.
  if (!_ctx || !_gainFade) return;
  const now = _ctx.currentTime;
  _gainFade.gain.cancelScheduledValues(now);
  _gainFade.gain.setValueAtTime(1.0, now);
}
