# ani-py

> Persistent project memory - durable knowledge only.
>
> This file is NOT a changelog, session log, TODO list, or git history.
> Git already records what changed and when. This file records what
> Git cannot: architecture, philosophy, non-goals, decisions, conventions,
> domain rules, and the current state of unfinished or partial work.
>
> Update ONLY when durable project knowledge changes (see Maintenance
> Rules at the bottom). Do not touch this file for commits, bug fixes,
> refactors, or session summaries.

---

## 1. Project Overview

**Purpose:** A polished, standalone terminal anime CLI for searching,
selecting, streaming, and downloading anime - one self-contained Python
implementation that keeps app logic in the standard library and delegates
networking, menus, playback, and downloads to best-of-breed external tools.

**Target Users:**
- Terminal-centric anime watchers on POSIX systems
- Owner-operated personal tool (single user, not a service)

**Current Milestone:** Personal tool, polishing

**Current Development Focus:** Live-test the new direct AniLight adapter on desktop and Termux while keeping the HiAnime-only default automatic chain.

**Longer-Term Direction:** Multi-provider support landed in 0.4.0 and now includes HiAnime and AniLight adapters. AniLight is the current candidate for a dependable direct backup. Kuhi and AnimeKai were removed in 0.5.2-rc11 once their upstreams were confirmed dead. The 3rd-provider slot is intentionally left open - see "Third provider search closed" in §8.

---

## 2. Technology Stack

### Runtime
| Component | Choice |
|---|---|
| Language | Python 3.10+ (3.10–3.13 in CI) |
| Python dependencies | None - stdlib only, permanently |
| HTTP | `curl` / curl-impersonate via `HttpClient` wrapper |
| Menu frontends | `fzf` (default), `rofi`, `dmenu`, built-in numbered fallback |
| Players | `mpv` (primary), `vlc`, `iina`, custom via `--player` |
| Downloads | `yt-dlp`, with `ffmpeg` fallback |
| Intro skipping | `ani-skip` 1.x (mpv only) |

### Tooling
| Component | Choice |
|---|---|
| Testing | stdlib `unittest` only (no pytest) |
| Build | `scripts/build-standalone.sh` → `dist/ani-py` |
| Tool check | `scripts/check-tools.sh` (incl. ani-skip `-i` support) |
| CI | GitHub Actions: compile + unittest + standalone build; tested main snapshots publish checksummed release assets |

### Infrastructure
| Component | Choice |
|---|---|
| Container | None |
| Deployment | Standalone release asset installed to `~/.local/bin/ani-py`; no package, no service |

---

## 3. Repository Structure

```text
ani-py/
├── ani-py                  # executable wrapper (thin launcher)
├── ani_py.py               # the monolith - all app logic
├── tests/test_*.py         # stdlib unittest suite
├── scripts/                # run-tests.sh, smoke-help.sh, check-tools.sh, build-standalone.sh
├── docs/                   # banner image
├── dist/                   # built standalone artifact
├── .github/workflows/      # CI
├── AGENTS.md               # rules for coding agents working in this repo
├── CONTRIBUTING.md         # setup, test, and contribution guide
└── CONTEXT.md              # maintainer-oriented project memory (see docs/development.md)
```

---

## 4. Architecture

**Overall:** Single-file stdlib app with a thin executable wrapper.
Scraping is isolated in one provider class so markup breakage is a
one-class fix; everything interactive (menus, playback, downloads) is
delegated to external binaries. Simple over clever; terminal-first.

**Folder Organization:** Flat repo - module, tests, and scripts at top
level. No packages, no layers, no plugins.

**Data Flow:** `App`: query → `ProviderManager` (provider-selected /
configured failover order) → menu pick → episodes → episode spec/range → `resolve`
(MAL id, streams, subtitle) → `Playback` (mpv via private IPC socket,
or `download` via yt-dlp/ffmpeg) → interactive controller loop →
provider-aware history.

---

## 5. Non-Goals

- No pip / runtime Python package dependencies, ever
- No GUI or web UI - terminal only
- No microservices, no daemon, no hosted backend
- No offline sync or streaming server

---

## 6. Architecture Rules

- All app logic lives in `ani_py.py`; `./ani-py` stays a thin launcher.
- All scraping/network parsing stays in provider adapters
  (`HianimeProvider`, `AniLightProvider`) - never in `App`,
  `Playback`, or `Menu`. `ProviderManager` owns selection/failover.
- Player control goes through `Playback` (private per-process mpv IPC
  socket by default; never hijack a shared `/tmp/mpvsocket`).
- Tests are stdlib `unittest` importing `ani_py` from the repo root.
- No network in unit tests - fake HTTP responses only; live provider
  checks are manual.

---

## 7. Coding Conventions

**General:**
- Match existing style; surgical changes only, no drive-by refactors.
- `fail()` raises `SystemExit` - tests expect that for error paths.
- Filenames/titles from the network are sanitized before touching disk.

**CLI:**
- Env-var defaults mirror flags (`ANI_PY_*`: mode, quality, player,
  skip, detach, download dir, IPC socket, menu flags, subtitle language).
- Passthrough options (`--menu-flags`, `--player-flag`) take dash-flags
  only via the `--opt='--flag'` equals form (argparse limitation) -
  documented in `--help`, covered by `tests/test_cli.py`.

