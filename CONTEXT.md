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

**Current Development Focus:** Verify a live backup provider (Kuhi instance or AnimeKai mirror); otherwise hold the HiAnime-only default.

**Longer-Term Direction:** Multi-provider support landed in 0.4.0 (HiAnime + AnimeKai failover) and grew a Kuhi backend in 0.5.0. Both backups are experimental opt-in here until a live instance is confirmed. Next: re-enable whichever backup proves reachable.

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

**Data Flow:** `App`: query → `ProviderManager` (HiAnime → AnimeKai
failover) → menu pick → episodes → episode spec/range → `resolve`
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
  (`HianimeProvider`, `AnimeKaiProvider`) - never in `App`,
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

### mpv-first playback with private IPC
**Choice:** mpv primary; per-process private IPC socket; episode queues
run foreground with keep-open disabled; `--skip` forces fresh processes.
**Status:** Current
**Reason:** In-place IPC replace can't carry episode-specific ani-skip
flags safely; private socket avoids hijacking the user's mpv.

### Termux/Android playback port
**Choice:** Loopback-only `AndroidMediaRelay` plus Termux branches in `Playback`/`App`; Termux detection runs before desktop players; `am`/`pm` ACTION_VIEW intents to mpv-android (`is.xyz.mpv`) or VLC (`org.videolan.vlc`).
**Status:** Current (desktop-verified; live-device test pending)
**Reason:** Android VIEW intents cannot carry Referer headers, so the relay keeps provider headers inside Termux and rewrites HLS child URLs through itself on 127.0.0.1 behind a random secret path.
**Alternatives Considered:** Wholesale copy of reference ZIP (rejected: would clobber newer provider defaults); direct intent URLs without relay (rejected: Referer-gated streams fail).

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

**Assumptions to avoid:**
- Never assume every episode exposes the same quality renditions.
- Never assume `ani-skip` is installed - the app warns and continues.
- Never assume `~/.local/bin/ani-py` and the repo are in sync.

---

## 11. Implementation Notes

- `dist/` holds a built artifact; rebuild via `make build`, don't hand-edit.
- `scripts/check-termux.sh` gates live Android testing (needs `am`/`pm` plus mpv-android or VLC); on desktop it fails closed, which is expected.
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
| `docs/` | Banner image used by README |
| `dist/` | Standalone build output |
| `.github/workflows/` | CI pipeline |

**Important Files:**
| File | Purpose |
|---|---|
| `ani_py.py` | Entry point + all logic (importable for tests) |
| `ani-py` | Executable wrapper: `from ani_py import main` |
| `Makefile` | `test` / `smoke` / `tools` / `build` targets |
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
| AnimeKai (scraped) | Experimental backup via AJAX + `enc-dec.app` token/decryption helper; no trusted default domain (explicit mirror required) |
| Kuhi (API) | Experimental backup via AniList search + stream extraction; deep media preflight required; default public instance currently undeployed |
| CDN media hosts | HLS segments, subtitle files |

`auto` mode fails over HiAnime → AnimeKai (order via `--provider-order`
/ `ANI_PY_PROVIDER_ORDER`); base URL overridable via `ANI_PY_ANIMEKAI_URL`.
As of 2026-09-22 the hardcoded `anikai.to` does not resolve from here and
reachable mirrors serve anti-bot challenges - live AnimeKai unverified,
HiAnime path fully working. If `ani-skip` is missing or errors, `--skip`
warns and plays without skip flags.

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
- Dual provider (HiAnime + AnimeKai) with failover; no further backends yet.
- `rofi`/`dmenu` paths exist but are untested here (not installed).
- Termux playback is desktop-verified only (95/95 green plus relay Range/HEAD probe); live-device playback still pending.

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
