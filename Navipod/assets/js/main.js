/**
 * main.js - Application Entry Point
 * Imports all modules and exposes functions to window
 */

// === MODULE IMPORTS ===
import * as state from './modules/state.js';
import * as ui from './modules/ui.js';
import * as api from './modules/api.js';
import * as player from './modules/player.js';
import * as queue from './modules/queue.js';
import * as search from './modules/search.js';
import * as radio from './modules/radio.js';
import * as favorites from './modules/favorites.js';
import * as playlists from './modules/playlists.js';
import * as downloads from './modules/downloads.js';
import * as views from './modules/views.js';
import * as admin from './modules/admin.js';

function initUserMenu() {
  const userMenu = document.getElementById('user-menu');
  if (!userMenu || userMenu.dataset.bound === 'true') return;
  userMenu.dataset.bound = 'true';

  document.addEventListener('click', (event) => {
    if (!userMenu.hasAttribute('open')) return;
    if (userMenu.contains(event.target)) return;
    userMenu.removeAttribute('open');
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (!userMenu.hasAttribute('open')) return;
    userMenu.removeAttribute('open');
  });
}

// === EXPOSE FUNCTIONS TO WINDOW FOR HTML ONCLICK HANDLERS ===

// State
window.toggleSidebar = state.toggleSidebar;

// UI
window.showToast = ui.showToast;
window.closeModal = ui.closeModal;
window.toggleFullscreenPlayer = ui.toggleFullscreenPlayer;
window.toggleMute = ui.toggleMute;
window.fmtTime = ui.fmtTime;
window.escHtml = ui.escHtml;

// Player
window.playTrack = player.playTrack;
window.playNext = player.playNext;
window.playPrev = player.playPrev;
window.playFromView = player.playFromView;

// Queue
window.addToQueue = queue.addToQueue;
window.addToQueueCurrent = queue.addToQueueCurrent;
window.removeFromQueue = queue.removeFromQueue;
window.toggleShuffle = queue.toggleShuffle;
window.toggleRepeat = queue.toggleRepeat;
window.toggleQueue = queue.toggleQueue;
window.renderQueue = queue.renderQueue;

// Search
window.handleSearch = search.handleSearch;
window.setSource = search.setSource;
window.executeSearch = search.executeSearch;
window.downloadUrl = search.downloadUrl;

// Radio
window.renderRadio = radio.renderRadio;
window.loadRadioPlaylists = radio.loadRadioPlaylists;
window.loadRadioPlaylist = radio.loadRadioPlaylist;
window.executeRadioSearch = radio.executeRadioSearch;
window.renderSavedRadios = radio.renderSavedRadios;
window.playRadioStream = radio.playRadioStream;
window.injectRadioToNavidrome = radio.injectRadioToNavidrome;
window.loadSidebarRadios = radio.loadSidebarRadios;
window.playSavedRadio = radio.playSavedRadio;
window.deleteSavedRadio = radio.deleteSavedRadio;
window.showDeleteRadioModal = radio.showDeleteRadioModal;

// Favorites
window.toggleFavorite = favorites.toggleFavorite;
window.toggleFavoriteCurrent = favorites.toggleFavoriteCurrent;
window.toggleFavoriteFromPlayer = favorites.toggleFavoriteFromPlayer;

// Playlists
window.showAddToPlaylistModal = playlists.showAddToPlaylistModal;
window.showCreatePlaylistModal = playlists.showCreatePlaylistModal;
window.showDeletePlaylistModal = playlists.showDeletePlaylistModal;
window.showEditPlaylistModal = playlists.showEditPlaylistModal;
window.createPlaylist = playlists.createPlaylist;
window.addToPlaylist = playlists.addToPlaylist;
window.removeFromPlaylist = playlists.removeFromPlaylist;
window.deletePlaylist = playlists.deletePlaylist;
window.editPlaylistName = playlists.editPlaylistName;
window.togglePlaylistPublic = playlists.togglePlaylistPublic;
window.copyPublicPlaylist = playlists.copyPublicPlaylist;
window.addToPlaylistCurrent = playlists.addToPlaylistCurrent;
window.showAddToPlaylistFromPlayer = playlists.showAddToPlaylistFromPlayer;
window.playPlaylistInOrder = playlists.playPlaylistInOrder;
window.playPlaylistShuffle = playlists.playPlaylistShuffle;
window.showRemoveFromPlaylistModal = playlists.showRemoveFromPlaylistModal;
window.openPlaylistCoverUpload = playlists.openPlaylistCoverUpload;
window.handlePlaylistCoverUpload = playlists.handlePlaylistCoverUpload;
window.showPlaylistCoverTrackModal = playlists.showPlaylistCoverTrackModal;
window.setPlaylistCoverFromTrack = playlists.setPlaylistCoverFromTrack;
window.resetPlaylistCover = playlists.resetPlaylistCover;