**Testing:**
- New behavior gets a unittest; parsers/helpers, provider scraping
  with fake responses, history, playback IPC, ani-skip injection.

---

## 8. Project Decisions

### Stdlib-only, zero Python dependencies
**Choice:** Standard library only; external binaries for the rest.
**Status:** Current
**Reason:** Single-file portability, no install/venv friction for a
personal CLI.
**Alternatives Considered:** [likely: requests/click/pytest - rejected for dependency weight]

### Hianime as the (current) provider
**Choice:** Scrape hianime; provider markup isolated in `HianimeProvider`.
**Status:** Current
**Reason:** Works without API keys; isolation keeps markup churn cheap.
**Alternatives Considered:** Multi-provider (deferred - see §1 direction).

### Independent repo as its own project
**Choice:** Own repo and own direction, instead of contributing AI-generated code upstream.
**Status:** Current
**Reason:** Upstream repos don't want AI-generated code; full freedom to modify.
**Note:** ani-py is an independent, MIT-licensed implementation inspired by ani-cli and other terminal anime launchers, not a code fork of ani-cli. "Upstream" elsewhere in this file means ani-py's original upstream line (v0.5.2-rc11 era), never ani-cli.

### hianime-only default (v0.5.0 divergence)
**Choice:** This checkout keeps `--provider-order` default at `hianime` even
though upstream v0.5.0 ships `hianime,kuhi`. AniLight is opt-in until a live
instance is confirmed from this network.
**Status:** Current
**Reason:** The Kuhi public instance returns `DEPLOYMENT_NOT_FOUND` and
AnimeKai has no trusted domain - auto-probing either every run wastes time
and warns noise. Re-enable by changing one default when verified.
**Alternatives Considered:** Upstream default as-is (rejected: dead preflight
on every run).

### Kuhi and AnimeKai removed (0.5.2-rc11)
**Choice:** Both adapters, their tests, their env overrides, and their CLI
choices are deleted rather than left disabled.
**Status:** Current
**Reason:** Verified dead on the live network, not merely unconfigured. The
Kuhi API returns HTTP 404 on its search and extract endpoints, and AnimeKai
has no domain that answers. A provider that cannot resolve still costs
failover time and implies a safety net that is not there; disabled dead code
is worse than absent dead code. `scripts/check-providers-live.sh` now drives
the real provider classes so this class of decay is caught before a release.
**Alternatives Considered:** Keeping them as opt-in (rejected: they cannot
return a stream, so there is nothing to opt into); leaving the classes for
reference (rejected: Git history is the archive, and they would rot).

### Third provider search closed (0.5.2-rc11)
**Choice:** Leave the 3rd-provider slot empty rather than ship a provider that
cannot produce a playable stream. PR #4 (KAA + AniNeko + AniKoto) was closed
unmerged and its branch deleted; the commits remain recoverable from the
closed PR.
**Status:** Current. The 3rd slot is deliberately open.
**Reason:** Every candidate was live-tested against one bar: search, episode
list, resolve, and an `ffprobe`-confirmed **audio** stream. A video-only
stream is a reject, because a silent anime player is not a working provider.
- **KAA** (`kaa.lt`) - resolves to two HLS streams and mpv decodes them
  after `--demuxer-lavf-o=allowed_extensions=ALL`, but all sampled segments
  (both streams, 5 points across an episode) are h264 video with **no audio
  stream at all**, and `master.m3u8` 404s so there is no quality or audio
  variant to fall back to.
- **AniKoto** (`anikototv.to`) - catalog works, `resolve()` fails outright.
- **AniNeko** (`anineko.to`) - returns a ~2 KB JavaScript shell, zero results.
- **AniHQ** (`anihq.cc`) - the cleanest catalog found: public, auth-free WP
  REST over `anime`/`episode` with working `?search=`. Streams sit behind
  `kiranime/v1` routes that return 401 for anonymous callers (the page's
  `*_actions` and `global_nonce` values are theme nonces and do not satisfy
  REST auth), and a real headless browser is stopped by Cloudflare before the
  player ever requests a source.
**Alternatives Considered:** A browser or anti-bot layer (rejected: ani-py is
stdlib-only by design, and Cloudflare evasion is a different product with a
different risk profile); shipping a video-only provider as experimental
(rejected: it still looks like failover coverage that does not exist, which is
the exact failure mode that made Kuhi and AnimeKai worth deleting).

### AnimeKai demoted to experimental opt-in
**Choice:** Default `--provider-order` is `hianime` only; AnimeKai requires
explicit opt-in (`--provider animekai`, `--provider-order hianime,animekai`,
or `ANI_PY_ANIMEKAI_URL` override) and must pass a live preflight (known
AJAX search → JSON schema → result-anchor fragment) before automatic paths
use it. `--list-providers` tags it `[experimental]`.
**Status:** Superseded by "Kuhi and AnimeKai removed" in 0.5.2-rc11. Kept as
the record of why the provider was distrusted before it was deleted.
**Reason:** v0.4.0 shipped AnimeKai as an enabled backup on contract evidence
only - no live request ever succeeded. The hardcoded `anikai.to` has no DNS
answer and reachable mirrors serve anti-bot/parking pages. A configured name
is not evidence of a usable service.
**Alternatives Considered:** Swapping the default to another mirror (rejected:
clone/shutdown reports make any single mirror untrustworthy); leaving auto
failover enabled (rejected: slow confusing failures instead of clean skip).

