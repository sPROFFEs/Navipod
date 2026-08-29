# Party Rooms

Party Rooms let multiple authenticated Navipod users listen to a shared queue with synchronized playback.

## Discovering rooms

Open **Home → Party** to see available rooms. The room list can show information such as:

- room name;
- host;
- listener count and capacity;
- current track;
- playback state.

Rooms are currently discoverable by signed-in regular users. Private rooms, passwords and invitation links are not currently part of the documented behavior.

## Create a room

1. Open **Home → Party**.
2. Select **Create room**.
3. Enter a room name.
4. Choose a capacity from **2 to 15 listeners**.
5. Optionally choose one of your playlists to seed the initial queue.
6. Choose whether guests may search the local library and add songs.
7. Create and open the room.

Each user can own one room at a time. Delete the existing room before creating another.

## Join and listen

Select a room from the Party list or Home shelf.

While connected, Navipod temporarily switches the player from your personal queue to the room's shared track and server-owned playback clock. Playback events such as play/pause, seek, track changes, media controls and reconnects are synchronized around the room state.

If the browser blocks autoplay, use the on-screen **Tap to start listening** prompt.

When you leave, Navipod restores the personal playback context that was active before joining, including the personal track/queue and playback settings handled by the client.

## Host and guest permissions

| Action | Host | Guest |
|---|---:|---:|
| Play / pause | Yes | Follows host |
| Seek | Yes | Follows host |
| Previous / next | Yes | Follows host |
| Add local-library songs | Yes | Only when enabled by host |
| Remove songs | Yes | No |
| Delete room | Yes | No |

## Persistence and limits

- Rooms and queues survive application restarts.
- After a restart, room playback resumes in a paused state.
- Music stops when the last listener disconnects, while the empty room can remain available.
- A short disconnect grace period prevents a refresh or brief network interruption from immediately stopping the room.
- Multiple tabs from the same account count as one listener.
- A room queue supports up to **500 songs**.
- Party search is limited to music already stored in the shared Navipod library; it does not initiate remote searches/downloads.
- Party endpoints require an authenticated regular user; service accounts and anonymous requests are rejected.

## Good screenshot for the README

For the public README, capture an **active** room rather than an empty list. Show the room name, current track, listeners and a queue long enough to communicate the feature immediately.

See [Screenshot Guide](SCREENSHOTS.md).
