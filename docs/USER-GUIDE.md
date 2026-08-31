# User Guide

The main application is available under `/portal` after authentication. This guide covers the web app; see [Android](ANDROID.md) and [Subsonic](SUBSONIC.md) for other clients.

## Find your way around

The top search field searches music. The Home filters provide focused views:

- **All** — recent playlists, personal mixes, recommendations and other shortcuts;
- **Party** — available Party Rooms and the room creation flow;
- **Public** — playlists shared by other users on this server;
- **Discover** — remote recommendations that are not already in the local library;
- **Radios** — live radio discovery and saved stations.

The side rail gives quick access to favorites and playlists. The player remains available at the bottom while you browse.

## Home and personal mixes

Home adapts to the library and listening activity available to your account. Its local-library mixes include:

- **Repeat** — tracks you return to most often;
- **Deep Cuts** — familiar music outside your most obvious repeats;
- **Favorites** — liked tracks and related music already in the library;
- **Rediscovery** — liked tracks that have not been played much recently.

Open a mix to play it or save its current contents as a normal playlist. Results evolve as listening history, favorites and the shared library change.

## Discover

The **Discover** tab shows remote recommendations that are not already owned by the server. From a recommendation you can:

- preview it when a preview is available;
- download it into the shared pool;
- dismiss it from your feed.

Dismissed recommendations are remembered by that browser. Downloaded music becomes available after the download and import job completes.

## Search and download music

Search can combine results from:

- the local Navipod/Navidrome library;
- Spotify;
- YouTube;
- Last.fm;
- MusicBrainz;
- connected Navipod federation peers, when enabled by an administrator.

Use the source filters to narrow the results. Local tracks can be played immediately. Remote tracks can be previewed or downloaded when the source supports it.

You can also open the global **Downloads** panel and paste a supported Spotify, YouTube, SoundCloud, Audius or Jamendo URL. Jobs show their current state, resolved source and downloader details. Failed jobs can be retried from the same panel.

A typical job moves through these stages:

```text
queued → processing → completed
```

A job can move to **Failed** from any active stage; its details explain the error and whether a fallback was attempted.

Navipod reuses matching content already in the shared pool when possible, so two users do not need separate copies of the same track.

## Browse the library

**Your Library** provides searchable, paginated views for playlists, artists, albums and genres. Open an item to play its tracks or use the available track actions. Favorites remain available from the heart shortcut in the side rail.

## Normal playlists

Create a playlist from the Library view or save one from a mix. Playlist owners can:

- rename or delete the playlist;
- add, remove and drag tracks into a new order;
- upload a cover or choose a cover already present in the playlist;
- switch the playlist between private and public.

The **Public** Home tab lists playlists shared by users on the same server. You can save a read-only local copy of another user's playlist and sync that copy when its source changes. Synced copies cannot be edited or published as if they were the original; create a separate playlist if you want an independent version.

## Smart playlists

Smart playlists are rule-based views of the current library rather than fixed track lists. When creating or editing one, you can combine:

- artist, album and genre filters;
- minimum and maximum release year;
- tracks added within a number of days;
- a minimum play count;
- tracks not played for a number of days;
- favorites only;
- result limit and sort order.

Use **Preview** before saving to check the match count and rule summary. Use **Refresh** on an existing smart playlist to recalculate it immediately. Relevant library and favorite changes can also refresh its contents automatically.

## Player controls

The compact player and full-screen player provide:

- play/pause, previous and next;
- seek, mute and volume;
- shuffle and repeat;
- favorite and add-to-playlist actions;
- lyrics, queue and sleep timer controls.

Repeat cycles through off, queue repeat and repeat-current-track. The sleep timer cycles through off, 15, 30 and 60 minutes and pauses playback when it expires.

### Queue and playback state

Open the queue to drag tracks into a new order or remove them. Manually queued tracks play before the remaining album, playlist or other playback context.

Navipod saves the personal queue, current context, position, shuffle/repeat state and volume for your account. It can restore that state in another session or browser, but this is not live cross-device control: if multiple devices play at once, the latest saved state wins. Party Room playback is kept separate from personal playback state.

### Lyrics

Open the lyrics panel from the player. When synchronized lyrics are available, the current line follows playback; plain lyrics are shown otherwise. Lyrics are retrieved through the Navipod backend and cached for later use.

### Per-device playback preferences

In **Settings → Playback**, you can enable volume normalization and choose a fade duration between tracks. These preferences are stored in the current browser and do not automatically follow your account to another device.

## Favorites and smart radio

Use the heart action to maintain your favorites. Favorites influence personal mixes and can be used as a smart-playlist rule.

Artist and track menus can start a smart radio using related music already available in the local library. Results improve as the library grows.

## Live radio

Open **Home → Radios** to browse editorial stations or search by station or city. You can play a live station immediately, save it to your Navidrome library, and remove saved stations later.

## Wrapped

When an administrator enables a Wrapped period, its card appears on Home. Wrapped summarizes listening time, top tracks and artists, listening patterns and Party Room comparisons. You can also save the displayed top tracks as a playlist.

Wrapped depends on completed listening events, so current playback may not appear until the track ends or is skipped.

## Party Rooms

Party Rooms let multiple Navipod users listen to one synchronized queue. The room host controls playback, while guests follow the shared room state. A room may optionally allow guests to add songs.

Read [Party Rooms](PARTY-ROOMS.md) for creation limits, permissions and synchronization behavior.

## Request a track deletion

Use the flag/delete-request action on a local track when it should be removed from the shared library. Enter a reason for the administrator; duplicate pending requests for the same track are prevented.

The top-bar response indicator shows approvals, rejections and any administrator response. A request does not remove a file until an administrator approves it.

## Account and provider settings

Settings let you:

- upload an avatar;
- change your password;
- configure optional Spotify and Last.fm metadata credentials;
- set metadata-provider priority;
- upload YouTube cookies for content that requires an authenticated browser session;
- configure per-device playback preferences;
- sign out.

Treat provider secrets and YouTube cookies as credentials. Do not share them or commit them to the repository.

If **Remember me** is selected at login, Navipod stores a signed, HTTP-only session cookie rather than saving the password in the browser. The session duration is controlled by the server administrator.

## Mobile listening

You have two main options:

- install the Navipod Android APK — see [Android](ANDROID.md);
- connect a compatible Subsonic client to your user endpoint — see [Subsonic](SUBSONIC.md).

## Need help?

See [Troubleshooting](TROUBLESHOOTING.md).