### AniLight direct provider
**Choice:** Add AniLight as an experimental direct provider using
`api.anilight.live` for catalog/episode data and the current portable
`ryu`/AnimeGG source through AniLight's own proxy. Keep it out of the default
automatic chain until live desktop and Termux smoke testing is complete.
**Status:** Current
**Reason:** It fits the stdlib + curl architecture better than providers that
require browser automation or third-party hosted scraper dependencies. AniLight
also exposes broader HLS/soft-sub backends, but their current CDN/proxy rules are
more complex and are deliberately deferred until playback behavior is proven.
**Alternatives Considered:** Making it the default backup immediately
(rejected until live acceptance); AnimePahe/Miruro direct adapters (deferred
because their current anti-bot requirements conflict with the lightweight
runtime design).

### mpv-first playback with private IPC
**Choice:** mpv primary; per-process private IPC socket; episode queues
run foreground with keep-open disabled; episode switches use in-place IPC
`loadfile` even with `--skip`. ani-py parses ani-skip's flags itself and
feeds the intervals to an embedded mpv script over a small per-session state
file;
`--chapters-file` is passed via `loadfile`'s per-file options (mpv >= 0.38)
with a legacy three-argument fallback. The last window shape (fullscreen,
geometry, autofit) is captured over IPC before a restart and re-applied as
CLI flags on the next launch from `~/.local/state/ani-py/window-state.json`.
**Status:** Current
**Reason:** An in-place window kept its fullscreen/geometry across episodes,
while the `--skip` restart path respawned mpv at the mpv.conf geometry and
dropped the toggled window state. Private socket avoids hijacking the user's
mpv. Caching `_skip_args` per `(mal_id, episode)` narrowed the restart path
down to ani-skip episodes; the embedded script removes that restart too.

