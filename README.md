# ani-py

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![Stdlib only](https://img.shields.io/badge/stdlib-only-green)
![License: MIT](https://img.shields.io/badge/license-MIT-yellow)

![ani-py terminal UI](docs/screenshots/ani-py-banner.png)

**ani-py** is a standalone Python CLI for searching, selecting, streaming, and downloading anime from the terminal. The Python runtime code uses only the standard library; external tools handle networking, menus, playback, and downloads.

It is an independent, AI-assisted implementation inspired by terminal anime launchers and is not affiliated with upstream `ani-cli`.

## Features

- Search and select anime from the terminal
- `fzf`, `rofi`, `dmenu`, or numbered fallback menus
- Single episodes or ranges such as `-e 3`, `-e 1-12`, `-e 0`, and `-e -1`
- Sub and dub modes
- Quality selection from available HLS variants
- Watch history and `--continue`
- mpv, VLC, IINA, and custom desktop players
- VLC for Android and mpv-android from Termux
- Android HLS relay for Referer-protected streams and subtitles
- Interactive next / previous / replay / episode / quality / subtitle controls
- Provider-independent `--auto-next` for desktop mpv, advancing only after natural EOF
- Private mpv IPC by default, with persistent desktop-mpv detach/reattach controls
- Downloads through `yt-dlp` with `ffmpeg` fallback
- Optional `ani-skip` integration for desktop mpv
- Provider-aware history and opt-in experimental provider adapters

## Install

### Linux / Unix

Install ani-py:

```sh
curl -fsSL https://github.com/Onehand-Coding/ani-py/releases/latest/download/install.sh | sh
```

Install recommended dependencies too when your package manager is supported:

```sh
curl -fsSL https://github.com/Onehand-Coding/ani-py/releases/latest/download/install.sh | sh -s -- --deps
```

The default Linux/Unix target is `~/.local/bin/ani-py`. Use `--prefix DIR` to choose another prefix.

Core requirements are Python 3.10+ and curl. `fzf` and mpv are recommended; VLC, rofi/dmenu, yt-dlp, ffmpeg, and ani-skip are optional.

### Termux / Android

The same installer detects Termux and installs to `$PREFIX/bin/ani-py`:

```sh
curl -fsSL https://github.com/Onehand-Coding/ani-py/releases/latest/download/install.sh | sh -s -- --deps
```

Install VLC for Android or mpv-android separately from your preferred Android app source. Shizuku/rish is optional and is **not** required.

### From a checkout

```sh
git clone https://github.com/Onehand-Coding/ani-py.git
cd ani-py
chmod +x ani-py
./ani-py "frieren"
```

Build the single-file distribution:

```sh
make build
./dist/ani-py -V
```

### Updating

ani-py installs a copy of itself, so an installed copy never changes on its
own. Published release assets include `SHA256SUMS`; the installer and
`--update` verify the standalone script before replacing an existing copy.
Update it with:

```sh
ani-py --update
```

This fetches the latest published release, verifies its SHA-256 digest, and
replaces the installed script in place. To also install external tools, re-run
the installer with `--deps`:

```sh
curl -fsSL https://github.com/Onehand-Coding/ani-py/releases/latest/download/install.sh | sh -s -- --deps
```

`--update` refuses to run when the latest published release is older than the
installed copy, so a local build ahead of the release is never silently rolled
back. Re-run `install.sh` if you want to force the latest published release.

The version is a calendar date, so `ani-py --version` reports something like
`ani-py 2026.10.4` rather than a semver string. That date is what `--update`
compares to decide whether an update would actually be a downgrade.

### Uninstall

```sh
curl -fsSL https://github.com/Onehand-Coding/ani-py/releases/latest/download/uninstall.sh | sh
```

The uninstaller removes ani-py only; it does not remove system packages or Android players.

## Usage

```sh
ani-py "frieren"
ani-py -q 1080 "dandadan"
ani-py -e 3 "one piece"
ani-py --dub -e 1-4 "bleach"
ani-py -c
ani-py -d -e 1-12 "pluto"
ani-py --skip "summer time rendering"
ani-py --auto-next -e 3 "frieren"
ani-py --sub-lang de -d -e 1 "one piece"
ani-py --attach
ani-py --update
ani-py --list-providers
```

Useful player/menu examples:

```sh
ani-py -p vlc "frieren"                    # VLC
ani-py --menu rofi "frieren"
ani-py --menu dmenu "frieren"
ani-py -p mpv "frieren"                    # mpv
```

Run `ani-py --help` for the complete current option list.

### Auto-next

`--auto-next` is controlled by ani-py rather than by a streaming provider.
ani-py keeps the episode list, waits for desktop mpv to report natural EOF over
its private IPC socket, then lazily resolves and starts the next episode. This
preserves quality/subtitle preferences, per-episode `ani-skip` behavior,
history updates, and normal provider failover.

With one selected episode, auto-next continues from that episode through the
currently loaded episode list. With an explicit multi-episode selection or
range, only that queue is played. Closing/stopping mpv or losing IPC stops the
queue; ani-py never treats a player exit as completed playback.

The feature currently requires desktop mpv on POSIX systems. VLC, IINA, custom
players, Windows named-pipe mpv control, and Android intent players do not
expose the same reliable completion signal through ani-py, so `--auto-next`
refuses those combinations instead of guessing.

Set `ANI_PY_AUTO_NEXT=1` to make the flag the environment default.

## Termux / Android

```sh
ani-py "frieren"             # Android resolver/default player
ani-py -p vlc "frieren"      # VLC for Android
ani-py -p mpv "frieren"      # mpv-android
```

For streams that require provider headers, ani-py uses a loopback-only relay on `127.0.0.1`. It keeps Referer/User-Agent handling inside Termux and rewrites HLS requests through the relay.

WebVTT subtitles are attached to the relayed HLS stream as a native subtitle rendition. This has been live-tested on the project owner's device with both VLC for Android and mpv-android.

On the tested VLC 3.7.1 setup, English subtitles only auto-selected after adding this one-time VLC option:

```text
--sub-language=eng
```

VLC: **Settings → Advanced → custom libVLC options**, then restart VLC. This is a VLC preference observed on the tested device, not a requirement asserted for every VLC version.

For Android architecture, diagnostics, relay behavior, and limitations, see [docs/android.md](docs/android.md).

## Real Android usage

### Termux workflow

<p align="center">
  <img src="docs/screenshots/android/termux-episode-picker.png" alt="Episode picker in Termux" width="44%">
  &nbsp;&nbsp;
  <img src="docs/screenshots/android/termux-playback-controller.png" alt="Playback controller in Termux" width="44%">
</p>

### Android playback

<p align="center">
  <img src="docs/screenshots/android/subtitles-rendered.png" alt="Android playback with rendered subtitles" width="48%">
  &nbsp;
  <img src="docs/screenshots/android/mpv-playback.png" alt="mpv-android playback" width="48%">
</p>

Recorded device flows are available for [VLC for Android](docs/clips/android-flow-vlc.mp4) and [mpv-android](docs/clips/android-flow-mpv.mp4). Additional screenshots and diagnostics are in [docs/screenshots/android](docs/screenshots/android).

## Providers

The default automatic provider chain is deliberately **HiAnime only**.

| Provider | Status | Default auto chain |
|---|---|---:|
| HiAnime | primary | yes |
| AniLight | experimental / opt-in | no |

Examples:

```sh
ani-py --provider hianime "frieren"
ani-py --provider anilight "frieren"
ani-py --provider-order hianime,anilight "frieren"
```

AniLight is registered as an experimental direct provider. The initial adapter
uses AniLight's current JSON API and its portable `ryu`/AnimeGG source path,
which returns quality-labelled progressive streams through AniLight's own proxy.
Sub and dub are supported where that source has coverage; the sub route is
hard-subbed. Broader soft-sub/MegaPlay backends are intentionally deferred until
their changing CDN/proxy behavior is proven against ani-py's desktop and Android
playback paths. AniLight is not part of the default automatic chain yet.

Kuhi and AnimeKai were removed in 0.5.2-rc11: their upstreams went away and
neither could return a stream, so keeping them only cost failover time.

Provider availability changes independently of ani-py's mocked tests. See [docs/providers.md](docs/providers.md) for current policy and live-check guidance.

## Playback controls

After a normal single-episode launch, ani-py offers:

- Next episode
- Previous episode
- Replay
- Choose episode
- Search another anime
- Change quality
- Change subtitle
- Detach & exit
- Stop & quit

Desktop mpv uses a private JSON IPC socket and replaces the active item in place when possible. Multi-episode ranges run sequentially. `Search another anime` keeps the current player running while you search and replaces playback only after you choose a new title and episode.

When a provider exposes multiple soft-subtitle tracks, `Change subtitle` can switch them from the controller. Desktop mpv switches live over IPC; other supported players may be relaunched with the selected subtitle. `--sub-lang de` (or another language/label) selects a track for playback and downloads, while `--sub-lang off` disables external subtitles.

For desktop mpv, `Detach & exit` stores enough session metadata to reconnect later. Run `ani-py --attach` to restore the same controller without restarting the video. Launching `ani-py` with no query also offers reattachment when a saved detached mpv session is still alive.

## Downloads and skipping

Download:

```sh
ani-py -d -e 1 "frieren"
ANI_PY_DOWNLOAD_DIR="$HOME/Videos/anime" ani-py -d -e 1-3 "frieren"
```

ani-py prefers yt-dlp and falls back to `ffmpeg -c copy`. Provider Referer/User-Agent headers are passed to the download backend. When a selected provider exposes soft subtitles, `--sub-lang <language-or-label>` downloads that subtitle beside the video (for example `Episode 1.de.vtt`) without burning it into the media file.

Intro/outro skipping is a desktop-mpv integration:

```sh
ani-py --skip "frieren"
```

ani-py calls `ani-skip -i <mal-id> -e <episode>` when the active provider exposes a MAL id. Android intent players, VLC, IINA, and generic custom players do not receive ani-skip mpv flags.

## Configuration

Common environment variables:

```text
ANI_PY_PLAYER
ANI_PY_PLAYER_FLAGS
ANI_PY_IPC_SOCKET
ANI_PY_MENU
ANI_PY_MENU_FLAGS
ANI_PY_DOWNLOAD_DIR
ANI_PY_SUB_LANG
ANI_PY_HIST_DIR
ANI_PY_CURL
ANI_PY_PROVIDER
ANI_PY_PROVIDER_ORDER
ANI_PY_ANILIGHT_URL
ANI_PY_ANILIGHT_API_URL
ANI_PY_MODE
ANI_PY_QUALITY
ANI_PY_SKIP_INTRO
ANI_PY_NO_DETACH
ANI_PY_EXIT_AFTER_PLAY
ANI_PY_ANDROID_DEBUG
NO_COLOR
```

Use `--player-flag='--flag'` and `--menu-flags='--flag'` when a passthrough value itself begins with a dash.

## Development

```sh
make test
make build
```

CI checks Python 3.10, 3.11, 3.12, and 3.13. Automated tests mock external players/downloads and use local HTTP fixtures; real-device/provider acceptance is tracked separately.

See [docs/development.md](docs/development.md) for the release checklist and test/build notes. If you want to contribute, start with [CONTRIBUTING.md](CONTRIBUTING.md).

## Troubleshooting

Check external tools:

```sh
./scripts/check-tools.sh
```

Android diagnostics:

```sh
ani-py --android-debug -p vlc -e 1 "frieren"
```

If mpv already uses `/tmp/mpvsocket`, no change is needed: ani-py uses a separate per-process IPC socket by default.

Provider failures should be checked against the specific provider and network; an offline parser test does not prove a third-party endpoint is live.

## Legal

Use ani-py in accordance with applicable law, provider terms, and your network environment.

## License

MIT
