# ani-py

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![Stdlib only](https://img.shields.io/badge/stdlib-only-green)
![License: MIT](https://img.shields.io/badge/license-MIT-yellow)
![Tools: curl/mpv/fzf/yt-dlp](https://img.shields.io/badge/tools-curl%20%7C%20mpv%20%7C%20fzf%20%7C%20yt--dlp-333)

![ani-py terminal UI](docs/screenshots/ani-py-banner.png)

> The image above recreates the current default **fzf** flow: search, episode selection, and playback controls. Exact colors/fonts depend on your terminal theme. `rofi`, `dmenu`, and the numbered fallback intentionally look different.

**ani-py** is a polished, standalone Python anime CLI for searching, selecting, streaming, and downloading anime from the terminal.

The design is intentionally small:

- **one self-contained Python implementation**
- **no pip/runtime Python package dependencies**
- external CLI tools only where they are useful
- a testable repo layout for development

ani-py keeps its application logic in the Python standard library while delegating networking, menu presentation, playback, and downloads to tools such as `curl`, `fzf`, `mpv`, `vlc`, `yt-dlp`, `ffmpeg`, and `ani-skip`.

> This project is an independent Python implementation inspired by terminal anime launchers. It is not affiliated with upstream `ani-cli`.

---

## Why this repo exists

Most upstream repos that do the same thing don't want AI-generated code in theirs, and I didn't want to bother them. I'm also dumb and lazy - I don't want to code this manually. So I let AI build it for me, and now I have my own copy I can modify to my heart's content.

Beyond that, this repo exists to:

- track changes cleanly in Git
- run repeatable tests locally and in CI
- keep the standalone script while still having a proper repository layout
- make provider/parser regressions easier to catch

---

## Features

- Search anime from the terminal
- Interactive anime and episode pickers
- Menu frontends: `fzf`, `rofi`, `dmenu`, or a built-in numbered fallback
- Episode/range selection such as `-e 3`, `-e 1-12`, `-e 0`, and `-e -1`
- Sequential range playback instead of spawning overlapping players
- Sub and dub modes
- Quality selection: `best`, `worst`, `360`, `480`, `720`, `1080`
- Watch history with `--continue`
- Download mode via `yt-dlp`, with `ffmpeg` fallback
- Optional intro/outro skipping through `ani-skip` + mpv
- Interactive playback controller: next / previous / replay / choose episode / change quality
- Private mpv IPC by default, so ani-py does **not** hijack a pre-existing `/tmp/mpvsocket`
- Multi-provider architecture with automatic failover
- HiAnime primary source + Kuhi multi-source automatic backup
- AnimeKai retained as a manual experimental adapter, not part of the default failover chain
- Conservative title matching: ambiguous fallback matches are shown to you instead of silently guessed
- Provider-aware history with automatic migration from the old HiAnime-only history format
- Stdlib-only Python application logic

## Providers and automatic failover

Default chain is **HiAnime only** — the public Kuhi instance is currently
undeployed and AnimeKai has no trusted domain. If HiAnime fails mid-run,
ani-py can map your title onto a backup and continue there: exact matches
resolve automatically, ambiguous ones ask you instead of guessing.

```bash
./ani-py "frieren"                                  # default chain
./ani-py --provider hianime|kuhi|animekai "frieren" # force one
./ani-py --provider-order kuhi,hianime "frieren"    # reprioritize
./ani-py --list-providers                           # show configured
```

Backups are gated, never trusted blindly: Kuhi must pass a live media
preflight (a real extraction returning a direct playable stream) before it
joins failover, and AnimeKai needs an explicitly configured mirror:

```bash
export ANI_PY_PROVIDER_ORDER=hianime,kuhi             # persist opt-in
export ANI_PY_KUHI_URL=https://your-instance.example
ANI_PY_ANIMEKAI_URL=https://your-mirror ./ani-py --provider animekai "frieren"
```

Probe a Kuhi instance from your own network with `./scripts/check-providers-live.sh`.

## Requirements

### Required

- Python 3.10+
- `curl` or a compatible curl-impersonate binary

### Recommended

- `fzf` for the default terminal UI
- `mpv` for the best playback/control integration

### Optional

- `rofi` / `dmenu`
- `vlc`
- `yt-dlp`
- `ffmpeg`
- `ani-skip`

Check your machine with:

```bash
./scripts/check-tools.sh
```

## Quick start

```bash
git clone <your-repo-url>
cd ani-py
chmod +x ani-py
./ani-py "frieren"
```

The repo wrapper imports `ani_py.py`. If you want a single distributable file:

```bash
make build
./dist/ani-py "frieren"
```

## Usage

```bash
./ani-py "frieren"
./ani-py -q 1080 "dandadan"
./ani-py -e 3 "one piece"
./ani-py --dub -e 1-4 "bleach"
./ani-py -c
./ani-py -d -e 1-12 "pluto"
./ani-py --skip "summer time rendering"
./ani-py --provider animekai "naruto"
./ani-py --provider-order animekai,hianime "one piece"
./ani-py -S 2 "frieren"          # pick the 2nd search result automatically
./ani-py -D                      # clear watch history
```

### Options

```text
usage: ani-py [-h] [-c] [-d] [-D] [-e EPISODE] [-q QUALITY] [-S SELECT_NTH]
              [--provider {auto,hianime,kuhi,animekai}]
              [--provider-order PROVIDER_ORDER] [--list-providers] [--dub]
              [--sub] [-v] [--player PLAYER] [--player-flag PLAYER_FLAG]
              [--ipc-socket IPC_SOCKET] [--menu {fzf,rofi,dmenu}]
              [--menu-flags MENU_FLAGS] [--skip] [--no-detach]
              [--exit-after-play] [-V]
              [query ...]

  query                 anime search query
  -c, --continue        continue from history
  -d, --download        download instead of play
  -D, --delete-history  clear watch history
  -e, --episode SPEC    episode or range, e.g. 4, 4-9, 4:9, 4..9, 0 (first), -1 (last)
  -q, --quality Q       best, worst, 360, 480, 720, 1080 (default: best)
  -S, --select-nth N    select search result by index
  --provider P          source provider: auto, hianime, kuhi, animekai (default: auto)
  --provider-order O    comma-separated auto-failover order (default: hianime; kuhi/animekai are experimental opt-in)
  --list-providers      show configured providers and exit
  --dub                 use dubbed stream
  --sub                 use subtitled stream (default)
  -v, --vlc             use VLC
  --player PLAYER       custom player executable
  --player-flag FLAG    extra player argument (repeatable; use --player-flag='--flag' for dash-flags)
  --ipc-socket PATH     mpv IPC socket path (default: private per ani-py process)
  --menu MENU           interactive menu frontend: fzf, rofi, dmenu
  --menu-flags FLAGS    extra menu frontend flags (use --menu-flags='--flag' for dash-flags)
  --skip                use ani-skip with mpv
  --no-detach           keep player attached
  --exit-after-play     exit after player closes/launches
  -V, --version         show version and exit
  -h, --help            show help and exit
```

Episode ranges accept `-`, `:`, or `..` as separators (`1-12`, `1:12`, `1..12`). `0` means first episode, `-1` means last.

### Menus

The default frontend is `fzf` when available:

```bash
./ani-py --menu fzf "frieren"
```

Alternative frontends:

```bash
./ani-py --menu rofi "frieren"
./ani-py --menu dmenu "frieren"
```

If the selected frontend is unavailable, ani-py falls back to a numbered terminal selector.

## Termux / Android

ani-py treats Android playback as **intent dispatch**, not as a desktop-player lookup. It deliberately does **not** call `pm path` to decide whether VLC/mpv is installed; package-manager calls from an ordinary Termux UID are unreliable on modern Android and are unnecessary for launching a `VIEW` intent.

Recommended setup:

```bash
pkg update
pkg install python curl fzf termux-tools termux-am
```

Install any normal Android video player. VLC and mpv-android are explicitly supported. Then run ani-py normally:

```bash
ani-py "frieren"
```

The default Android mode is `auto`: Android's normal media resolver/default-player mechanism is used, so other capable players can work too. Pin a player when desired:

```bash
ani-py --android-player vlc "frieren"
ani-py --android-player mpv "frieren"
ani-py -v "frieren"                 # VLC shortcut
```

Playback launch order is intentionally low-friction:

1. Try the normal Termux `am`/TermuxAm `ACTION_VIEW` path.
2. If normal targeting cannot launch, use Termux's `termux-open` chooser when available.
3. If you already have `rish` + Shizuku configured, ani-py can retry the same intent through `rish`. **Shizuku is never required.**

For protected streams, ani-py starts a small stdlib-only relay bound to `127.0.0.1`. The Android player receives the local URL while the relay injects the provider `Referer` and User-Agent upstream and rewrites nested HLS playlists/segments through itself. This avoids the old Android-VLC problem where an intent could launch VLC but could not attach the stream's HTTP referrer. The relay is detached and expires after an idle period, so **Detach & exit** does not immediately break playback.

Subtitles ride the relayed HLS playlist as a native subtitle rendition, so they load automatically in both players. mpv-android selects the track on its own; VLC needs one one-time setting before it auto-selects: **Settings → Advanced → custom libVLC options**, add `--sub-language=eng`, then restart VLC. (VLC loads the track without this, but leaves it deselected; subtitle color/size live on the same VLC settings screen.) A staged copy of the subtitle plus the conventional `subtitles_location` string extra remain as fallback. `--skip` remains unavailable for Android intent players because `ani-skip` returns desktop mpv command-line/script options that cannot be injected through the Android intent API.

Environment default:

```bash
export ANI_PY_ANDROID_PLAYER=vlc   # auto | vlc | mpv
```

### Android in pictures

| Termux picker | VLC playback |
|---|---|
| ![episode picker in Termux](docs/screenshots/android/termux-episode-picker.png) | ![One Piece playing in VLC for Android](docs/screenshots/android/vlc-playback.png) |
| VLC subtitle track | mpv-android playback |
|---|---|
| ![English subtitle track listed in VLC](docs/screenshots/android/vlc-subtitle-track.png) | ![One Piece with subtitles in mpv-android](docs/screenshots/android/mpv-playback.png) |

More shots (playback controller, diagnostics, fullscreen frames) live in `docs/screenshots/android/`.

### Android flows on video

Full search → watch runs, recorded on-device (~1 min each):

| VLC flow (`android_auto`) | mpv flow (`--android-player mpv`) |
|---|---|
| <video src="docs/clips/android-flow-vlc.mp4" poster="docs/clips/android-flow-vlc-poster.png" width="270" controls preload="none"></video> | <video src="docs/clips/android-flow-mpv.mp4" poster="docs/clips/android-flow-mpv-poster.png" width="270" controls preload="none"></video> |

## Playback controls

After a normal single-episode launch, ani-py exposes a compact controller:

- **Next episode**
- **Previous episode**
- **Replay**
- **Choose episode**
- **Change quality**
- **Detach & exit**
- **Stop & quit**

With mpv, ani-py uses a private JSON IPC socket and replaces the active item in-place when possible. This keeps **Next episode** from launching a second overlapping mpv instance.

For a multi-episode/range selection, ani-py treats the selection as a queue. Each episode finishes before the next one starts.

## `--skip`

`--skip` is an mpv-only integration.

```bash
./ani-py --skip "frieren"
```

ani-py extracts the MAL id exposed by the active provider and asks `ani-skip` for episode-specific mpv flags using the current CLI contract:

```bash
ani-skip -q <mal-id> -e <episode>
```

The returned mpv flags are inserted into the mpv launch command. Missing MAL ids, missing binaries, command failures, and empty responses are reported instead of silently disabling skipping.

If the provider does not expose a MAL id, ani-py warns and plays normally. `--skip` is not applied to VLC/IINA/custom non-mpv players; ani-py reports that instead of silently pretending it worked.

## Downloads

```bash
./ani-py -d -e 1 "frieren"
ANI_PY_DOWNLOAD_DIR="$HOME/Videos/anime" ./ani-py -d -e 1-3 "frieren"
```

Download behavior:

1. Create the configured output directory if needed.
2. Download the selected subtitle track to `<title>.vtt` when available.
3. Prefer `yt-dlp` for the HLS video stream.
4. Fall back to `ffmpeg -c copy` when `yt-dlp` is unavailable.
5. Pass the provider referer and user-agent headers to the download backend.

A subtitle failure is reported, but it does not discard an otherwise valid video download.

## Environment variables

```text
ANI_PY_PLAYER          preferred player executable
ANI_PY_PLAYER_FLAGS    extra player flags
ANI_PY_ANDROID_PLAYER  Termux/Android player: auto, vlc, or mpv
ANI_PY_IPC_SOCKET      override private mpv IPC socket (advanced)
ANI_PY_MENU            fzf, rofi, dmenu, or fallback terminal UI
ANI_PY_MENU_FLAGS      extra menu flags
ANI_PY_DOWNLOAD_DIR    download destination
ANI_PY_HIST_DIR        state directory root
ANI_PY_CURL            curl/curl-impersonate executable
ANI_PY_PROVIDER        auto, hianime, kuhi, or animekai
ANI_PY_PROVIDER_ORDER  automatic failover order (default hianime; add kuhi/animekai to opt in)
ANI_PY_KUHI_URL        override Kuhi API base URL
ANI_PY_ANIMEKAI_URL    override AnimeKai base URL (manual/experimental)
ANI_PY_MODE            default mode: sub or dub
ANI_PY_QUALITY         default quality
ANI_PY_SKIP_INTRO      enable ani-skip by default (1/0)
ANI_PY_NO_DETACH       keep player attached (1/0)
ANI_PY_EXIT_AFTER_PLAY exit after play (1/0)
NO_COLOR               disable ANSI colors
```

## Test harness

The harness uses only the Python standard library. External tools are mocked so CI can verify the exact commands ani-py would execute without actually opening a player or downloading media.

Current coverage (120 tests) includes:

- CLI/environment parsing plus episode, range, and quality selection
- provider search/episode/stream parsing (HiAnime, Kuhi, AnimeKai)
- automatic failover, title matching, and history migration
- menu frontends (fzf/rofi/dmenu/fallback) and mpv IPC control
- VLC/IINA/custom-player command construction
- Termux/Android intent routing, subtitle relay, and player pinning
- `ani-skip`, `yt-dlp`/ffmpeg downloads, and subtitle handling
- missing-tool and failure-path behavior

Run everything:

```bash
./scripts/run-tests.sh
```

or:

```bash
make test
```

Smoke check only:

```bash
make smoke
```

External tool report:

```bash
make tools
```

Build the single-file distribution:

```bash
make build
```

## CI

`.github/workflows/test.yml` runs the compile/test/build harness on Python 3.10, 3.11, 3.12, and 3.13.

The unit harness deliberately does not require a live provider or launch real media players. Real-machine checks are still valuable before a release because provider/network behavior and third-party tools can change independently.

## Repository layout

```text
ani-py/
├── ani-py                  # executable wrapper
├── ani_py.py               # standalone implementation
├── README.md
├── CONTEXT.md              # local project memory
├── CHANGELOG.md
├── LICENSE
├── Makefile
├── .gitignore
├── docs/
│   ├── clips/                    # screen recordings + poster frames
│   │   ├── android-flow-vlc.mp4
│   │   ├── android-flow-vlc-poster.png
│   │   ├── android-flow-mpv.mp4
│   │   └── android-flow-mpv-poster.png
│   └── screenshots/
│       ├── ani-py-banner.png
│       └── android/
│           ├── termux-episode-picker.png
│           ├── termux-playback-controller.png
│           ├── termux-vlc-diagnostics.png
│           ├── vlc-playback.png
│           ├── vlc-playback-sky.png
│           ├── vlc-subtitle-track.png
│           ├── mpv-playback.png
│           ├── subtitles-rendered.png
│           ├── video-frame-map.png
│           └── video-frame-flags.png
├── scripts/
│   ├── build-standalone.sh
│   ├── check-tools.sh
│   ├── check-providers-live.sh
│   ├── run-tests.sh
│   └── smoke-help.sh
├── tests/
│   ├── test_animekai.py
│   ├── test_kuhi.py
│   ├── test_app_flow.py
│   ├── test_cli.py               # plus local passthrough/default-order tests
│   ├── test_core.py
│   ├── test_download.py
│   ├── test_history.py
│   ├── test_http.py
│   ├── test_menu.py
│   ├── test_playback.py
│   ├── test_players.py
│   ├── test_provider.py
│   ├── test_provider_manager.py  # plus local preflight tests
│   └── test_skip.py
└── .github/
    └── workflows/
        └── test.yml
```

## Development notes

- Provider-specific parsing is isolated behind `HianimeProvider`, `KuhiProvider`, and `AnimeKaiProvider`; `ProviderManager` owns selection/failover.
- The default repo launcher is `./ani-py`; `make build` produces a truly standalone `dist/ani-py`.
- External integrations should get a mocked regression test whenever a command-line contract changes.
- Keep a real-machine release checklist for mpv, `ani-skip`, downloads, and whichever menu frontends you actually use.

## Troubleshooting

### Provider returns no stream

In default `auto` mode, this checkout attempts `hianime` only (upstream default is `hianime,kuhi`). To diagnose one source directly:

```bash
./ani-py --provider hianime -e 1 "naruto"
./ani-py --provider kuhi -e 1 "naruto"
```

HiAnime may require a current curl-impersonate build when browser challenges change. The default Kuhi public instance is currently undeployed (verify with `./scripts/check-providers-live.sh`); set `ANI_PY_KUHI_URL` if you run your own deployment, or opt in via `--provider-order hianime,kuhi`. AnimeKai needs an explicitly configured compatible mirror (`ANI_PY_ANIMEKAI_URL`) and stays out of automatic failover.

### mpv already uses `/tmp/mpvsocket`

That is fine. ani-py creates its own per-process socket by default. Only use `ANI_PY_IPC_SOCKET` / `--ipc-socket` when you intentionally want to override it.

### `--skip` does nothing

Run:

```bash
./scripts/check-tools.sh
ani-skip --help
```

ani-py now reports missing MAL ids, missing binaries, empty responses, and failed `ani-skip` commands instead of silently ignoring them.

### Menu looks different from the README image

The banner represents the current **fzf** flow. Terminal fonts/themes vary, and `rofi`, `dmenu`, and the numbered fallback are different UIs by design.

## Legal note

Use this project responsibly and in accordance with local laws, provider terms, and your network environment.

## License

MIT