Skip intervals are not passed to mpv's user script-opts anymore; ani-py
extracts them from ani-skip's stdout into the private `<socket>.skip` state
file (sitting next to the IPC socket in `$XDG_RUNTIME_DIR` or the systemd
runtime dir, per the socket's location) and embeds
`~/.local/state/ani-py/ani-py-skip.lua`, which re-reads intervals on every
mpv file-loaded event. The Lua state-file parser must accept `%w_` keys
(`op_start`) — Lua's `%w` does not include the underscore, and using it
silently zero-fills the intervals.

### History records completion, not merely what was opened (2026.10.4)
**Choice:** `HistoryEntry` carries a `completed` flag, persisted as a trailing
`state=0`/`state=1` token. `--continue` reopens the last episode when it was
unfinished and advances only when it was finished.
**Status:** Current
**Reason:** History used to be written the moment playback started, while
`--continue` read it as "last watched" and returned `episodes[idx + 1]`. The two
assumptions collided: stopping 20 minutes into an episode skipped the rest of
it. Completion is the only thing that makes "continue" mean resume.
**Gotchas:**
- *Do not "simplify" `idx + 1` to `idx`.* With no completion signal, resuming a
  finished episode makes mpv rewind to the first frame (`_reset_resumed_position`)
  and rewatch it, with no way to advance. That trades a skip bug for a loop bug.
- *Rows without the token are read as completed, never unfinished.* They were
  written under the old "advance" behaviour; defaulting them the other way would
  replay an already-finished episode the first time a user ran `--continue`.
- *The state token is matched by shape at the end of the row, not by field
  position.* Titles may contain tabs, so a fixed position would corrupt them.
- *Only natural EOF marks an episode finished* (`Playback.reached_eof()`).
  The interactive menu re-checks it **on every exit path, before the player is
  stopped or detached**, because that is the only moment the answer is both
  known and still knowable. Recording at the top of the menu loop is too early
  (the episode has not finished yet) and recording after `playback.stop()` is
  too late (mpv is gone, so every episode looks unfinished). Ordinary playback
  records at those exits too: closing the menu is the normal way users end a
  long watch, and without it a finished episode stays unfinished and
  `--continue` replays it.
- *Closing the mpv window (its X button) is external and has no exit path.*
  Every hook above is a branch ani-py controls; an X click is not. So
  `_watch_completion` records the finish the moment it is observed while mpv is
  still alive, and the last known state then survives an abrupt close.
- *The watcher's 1s poll is a deliberate tradeoff, not the only option.*
  mpv's `observe_property` was evaluated and rejected: it is supported and does
  push `property-change` events, but under `--keep-open=yes` it never delivered
  `eof-reached: true`. The terminal state arrived as an event with no `data`
  field (property dropped), so an event-driven version would still need this
  poll as a fallback. Cost is one small query per second on a connection that
  is already open; the residual is a ~1s window in which closing the window
  immediately after an episode ends records it unfinished. Do not swap this for
  `observe_property` without re-verifying that it reports `true`; tightening the
  interval buys only a narrower version of the same edge case.
- *`reached_eof()` returns tri-state: True, False, or None.* None means the
  player could not be reached, which is *not* the same as False. Writing None
  down as "did not finish" makes the exit-path record erase the watcher's
  result, so an episode watched to the end and then closed via the X button
  would be replayed by `--continue`. Unknown must never overwrite known.
- *History sorting is display-only.* The file stays oldest-first and `HistoryStore.update()` edits in place; `-c` and `--forget` share the `_ordered_history` helper so `--sort recent` (newest first) and `--sort alpha` (title A-Z) match in both views. Plain `-c` keeps file order.
- *`HistoryStore.update()` holds a lock.* The watcher writes from a background
  thread while the main thread may be writing on exit; it is a
  read-modify-write, so an unsynchronised interleaving would drop an episode.
  `save()` writes a temp file and renames, so readers never see a partial row.

### Provider-independent auto-next (2026.10.4)
**Choice:** `--auto-next` is an app/controller feature, not a provider
capability. A single selected episode expands to the remaining entries in the
already-loaded episode list. The default cap of 12 (`--auto-next-limit`, 0 for
no cap) applies only to that implicit expansion; an explicit multi-episode
selection or range is always played exactly as chosen. Each following episode
is resolved lazily through the normal `_bundle()` path only after desktop mpv
reports an `end-file` event over ani-py's private IPC socket.
**Status:** Current
**Reason:** Providers should continue to expose only search/episode/resolve
data. Keeping progression in `App` makes auto-next consistent across provider
failover and preserves quality/subtitle preferences, history, and episode-
specific ani-skip behavior. Natural EOF is deliberately distinguished from
player close/stop; interrupted playback never advances automatically.
**Limitations:** Reliable completion detection currently requires desktop mpv
with POSIX Unix-socket IPC. VLC, IINA, custom players, Windows named-pipe mpv
control, and Android intent players are rejected for `--auto-next` rather than
using process exit, relay traffic, or other completion guesses. The queue uses
the episode-list snapshot loaded at startup and does not cross title/season
boundaries automatically.

**Gotchas learned while hardening this (do not "simplify" these away):**

- *Completion is mpv's `end-file` event, not a polled `eof-reached`.*
  Polling was racy in three separate ways, all reproduced against real mpv:
  the property is briefly unavailable while a file swaps, it can still read
  `True` from the file that just finished right after a `loadfile` (an episode
  was advancing 0.46s after it loaded, not after it played), and it becomes
  permanently unavailable once mpv unloads at the end, which surfaced to users
  as a bogus "Lost contact with mpv" after a full 20s grace. `end-file` also
  carries `reason`, the only way to tell a real EOF from `error`, `quit`, or a
  replacement. `_IpcSession` holds one connection open and multiplexes replies
  (matched by `request_id`) with events.
- *mpv's `input-ipc-server` accepts exactly one client and pushes events only
  to a connected client.* A connect/disconnect-per-command `_ipc()` silently
  drops every event in between, which is why polling was used originally. The
  session must therefore be opened before the first file can end.
- *Auto-next launches mpv with `--keep-open=no --idle=yes`* (`event_completion`).
  With `--keep-open=yes` mpv just pauses at the end and emits no `end-file` at
  all; with `--keep-open=no` alone it would exit, so `--idle=yes` is what keeps
  it alive to load the next episode into.
- *`active()` must not probe `get_property path`.* `path` is unavailable while
  mpv is idle with nothing loaded, which is exactly the state auto-next sits in
  between episodes, so probing it made ani-py believe mpv had died and restart
  the player on every episode switch. It probes `idle-active` instead. A
  session left behind by a dead player is dropped in `play()` for the same
  reason.
- *mpv retries a stalled source itself and never ends the file*, so waiting
  only on `end-file` hangs forever. `_wait_end_file` also accumulates
  `paused-for-cache` time and gives up as `stalled`. It accumulates rather than
  requiring a continuous run because mpv flaps that flag between retries.
- *mpv `save-position-on-quit` resumes at the end of a finished file*, which is
  a genuine EOF and silently skipped the entire queue in about 1.2s instead of
  19s. `_reset_resumed_position()` rewinds when `time-pos >= duration - 1s`
  and leaves genuine mid-episode resumes alone. It is applied on both the
  launch path and the `loadfile` path, because mpv re-applies the saved
  position on every file it opens.
- *`replace()` stays in-place even when ani-skip returned flags* (`_skip_args`
  is cached per `(mal_id, episode)`). Before this change, any episode with
  ani-skip flags restarted mpv entirely, which dropped fullscreen and any
  other live window state. The embedded skip-runtime is re-synced via the
  `ani-py-*` skip-state file instead.
- *Switching episodes re-uses the same mpv window whenever possible.* A fresh
  process only starts if IPC is gone or the switch fails; the previous
  window's `fullscreen`/`geometry`/`autofit` is captured over IPC right
  before teardown into `window-state.json` and re-applied as CLI flags, so a
  fullscreen auto-next run does not revert to the mpv.conf geometry.

- *Episode switches drop the previous episode's external subtitle tracks*
  (`_clear_external_subtitles`) so a new episode cannot fall back to the last
  one's subs. Embedded tracks stay; they belong to the file mpv is playing.
- *The unusable-player rejection runs in `App.run()` before the search*, not
  only in `_run_auto_next`, so `-p vlc --auto-next` fails immediately instead
  of after a full lookup.
- *`--auto-next-limit` must not trim an explicit selection.* The cap exists
  only to stop one selected episode expanding into a whole season unattended;
  a range the user deliberately picked is their decision and is played in full.
  Applying the cap to both paths contradicted this section's own docstring.
- *Auto-next leaves the terminal silent for the length of an episode*, and the
  per-episode banner is printed once and never seen again. `NowPlaying` fills
  that gap with a line redrawn in place plus an OSC tab title. Both are gated
  on `sys.stderr.isatty()`: a piped or redirected run must not accumulate
  carriage returns in a log file. It is `--auto-next` only, because ordinary
  playback still has the interactive menu. `Playback.progress()` reads
  `time-pos`/`duration` over the existing session rather than opening a new
  connection, which mpv would refuse.
- *Never render the episode number and the queue length as one ratio.*
  `Ep {episode.number}/{len(queue)}` reads as "19 out of 8" the moment playback
  starts mid-season, because those are unrelated numbers: the first is the
  site's episode number, the second is how many were queued. They are shown
  separately as `Ep 19 (1/8)`. The same applies to the tab title.

Every one of these was found by driving the real `_run_auto_next` against a
real mpv; none is reachable from the mocked unit tests. `wait_for_completion`
falls back to polling only when ani-py could not hold a session open (an
adopted detached session). Treat a change to this path as unverified until it
has been run against real mpv.

### Termux/Android playback port
**Choice:** Detached loopback relay child (`run_android_relay` via `--_android-relay-config`) plus intent dispatch in `Playback`; `android_auto` asks Android's resolver first, explicit `vlc`/`mpv` modes pin `org.videolan.vlc` / `is.xyz.mpv`; `termux-open` chooser and existing-`rish` retry are fallbacks only.
**Status:** Current (0.5.1 rework; live-device verified 2026-09-24). Supersedes the 0.5.0 in-process `AndroidMediaRelay` with `pm path` preflight gating.
**Reason:** `pm path` from an ordinary Termux UID is unreliable and unnecessary for `VIEW` dispatch; Android intents still cannot carry Referer headers, so the relay keeps provider headers inside Termux and rewrites nested HLS child/key/segment URLs through itself on 127.0.0.1 behind a random secret token. The production relay also keeps a capability set: only the initial stream/subtitle targets and child URLs discovered while rewriting HLS manifests may be fetched. A detached child with idle timeout lets `Detach & exit` survive.
**Alternatives Considered:** Wholesale copy of reference ZIP (rejected: would clobber newer provider defaults); direct intent URLs without relay (rejected: Referer-gated streams fail); killing the Android player on `stop()` (rejected: stopping the local relay is the least invasive action).

### Android subtitles (0.5.2-rc3 → rc7, live-device verified 2026-09-25)
**Choice:** WebVTT subtitles ride the HLS playlist as an `EXT-X-MEDIA` rendition served by the loopback relay. ani-py uses its own `ani-py-subs` group, leaves an existing upstream subtitle topology untouched, and includes language/label metadata only when the provider supplies it. Variant media playlists are wrapped in a generated single-variant master when necessary, and the rendition URI points to a `sublist` VOD playlist around the complete subtitle file.
**Fallback:** VLC still receives the conventional `subtitles_location` URL extra. Automatic shared-storage staging was removed in rc7 because it created per-episode files and was not the path that made Android subtitles reliable. mpv-android uses the HLS rendition because shell `am` cannot construct its Parcelable subtitle-array extra.
**Live-device result:** VLC 3.7.1 and mpv-android both played subtitles through the HLS rendition on the project owner's Termux device. On that VLC setup, English auto-selection required the one-time custom libVLC option `--sub-language=eng`; treat this as a tested player/device preference, not a universal VLC requirement.
**Known limitation:** If the ROM destroys VLC's activity in the background, playback can restart at position 0 on return because the external intent does not carry a resume position.

### Multi-track soft subtitles and detached mpv reattachment (0.5.2-rc11)
**Choice:** Providers keep `subtitles: list[SubtitleTrack]` on the stream
bundle instead of collapsing to one URL; `subtitle_tracks` synthesises a
track from the legacy single `subtitle` field when a provider still sets
only that. Selection is always explicit - `--sub-lang` / `ANI_PY_SUB_LANG`
or `Change subtitle` in the controller - never an automatic pick. Desktop
mpv switches tracks live over the private IPC socket; VLC/IINA/Android use
the existing replace-and-relaunch path. `Detach & exit` on desktop mpv
writes a small session record (private socket + anime/controller context),
and `ani-py --attach` reconnects to the same process; no-query startup
offers the same reattachment.
**Status:** Current
**Reason:** Collapsing provider subtitle lists to one URL discarded
selectable tracks HiAnime already exposes. Reattachment reuses
the running mpv instead of restarting playback and losing position.
**Invariants:**
- An explicit language/label request is strict. A miss selects no
  subtitle and warns (`choose_subtitle_track`); it never falls back to a
  different language. Only `auto` picks a default.
- Download subtitles are written beside the video with a language-aware
  name (e.g. `Episode 1.de.vtt`); `--sub-lang off` disables external
  subtitles entirely.
- Stale or dead session records are rejected and cleaned up, not adopted.
- An mpv IPC switch changes the subtitle track only; it does not reload
  the video.
**Limitations:** Only desktop mpv is reattachable. Android intent players
and the other desktop players keep their existing plain-detach behaviour,
because they have no private socket to reconnect to.

### Calendar versioning and release snapshots (2026.9.30)
**Choice:** `VERSION` is a calendar version (`YYYY.M.D`) - the date the most
recent user-visible change landed. No suffixes, no counters, no semver. A
successful `test` workflow on `main` publishes a unique
`release-<VERSION>-<short-sha>` snapshot when distributable files changed;
`install.sh` and `ani-py --update` consume the latest release asset and verify
`ani-py` against `SHA256SUMS`.
**Status:** Current
**Reason:** CalVer avoids the patch/minor judgment problem that produced the old
`0.5.2-rc11` sequence, while automated release snapshots remove mutable
`main` from the normal install/update data path without adding a manual release
ceremony. `VERSION` remains load-bearing: `--update` compares it to decide
whether replacing the local copy would be a downgrade.
**Alternatives Considered:** SemVer with bump-per-merge (rejected: the
patch/minor judgment call is the thing that went wrong before); a build counter
like `-rc12` (rejected: a counter in semver clothing); raw `main` downloads
(rejected for normal installs/updates because the bytes are mutable and were
not independently checked). `ANI_PY_REF` remains an explicit development
escape hatch and warns that checksum verification is bypassed.
**Transition:** Copies installed before this change carry `0.5.2-rc11`.
`_version_key` therefore reads the leading digits of each dot-separated
component, so those installs upgrade normally instead of making the guard treat
them as unparseable and stand down. A regex test pins the CalVer format so
semver cannot creep back in.

---

## 9. Domain Knowledge

- The provider MAL id is the key `ani-skip` input (`-i <mal-id> -e <ep>`).
- Sub and dub resolve through separate servers/streams.
- HiAnime lists several servers per episode. `resolve` tries ZokoAnime
  first and falls through to the others of the same sub/dub type when a
  server's embed page or HLS host fails (dead CDN, bad TLS cert, changed
  markup). Each server is assumed to serve the same player payload shape;
  one that does not is skipped, not treated as a hard error. If every
  server fails, the first error is raised with the others named. Note that
  upstream currently lists a single server per type, so the fall-through has
  nothing to walk until that changes - and the current payload shape is JS
  gated, see Known Gotchas.