// Downloads
window.openDownloadsModal = downloads.openDownloadsModal;
window.closeDownloadsModal = downloads.closeDownloadsModal;
window.openDeleteResponsesModal = downloads.openDeleteResponsesModal;
window.closeDeleteResponsesModal = downloads.closeDeleteResponsesModal;
window.handleModalDownload = downloads.handleModalDownload;
window.triggerDownload = downloads.triggerDownload;
window.showDownloadConfirmModal = downloads.showDownloadConfirmModal;
window.executeDownload = downloads.executeDownload;

// Views
window.loadView = views.loadView;
window.createCard = views.createCard;
window.createMixCard = views.createMixCard;
window.createPlaylistCard = views.createPlaylistCard;
window.createTrackRow = views.createTrackRow;
window.handleCardClick = views.handleCardClick;
window.playPreview = views.playPreview;
window.renderSidebarPlaylists = views.renderSidebarPlaylists;
window.refreshRecentActivity = views.refreshRecentActivity;
window.loadUserData = views.loadUserData;
window.showSaveMixModal = views.showSaveMixModal;
window.discoveryTogglePreview = views.discoveryTogglePreview;
window.discoveryDismiss = views.discoveryDismiss;
window.discoveryDownload = views.discoveryDownload;
window.saveMixAsPlaylistAction = views.saveMixAsPlaylistAction;
window.saveWrappedTopSongsPlaylist = views.saveWrappedTopSongsPlaylist;
window.showTrackDeleteRequestModal = views.showTrackDeleteRequestModal;
window.submitTrackDeleteRequest = views.submitTrackDeleteRequest;
window.showTrackActionsSheet = views.showTrackActionsSheet;
window.closeTrackActionsSheet = views.closeTrackActionsSheet;

// Admin
window.toggleReset = admin.toggleReset;
window.adminAction = admin.adminAction;
window.handleAdminForm = admin.handleAdminForm;
window.deleteUser = admin.deleteUser;
window.createUser = admin.createUser;
window.resetPassword = admin.resetPassword;
window.adminSearchLibrary = admin.adminSearchLibrary;
window.adminFindDuplicates = admin.adminFindDuplicates;
window.showDeleteTrackModal = admin.showDeleteTrackModal;
window.adminDeleteTrack = admin.adminDeleteTrack;

// === YOUTUBE API CALLBACK ===
window.onYouTubeIframeAPIReady = () => {
  player.setupYouTubePlayer();
};

// === INITIALIZATION ===
document.addEventListener('DOMContentLoaded', async () => {
  console.log('[MAIN] Navipod ES6 Modules Initialized');

  initUserMenu();
  views.initSpaHistory();

  // YouTube IFrame API is loaded lazily on first preview to avoid an
  // unconditional cross-origin script on app boot. See player.playPreview.

  // Load user data (favorites, playlists)
  views.loadUserData();
  downloads.initDownloadHud();

  // Setup player controls
  player.setupPlayer();

  // Keep heartbeat quiet while backgrounded
  views.initHeartbeatLifecycle();

  const restoredSession = await player.restorePlaybackSession();

  // Load initial view
  // Load initial view only if we are on the root/portal path.
  // window.NAVIPOD_INITIAL_VIEW / NAVIPOD_INITIAL_PARAM are set by app_shell.html
  // (a non-blocking inline <script> that runs before this deferred module).
  // We are the *only* caller of loadView on page load — the template no longer
  // calls it a second time, eliminating the double-render (fix Q-10).
  const _serverView  = window.NAVIPOD_INITIAL_VIEW  ?? 'home';
  const _serverParam = window.NAVIPOD_INITIAL_PARAM ?? null;

  if (window.location.pathname === '/' || window.location.pathname === '/index.html') {
    if (restoredSession?.view && restoredSession.view !== 'home') {
      views.loadView(restoredSession.view, restoredSession.param ?? null, { replaceHistory: true });
    } else {
      views.loadView(_serverView, _serverParam, { replaceHistory: true });
    }
  } else if (window.location.pathname === '/portal') {
    if (restoredSession?.view && restoredSession.view !== 'home') {
      views.loadView(restoredSession.view, restoredSession.param ?? null, { replaceHistory: true });
    } else {
      views.loadView(_serverView, _serverParam, { replaceHistory: true });
    }
  } else {
    // If we are on a different page (e.g. /admin/system), we just set the view state without rendering
    // logic to highlight sidebar can be added here if needed
    console.log('[MAIN] Preserving server-rendered content for path:', window.location.pathname);
    if (window.location.pathname.includes('/admin/system')) state.setCurrentViewName('system_monitor');
  }

  // Initialize Lucide icons
  if (window.lucide) {
    lucide.createIcons();
  }

  // Register PWA service worker
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(err => {
      console.warn('[SW] Registration failed:', err);
    });
  }
});
