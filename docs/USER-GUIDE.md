# User Guide

The main application shell is available under `/portal` after authentication.

## Home

Home is the discovery and listening dashboard. Depending on your library and configured providers, it surfaces:

- recommendations;
- personal mixes;
- Wrapped-style listening views;
- navigation into Party Rooms;
- shortcuts into your music library and favorites.

## Personal mixes

Navipod includes local-library mixes such as:

- **Repeat** — music you repeatedly return to;
- **Deep Cuts** — tracks deeper in your library;
- **Favorites** — music built around your starred/favorite behavior;
- **Rediscovery** — tracks worth surfacing again.

Mix availability and results depend on the music and activity available to the server.

## Search

Search can combine your local library with remote discovery providers:

- local Navipod / Navidrome library;
- YouTube;
- Spotify;
- Last.fm;
- MusicBrainz.

Remote results can be used for discovery and, where supported by the application, preview/download flows. Finished downloads enter the shared pool so duplicate content can be reused rather than downloaded independently for each user.

Download jobs progress through states such as:

```text
pending → downloading → importing → completed
```

Navipod can reuse duplicates detected by source, hash or fingerprint.

## Library

Library views provide searchable, paginated browsing across areas such as:

- artists;
- albums;
- genres;
- album-artist groupings;
- favorites;
- playlists and smart playlists.

## Smart playlists

Smart playlists support editable rules, previews and automatic refresh behavior after relevant library or favorite changes.

## Favorites and radios

Use favorites to keep important tracks, albums or artists close at hand. Radios and recommendation surfaces complement your local library with discovery behavior supported by the configured providers.

## Party Rooms

Party Rooms let multiple Navipod users listen to one synchronized queue. The room host controls playback, while guests follow the shared room state.

Read [Party Rooms](PARTY-ROOMS.md) for permissions, limits and synchronization behavior.

## Settings

Regular users can access engine/provider-related settings exposed to them by the application. Administrators additionally have operational areas for users, updates, backups, monitoring and library maintenance.

## Mobile listening

You have two main options:

- install the Navipod Android APK — see [Android](ANDROID.md);
- connect a compatible Subsonic client to your user endpoint — see [Subsonic](SUBSONIC.md).

## Need help?

See [Troubleshooting](TROUBLESHOOTING.md).
