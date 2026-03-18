/**
 * state.js - Global State & Constants
 * Centralized state management for the music player
 */

// === API CONSTANTS ===
export const API = "/api";

// === DOM REFERENCES ===
export const audio = document.getElementById('audio-player');

// === PLAYBACK STATE ===
export let currentTrack = null;
export let isPlaying = false;

// === SEARCH STATE ===
export let searchDebounce = null;
export let currentSource = 'all';

// === USER DATA ===
export let userFavorites = new Set();
export let userPlaylists = [];

// === QUEUE SYSTEM ===
export let userQueue = [];           // Explicitly queued by user (High Priority)
export let contextQueue = [];        // Background list (Playlist, Search, etc.)
export let contextIndex = -1;        // Index in contextQueue
export let currentViewList = [];     // Tracks currently visible in the view
export let isQueueOpen = false;
export let shuffleMode = false;
export let repeatMode = 'off';       // off, all, one
export let originalContextQueue = []; // Backup for un-shuffle

// === UI STATE ===
export let currentViewName = 'home'; // Track active view for UI logic
export let isSidebarOpen = false;
export let isFullscreenPlayerOpen = false;
export let lastPrevClickTime = 0;    // For double-click back button logic
export let isSeeking = false;        // Track if user is currently dragging progress bar

// === YOUTUBE STATE ===
export let ytPlayer = null;
export let ytReady = false;

// === HEARTBEAT SYNC ===
export let lastSyncVersion = null;
export let heartbeatInterval = null;

// === DOWNLOAD MANAGER ===
export let downloadPolling = null;

// === RADIO STATE ===
export const RADIO_HUBS = ["London", "Tokyo", "Berlin", "Huelva", "New York", "Paris", "Madrid", "Ibiza"];
export let currentRadioHub = RADIO_HUBS[Math.floor(Math.random() * RADIO_HUBS.length)];


// === STATE SETTERS ===
// (Needed because ES6 exports are read-only bindings)

export function setCurrentTrack(track) { currentTrack = track; }
export function setIsPlaying(val) { isPlaying = val; }
export function setSearchDebounce(val) { searchDebounce = val; }
export function setCurrentSource(val) { currentSource = val; }
export function setUserFavorites(val) { userFavorites = val; }
export function setUserPlaylists(val) { userPlaylists = val; }
export function setUserQueue(val) { userQueue = val; }
export function setContextQueue(val) { contextQueue = val; }
export function setContextIndex(val) { contextIndex = val; }
export function setCurrentViewList(val) { currentViewList = val; }
export function setIsQueueOpen(val) { isQueueOpen = val; }
export function setShuffleMode(val) { shuffleMode = val; }
export function setRepeatMode(val) { repeatMode = val; }
export function setOriginalContextQueue(val) { originalContextQueue = val; }
export function setCurrentViewName(val) { currentViewName = val; }
export function setIsSidebarOpen(val) { isSidebarOpen = val; }
export function setIsFullscreenPlayerOpen(val) { isFullscreenPlayerOpen = val; }
export function setLastPrevClickTime(val) { lastPrevClickTime = val; }
export function setYtPlayer(val) { ytPlayer = val; }
export function setYtReady(val) { ytReady = val; }
export function setLastSyncVersion(val) { lastSyncVersion = val; }
export function setHeartbeatInterval(val) { heartbeatInterval = val; }
export function setDownloadPolling(val) { downloadPolling = val; }
export function setCurrentRadioHub(val) { currentRadioHub = val; }
export function setIsSeeking(val) { isSeeking = val; }


// === SIDEBAR TOGGLE ===
export function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    isSidebarOpen = !isSidebarOpen;

    if (isSidebarOpen) {
        sidebar.classList.add('open');
        overlay.classList.add('active');
    } else {
        sidebar.classList.remove('open');
        overlay.classList.remove('active');
    }
}
