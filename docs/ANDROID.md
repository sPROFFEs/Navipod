# Android App

Navipod releases can include an Android APK that wraps the web application and integrates with Android media controls.

## Release artifacts

The project documents two Android artifacts alongside releases:

- `navipod-android.apk` — installable APK.
- `navipod-android-source.zip` — Android Studio project sources for the wrapper.

Release page:

```text
https://github.com/sPROFFEs/Navipod/releases
```

## Requirements

The documented APK target supports Android **5.0+**.

## Install

1. Download `navipod-android.apk` to the device.
2. If Android asks, allow **Install unknown apps** for the browser or file manager you used.
3. Open the APK and install it.
4. Launch Navipod.
5. Enter your Navipod server URL, for example:

   ```text
   https://navipod.example.com
   ```

6. Connect and sign in with the same credentials as the web application.

## Features

The documented wrapper provides:

- native system media notification;
- album artwork, title and artist in the notification;
- play / pause / next / previous media actions;
- background playback that continues when the screen is off or the app is not foregrounded;
- configurable server URL from the login screen.

## Changing server URL

Use the gear/settings control on the login screen to change the server URL later.

## README screenshot recommendation

Use a portrait capture that shows the actual Navipod player, not the Android launcher. Avoid notification content with personal names, private domains or sensitive album/library metadata you do not want public.

See [Screenshot Guide](SCREENSHOTS.md).
