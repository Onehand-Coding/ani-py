# Termux / Android Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port Termux/Android playback from the v0.6.0 reference ZIP into the current repo without losing newer changes.

**Architecture:** Single-file stdlib app stays as-is. Add a loopback-only `AndroidMediaRelay` class plus Android branches in `Playback` and `App`. Detect Termux before desktop players.

**Tech Stack:** Python 3.10+ stdlib only, `am`/`pm` shell intents, `ThreadingHTTPServer`, stdlib `unittest`.

**Spec:** User task 2026-09-23 plus `TERMUX_PORTING_NOTES.md` and `termux-support.patch` in `ani-py-termux-reference-v0.6.0.zip` (reference only, never overwrite).

## Global Constraints

- Do NOT replace the current repo wholesale; port behavior only.
- Preserve provider system, UI, downloads, history, quality, mpv behavior, and all 85 current tests.
- Stdlib only, no new Python dependencies.
- All app logic stays in `ani_py.py`; `./ani-py` stays a thin launcher.
- Relay binds only to `127.0.0.1` with a random secret path.
- `auto` relay triggers on non-empty Referer; `always` + `--exit-after-play` fails.
- Desktop behavior remains unchanged.

## Review Focus

- Relay bound to non-loopback or with predictable path leaks provider headers.
- Relative HLS child resolved against master instead of current playlist breaks nested variants.
- `URI="..."` key/map rewritten incorrectly breaks encrypted HLS.
- Detach offered while relay required kills playback silently.
- `force-stop` hitting the wrong package kills an unrelated app.

## File Structure

- Modify `ani_py.py`: imports, `AndroidMediaRelay`, `Playback`, `App._interactive_loop`, `build_parser`.
- Create `tests/test_termux.py`: adapted relay + intent coverage.
- Modify `tests/test_cli.py`: add android option parsing only.
- Create `scripts/check-termux.sh`: live device check.
- Modify `scripts/check-tools.sh`: Termux hint.

## Tasks

- [ ] T1 Relay: add loopback-only `AndroidMediaRelay` (random secret path, 127.0.0.1 bind, Referer/User-Agent/Range forwarding, Content-Range/Accept-Ranges preserve, GET+HEAD, relative HLS rewrite, URI="..." rewrite resolved against current playlist).
- [ ] T2 Playback branches: Termux detect before desktop players, mpv-android `is.xyz.mpv` then VLC `org.videolan.vlc` via `pm path`, `-v` maps to Android VLC, ACTION_VIEW intents (video/any mpv, video/* VLC), no desktop mpv IPC for Android, Next/Prev/Replay/quality relaunch intents.
- [ ] T3 Controller lifecycle: relay keeps controller alive, hide Detach while required, Escape/cancel warns instead of exiting, Stop & quit stops relay then force-stops only selected package, exit-after-play disables auto relay with warning and fails with always.
- [ ] T4 Parser flags: `--android-player auto|mpv|vlc` + `ANI_PY_ANDROID_PLAYER`, `--android-relay auto|always|never` + `ANI_PY_ANDROID_RELAY`, skip/subtitle warnings on Android.
- [ ] T5 Tests: create `tests/test_termux.py` (auto-select, intent shapes, warnings, controller gating, loopback Referer/HLS/URI/Range/HEAD integration), extend `tests/test_cli.py` with android parse only.
- [ ] T6 Scripts and docs: create `scripts/check-termux.sh`, append Termux hint to `scripts/check-tools.sh`, document flags/limits in README and CHANGELOG.

## Verification

- [ ] Run `python3 -m unittest discover -s tests` with all suites green.
- [ ] Run `./ani-py --help` and `./ani-py --version` smoke checks.
- [ ] Run `./scripts/check-tools.sh` for external tool status.

## Non-goals

- No wholesale replacement of the current repo from the reference ZIP.
- No new Python dependencies; stdlib only.
- No desktop player behavior change.
- No live network checks inside unit tests.
