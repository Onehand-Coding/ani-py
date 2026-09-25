# Android / Termux

ani-py launches Android players with normal `ACTION_VIEW` intents. It does not require package-manager preflight and does not require Shizuku.

## Setup

```sh
pkg update
pkg install python curl fzf termux-tools termux-am
```

Install VLC for Android or mpv-android from your preferred app source.

```sh
ani-py "frieren"                       # Android resolver/default player
ani-py -v "frieren"                    # VLC
ani-py --android-player mpv "frieren"  # mpv-android
```

Set a persistent preference with:

```sh
export ANI_PY_ANDROID_PLAYER=vlc   # auto | vlc | mpv
```

## Playback model

Protected provider streams can require HTTP headers that Android intents cannot attach. ani-py therefore starts a loopback-only relay on `127.0.0.1` with a random token path. The relay:

- injects the provider Referer and User-Agent upstream;
- rewrites HLS child playlists, media segments, and key URIs;
- forwards byte ranges needed for seeking;
- expires after an idle timeout;
- stays alive when you choose **Detach & exit**.

The normal launch order is direct Termux `am`, then the ordinary Android chooser when available, then an existing `rish`/Shizuku setup as an optional compatibility fallback. Shizuku is not a dependency.

## Subtitles

For WebVTT sources, the relay exposes the subtitle as a native HLS subtitle rendition. This path has been live-tested on the project owner's device with both VLC for Android and mpv-android.

VLC also receives its conventional `subtitles_location` string extra as a fallback. mpv-android's separate subtitle intent API uses a Parcelable URI array that shell `am` cannot construct, so the HLS rendition is the portable path for mpv-android.

On the tested VLC 3.7.1 setup, English tracks only auto-selected after adding this one-time VLC option:

```text
--sub-language=eng
```

VLC path: **Settings → Advanced → custom libVLC options**, then restart VLC. This is a player preference, not an ani-py requirement for every VLC version/device.

ani-py carries subtitle language/label metadata when the provider supplies it. It does not label an unknown subtitle language as English.

## Diagnostics

```sh
ani-py --android-debug -v -e 1 "frieren"
```

Debug mode reports the selected player, relay state, subtitle transport, sanitized intent result, and a relay log path. Signed provider URLs and relay tokens are not printed.

## Limitations

- `--skip` is unavailable for Android intent players because ani-skip returns desktop mpv options.
- Android external players are not controlled through ani-py's desktop mpv IPC socket.
- Player/ROM lifecycle behavior can affect resume position if Android destroys the player activity.