- HLS variant sets differ per episode upstream (one episode may offer
  1080/720/360 while another offers 1080-only); quality selection falls
  back to best available - not an app bug.
- Episode spec: `4`, ranges `4-9` / `:` / `..`, `0` = first, `-1` = last,
  reversed ranges allowed; anything unresolvable yields empty, never an error.
- A multi-episode selection is a sequential queue, not parallel players.
- History identities are provider-aware (`provider`, `provider_id`);
  legacy three-column HiAnime history migrates transparently on read.
- Cross-provider title matching is conservative: exact/high-confidence
  matches auto-map, ambiguous ones go to the user menu.

---

## 10. Known Gotchas

### Provider / network
- Hianime markup changes silently break search/resolve - when streams
  fail, check markup first; the fix is confined to `HianimeProvider`.
- HiAnime embed hosts gate on `Referer` and answer **HTTP 200** with their
  own branded error page when it is missing, so a soft 404 looks exactly
  like a markup change. Always send a `Referer` when fetching an embed page,
  and treat a known error-page marker as a host failure, not a markup
  failure. This masked a real upstream change for a while.
- HiAnime's live backend moved from the `window.__P` blob to a
  `stream/getSources` XHR (megaplay.buzz). Only the `enc` field is
  encrypted: AES-256-CBC over the manifest JSON, key = the 16-byte literal
  seed `i?LMTAx0Q6,:}50U` zero-padded into a 32-byte buffer, IV =
  `W0;27ToaUpl_P%'c`. The manifest's HLS playlist and its segments are
  plaintext. stdlib has no AES, so `ani_py.py` carries a small decrypt-only
  AES-256-CBC implementation (`_aes256_cbc_decrypt`); `_resolve_embed`
  keeps the legacy `window.__P` path as a fallback when the blob is still
  present. `stream/getSources` needs the embed's own origin as `Referer`
  plus `X-Requested-With: XMLHttpRequest`.
