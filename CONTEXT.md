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

**Longer-Term Direction:** Multi-provider support landed in 0.4.0 and now includes HiAnime, AniLight, Kuhi, and AnimeKai adapters. AniLight is the current candidate for a dependable direct backup; Kuhi and AnimeKai remain experimental legacy options until they prove live and maintainable.

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
| Tool check | `scripts/check-tools.sh` (incl. ani-skip `-q` support) |
| CI | GitHub Actions: compile + unittest + standalone build |

### Infrastructure
| Component | Choice |
|---|---|
| Container | None |
| Deployment | Symlinked from `~/.local/bin/ani-py`; no package, no service |

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
├── CHANGELOG.md
└── CONTEXT.md              # this file (local only, not upstream)
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
  (`HianimeProvider`, `AniLightProvider`, `KuhiProvider`,
  `AnimeKaiProvider`) - never in `App`,
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
  skip, detach, download dir, IPC socket, menu flags).
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

### Independent fork as its own repo
**Choice:** Own copy instead of contributing AI-generated code upstream.
**Status:** Current
**Reason:** Upstream repos don't want AI-generated code; full freedom to modify.

### hianime-only default (v0.5.0 divergence)
**Choice:** This checkout keeps `--provider-order` default at `hianime` even
though upstream v0.5.0 ships `hianime,kuhi`. Kuhi and AnimeKai stay
experimental opt-in until a live instance is confirmed from this network.
**Status:** Current
**Reason:** The Kuhi public instance returns `DEPLOYMENT_NOT_FOUND` and
AnimeKai has no trusted domain - auto-probing either every run wastes time
and warns noise. Re-enable by changing one default when verified.
**Alternatives Considered:** Upstream default as-is (rejected: dead preflight
on every run); removing the providers entirely (rejected: machinery is good
and activates via one env var).

### AnimeKai demoted to experimental opt-in
**Choice:** Default `--provider-order` is `hianime` only; AnimeKai requires
explicit opt-in (`--provider animekai`, `--provider-order hianime,animekai`,
or `ANI_PY_ANIMEKAI_URL` override) and must pass a live preflight (known
AJAX search → JSON schema → result-anchor fragment) before automatic paths
use it. `--list-providers` tags it `[experimental]`.
**Status:** Current
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
run foreground with keep-open disabled; `--skip` forces fresh processes.
**Status:** Current
**Reason:** In-place IPC replace can't carry episode-specific ani-skip
flags safely; private socket avoids hijacking the user's mpv.

### Termux/Android playback port
**Choice:** Detached loopback relay child (`run_android_relay` via `--_android-relay-config`) plus intent dispatch in `Playback`; `android_auto` asks Android's resolver first, explicit `vlc`/`mpv` modes pin `org.videolan.vlc` / `is.xyz.mpv`; `termux-open` chooser and existing-`rish` retry are fallbacks only.
**Status:** Current (0.5.1 rework; live-device verified 2026-09-24). Supersedes the 0.5.0 in-process `AndroidMediaRelay` with `pm path` preflight gating.
**Reason:** `pm path` from an ordinary Termux UID is unreliable and unnecessary for `VIEW` dispatch; Android intents still cannot carry Referer headers, so the relay keeps provider headers inside Termux and rewrites nested HLS child/key/segment URLs through itself on 127.0.0.1 behind a random secret token. A detached child with idle timeout lets `Detach & exit` survive.
**Alternatives Considered:** Wholesale copy of reference ZIP (rejected: would clobber newer provider defaults); direct intent URLs without relay (rejected: Referer-gated streams fail); killing the Android player on `stop()` (rejected: stopping the local relay is the least invasive action).

### Android subtitles (0.5.2-rc3 → rc7, live-device verified 2026-09-25)
**Choice:** WebVTT subtitles ride the HLS playlist as an `EXT-X-MEDIA` rendition served by the loopback relay. ani-py uses its own `ani-py-subs` group, leaves an existing upstream subtitle topology untouched, and includes language/label metadata only when the provider supplies it. Variant media playlists are wrapped in a generated single-variant master when necessary, and the rendition URI points to a `sublist` VOD playlist around the complete subtitle file.
**Fallback:** VLC still receives the conventional `subtitles_location` URL extra. Automatic shared-storage staging was removed in rc7 because it created per-episode files and was not the path that made Android subtitles reliable. mpv-android uses the HLS rendition because shell `am` cannot construct its Parcelable subtitle-array extra.
**Live-device result:** VLC 3.7.1 and mpv-android both played subtitles through the HLS rendition on the project owner's Termux device. On that VLC setup, English auto-selection required the one-time custom libVLC option `--sub-language=eng`; treat this as a tested player/device preference, not a universal VLC requirement.
**Known limitation:** If the ROM destroys VLC's activity in the background, playback can restart at position 0 on return because the external intent does not carry a resume position.

---

## 9. Domain Knowledge

- The provider MAL id is the key `ani-skip` queries (`-q <mal-id> -e <ep>`).
- Sub and dub resolve through separate servers/streams.
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
- CDN throughput varies (~400KB/s observed); slow downloads are usually
  the CDN, not the app. yt-dlp fragment retries handle transient stalls.

### CLI parsing
- `--menu-flags --exact` fails (`expected one argument`) - argparse won't
  take flag-like values positionally. Always use `--menu-flags='--exact'`.
  Same for `--player-flag`.
- Interactive menus need a TTY; headless runs hang or die at the prompt.

### Shell / processes
- Detached mpv survives the controller - a crashed script leaves an
  orphan player + IPC socket behind; check `pgrep -f mpv` after failures.
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

## 11. Implementation Notes

- `dist/` holds a built artifact; rebuild via `make build`, don't hand-edit.
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
| `tests/` | Unit suite (`animekai`, `kuhi`, `app_flow`, `cli`, `download`, `http`, `menu`, `playback`, `players`, `provider`, `provider_manager`, `skip`) |
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
| `CHANGELOG.md` | Release notes |

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
./scripts/check-tools.sh            # incl. ani-skip -q support probe
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
| AnimeKai (scraped) | Experimental backup via AJAX + `enc-dec.app` token/decryption helper; no trusted default domain (explicit mirror required) |
| Kuhi (API) | Experimental backup via AniList search + stream extraction; deep media preflight required; default public instance currently undeployed |
| CDN media hosts | HLS segments, subtitle files |

Default `auto` mode currently uses HiAnime only. AniLight, Kuhi, and AnimeKai
are experimental opt-in providers configured through `--provider-order` /
`ANI_PY_PROVIDER_ORDER`; AniLight is the current live-testing candidate and
AnimeKai additionally requires an explicit
`ANI_PY_ANIMEKAI_URL` mirror. The previously tested public Kuhi deployment
is currently undeployed, and no AnimeKai domain is treated as a trusted
default. Mocked adapter tests are not evidence that either deployment is
currently live. If `ani-skip` is missing or errors, `--skip` warns and
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
