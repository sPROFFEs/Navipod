# Subsonic Clients

Each user's Navidrome instance is exposed through a user-specific path so compatible Subsonic clients can connect to it.

## Server URL

Use:

```text
https://your-domain/<username>
```

Use the same Navipod username and password for the connection.

## Compatible clients

The project specifically documents compatibility with clients such as:

- Amperfy;
- Tempo;
- Symfonium;
- other Subsonic-compatible clients.

If a client offers a choice between modern and legacy authentication and cannot connect with its default mode, enable the legacy authentication option documented by that client.

## Typical setup

1. Install your preferred Subsonic client.
2. Add a server.
3. Enter the Navipod user URL.
4. Enter the same credentials you use for Navipod.
5. Test the connection.

## HTTPS recommendation

Use an HTTPS deployment for mobile/remote access. The plain internal HTTP deployment is intended for trusted LAN/VPN use only.

## Related guides

- [Deployment](DEPLOYMENT.md)
- [Android](ANDROID.md)
- [Security](SECURITY.md)