- The megaplay embed exposes `data-id`, `data-realid` and `data-mediaid`.
  Only **`data-id`** may be sent to `stream/getSources`: it is the
  per-episode, per-mode identifier. `data-realid` is shared by the sub and
  dub embeds of the same episode, so requesting it returns **a different
  show entirely** (verified: One Piece episode 1 resolved to Toilet-bound
  Hanako-kun's video and subtitle), and it also collapses sub and dub onto
  one stream. `data-mediaid` is the series id and is equally wrong here.
  When a provider "works" but plays the wrong content, identify the media
  from its subtitle text - durations and filenames look entirely normal.
- The `type` parameter on `stream/getSources` is ignored: the `id` alone
  selects the stream, because `data-id` already encodes the mode (the same
  `data-id` requested with `type=sub` and `type=dub` returns identical
  media). ani-py still sends `type` for fidelity with the real client.
- Dub works and is owner-verified on-device (2026-09-29), with two caveats
  worth knowing. The dub master is a materially worse encode than the sub
  - `1440x1080` at 23.976 fps and ~1.2 Mbps against the sub's
  `1920x1080` - so it appears to be an older print rather than a modern
  dub. And the `tracks` array is **not mode-aware**: a dub request returns
  the *sub* media's English subtitle, so playing dub attaches a subtitle
  that does not match the dubbed dialogue. Turning subtitles off in mpv is
  the accepted workaround. Fixing it properly would mean discarding
  `tracks` for dub or resolving it per mode, and neither is done - do not
  "fix" the mismatch by trusting the dub track, it is the sub's file.
- Do not repeat the earlier wrong conclusion that the m3u8 playlists and
  every segment were encrypted and that a local decrypting proxy was
  required. That reading came from the client's own proxy fallback path,
  not the normal stream; the normal stream is plaintext HLS. Confirm a
  claimed encryption layer against the actual bytes (`file`, `ffprobe`)
  before designing around it.
- ffmpeg's HLS demuxer rejects segments whose extension is not in its
  allowlist, and it prints a misleading `mime type is not rfc8216
  compliant` rather than the real reason. KAA served MPEG-TS as `.jpg`, and
  HiAnime's current megaplay backend does the same with a rotating set of
  decoys (`.jpg`, `.html`, `.js`, `.css`, `.txt`, `.png`, `.webp`, `.ico`).
  mpv recovers with `--demuxer-lavf-o=allowed_extensions=ALL`, which
  `Playback._mpv_command` now always passes; the ffmpeg CLI flag
  `-allowed_extensions ALL` does *not* work for the same URL, because the
  option only takes effect on the demuxer context. This - not encryption -
  was the thing actually blocking playback.
- A provider that resolves to a playable stream is not the same as a
  provider that works. KAA resolves cleanly and mpv decodes it, yet every
  segment is video-only with no audio. Always ffprobe a real segment for
  an audio stream before calling any provider viable.
- Never conclude an anime site is dead from a failed search. Verify with a
  bare homepage fetch first - an assumed URL shape yields a false "dead"
  reading on sites that are perfectly alive.
- WordPress anime themes gate streams behind an authenticated REST nonce
  (anonymous `kiranime/v1` calls return 401) even when the catalog is
  fully public over `wp-json/wp/v2`. A usable catalog is not a usable
  provider.
- CDN throughput varies (~400KB/s observed); slow downloads are usually
  the CDN, not the app. yt-dlp fragment retries handle transient stalls.

### CLI parsing
- `--menu-flags --exact` fails (`expected one argument`) - argparse won't
  take flag-like values positionally. Always use `--menu-flags='--exact'`.
  Same for `--player-flag`.
- Interactive menus need a TTY; headless runs hang or die at the prompt.

### Shell / processes
- Detached mpv survives the controller. A clean `Detach & exit` on desktop
  mpv records a session that `--attach` can adopt, but a *crashed* script
  still leaves an orphan player + IPC socket behind; check `pgrep -f mpv`
  after failures.
- `pkill -f <pattern>` matches your own shell's command line; exclude
  self before killing test players.
- `fzf --ansi` strips ANSI codes from its output, so menu rows built
  with `sty()` never round-trip exactly - match picks ANSI-insensitively
  (see `_strip_ansi`; `_from_history` regressed as `ValueError` on Termux).

**Assumptions to avoid:**
- Never assume every episode exposes the same quality renditions.
- Never assume `ani-skip` is installed - the app warns and continues.
- Never assume `~/.local/bin/ani-py` and the repo are in sync.

---

- There is no package manager. Normal installs and updates consume the latest
  checksummed GitHub Release snapshot produced only after the main test workflow
  succeeds. The installer downloads into a `mktemp` directory and verifies
  `ani-py` against `SHA256SUMS`; `--update` fetches bytes without text/locale
  decoding, verifies the same digest, then atomically replaces the running file.
  The downgrade guard still refuses a release whose `VERSION` is older than a
  newer local development copy. `ANI_PY_REF` is intentionally development-only
  and warns before bypassing release checksum verification.

## 11. Implementation Notes

- `dist/` holds a built artifact; rebuild via `make build`, don't hand-edit.
- `Search another anime` in the interactive controller replaces playback
  inside the same session: the current player keeps running while a new title
  and episode are chosen, then the old player is torn down and the new one
  starts. Position is not carried across the swap.
- `scripts/check-termux.sh` gates live Android testing (needs `am`; `termux-open`/`rish` optional; player apps are not probed via `pm path`); on desktop it warns, which is expected.
- `rofi`/`dmenu` are optional - absent here; `fzf` + numbered fallback
  cover menu paths.
- Public repo: `github.com/Onehand-Coding/ani-py` (`main`, pushed 2026-09-22).
  `dist/` and `__pycache__/` are git-ignored; rebuild via `make build`.

---

## 12. Repository Map

**Important Directories:**
| Directory | Purpose |
|---|---|
| `tests/` | Unit suite (`app_flow`, `cli`, `core`, `download`, `http`, `menu`, `playback`, `players`, `provider`, `provider_manager`, `session`, `skip`) |
| `scripts/` | Test/build/tool-check automation |
| `docs/` | Android, provider, development docs plus screenshots/clips |
| `dist/` | Standalone build output |
| `.github/workflows/` | CI pipeline |

**Important Files:**
| File | Purpose |
|---|---|
| `ani_py.py` | Entry point + all logic (importable for tests) |
| `ani-py` | Executable wrapper: `from ani_py import main` |
| `Makefile` | test/smoke/tools/build plus installer convenience targets |
| `install.sh` / `uninstall.sh` | Linux/Unix + Termux user installer lifecycle |
| `AGENTS.md` | Non-negotiable rules for coding agents, plus the CONTEXT.md conventions |
| `CONTRIBUTING.md` | Setup, the test gate, and how to add a provider |

**Generated - never edit manually:**
| Path | Type | Regenerated by |
|---|---|---|
| `dist/ani-py` | file | `scripts/build-standalone.sh` |
| `__pycache__/` | dir | Python interpreter |

---

## 13. Development Commands

```bash
# Full gate
make test          # scripts/run-tests.sh: compile + unittest + help/version smoke + standalone build
make smoke         # compile + --help
make tools         # external binary check
make build         # standalone artifact to dist/
```

**Verification commands:**

```bash
python3 -m py_compile ani_py.py ani-py
python3 -m unittest discover -s tests
./ani-py --help && ./ani-py --version
```

**Common debugging:**

```bash
./scripts/check-tools.sh            # incl. ani-skip -i support probe
pgrep -a -f 'force-media-title='    # find orphan test players (self-excluding pattern)
ANI_PY_PLAYER_FLAGS='--vo=null --ao=null --vid=no'  # headless mpv probing
ANI_PY_DOWNLOAD_DIR=/tmp/x          # redirect downloads
```

---

## 14. External Services

| Service | Purpose |
|---|---|
| hianime (scraped) | Primary: search, episodes, stream/subtitle resolve |
| AniLight | Experimental direct backup: AniList/slug search, sub/dub portable progressive source via AniLight API proxy, MAL metadata |
| CDN media hosts | HLS segments, subtitle files |

Default `auto` mode currently uses HiAnime only. AniLight is an experimental
opt-in provider configured through `--provider-order` /
`ANI_PY_PROVIDER_ORDER` and is the current live-testing candidate. Kuhi and
AnimeKai were removed in 0.5.2-rc11 after their upstreams were confirmed dead.
Mocked adapter tests are not evidence that a deployment is currently live -
run `scripts/check-providers-live.sh`, which drives the real provider classes.
If `ani-skip` is missing or errors, `--skip` warns and
plays without skip flags.

**Secrets Location:** None - no keys, no accounts, no `.env`.

---

## 15. AI Collaboration Notes

**Implementation Style:**
- Prefer incremental changes over rewrites; match existing style.
- Never add a Python dependency - few lines of stdlib beat any package.
- Never put logic in the `ani-py` wrapper.
- Keep network code in `HianimeProvider`, player code in `Playback`.
- Explain architectural changes before implementing them.

**Communication:**
- Ask before architectural decisions; per-conflict confirmation when
  syncing from upstream exports (prior pattern: ask per file).
- State assumptions explicitly; present trade-offs when approaches differ.

**Learning Preference:**
- Owner is technical; favor maintainable, plain solutions over clever ones.

---

## 16. Known Limitations

- POSIX-only in practice (Unix IPC sockets for mpv control).
- `--skip` is mpv-only by design; other players warn and ignore it.
- Multiple provider adapters exist, but the default automatic chain remains HiAnime-only until a backup passes live acceptance.
- `rofi`/`dmenu` paths exist but are untested here (not installed).
- Termux playback is live-device verified (97/97 green plus relay Referer/Range/HLS-rewrite probes; owner confirmed VLC/auto playback on-device 2026-09-24).
- `--update` replaces the script only. It does not re-run the installer's
  dependency installation (`--deps`) or its Termux prefix detection, and it
  needs write access to the install directory (it points at `sudo`
  otherwise). Re-run `install.sh --deps` when external tools are missing.

---

## Maintenance Rules

> Decisions are append-only (see §8). Never delete a decision - supersede or
> deprecate it instead.

Umbrella rule: never update this file for implementation work unless it
changes durable project knowledge. (A bug fix is usually just a bug fix - but
if fixing it revealed provider markup behavior, that's a gotcha.)

Before touching this file, run through this checklist. If every answer is
"No," leave CONTEXT.md untouched.

- Did the architecture or a design decision change?
- Did project philosophy or a non-goal change?
- Did an architecture rule change (added, removed, or relaxed)?
- Did a coding convention change project-wide?
- Did domain knowledge get discovered or clarified?
- Did a new gotcha appear, or a wrong assumption get exposed?
- Did an implementation note change (something now mid-flight, or now resolved)?
- Did the stack, milestone, long-term direction, or external services change?
- Did a permanent limitation get added or lifted?

**Never update this file for:**
- commits
- completed tasks
- bug fixes
- refactors
- git history
- branch changes
- session summaries
- TODO lists
- transient blockers (issue tracker's job)

Git already records all of those.
