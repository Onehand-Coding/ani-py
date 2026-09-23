# Changelog

## Unreleased (local checkout)

- Default automatic provider order is `hianime` only. Kuhi and AnimeKai stay
  experimental opt-in until a live instance is confirmed from this network
  (Kuhi's public instance is currently undeployed; AnimeKai has no trusted
  default domain).
- `--list-providers` tags experimental providers; automatic paths preflight
  them before use.
- Documents `--opt='--flag'` equals form for `--menu-flags`/`--player-flag`
  (argparse won't take flag-like values positionally) with regression tests.

## 0.5.0 - 2026-09-22

- Replaced AnimeKai in the default automatic failover chain with **Kuhi**. Default order is now `hianime,kuhi`.
- Added `KuhiProvider`, using AniList IDs for search/identity, merged provider episode lists, sub/dub extraction, direct HLS/MP4/DASH streams, subtitles, referer headers, quality labels, HLS master expansion, and MAL metadata where available.
- Kuhi automatic failover is protected by a **deep media preflight**: a known episode must resolve to a direct HTTP(S) stream before the provider is considered available. The result is memoized per run.
- Added `ANI_PY_KUHI_URL` and `--provider kuhi`; `--provider-order` now defaults to `hianime,kuhi`.
- Kept AnimeKai registered only as a manual experimental provider; it is not used by default.
- Expanded the regression suite to **77 tests**, including Kuhi search, episodes, stream extraction, embed rejection, HLS variant expansion, live-preflight semantics, and new CLI defaults.
- Added `scripts/check-providers-live.sh` for a real-network Kuhi extraction check before releases.
- Documented that the default public Kuhi instance may rate-limit/cold-start and can be replaced with a self-hosted deployment.

## 0.4.0 - 2026-09-22

- Refactored source handling behind a provider contract and `ProviderManager`.
- Added AnimeKai as the first backup provider.
- Added `--provider auto|hianime|animekai`, `--provider-order`, and `--list-providers`.
- Added automatic fallback for search, episode-list, and stream-resolution failures.
- Added conservative cross-provider title matching: exact/high-confidence matches can auto-map; ambiguous matches require user selection.
- Made anime/history identities provider-aware; legacy three-column HiAnime history is migrated transparently on read.
- Added AnimeKai AJAX search, `syncData` anime-id extraction, episode-token loading, sub/dub/softsub server separation, direct source resolution, subtitle/quality extraction, and MAL-id discovery where available.
- Kept the standalone/no-pip design: AnimeKai uses curl plus the public enc-dec.app AnimeKai token/decryption endpoints.
- Added provider/source display to playback status and deduplicated quality choices.
- Added mocked regression coverage for AnimeKai and provider failover.

## 0.3.1 - 2026-09-22

- Fixed `--skip` against `ani-skip` 1.0.1 by using the documented `-q/--query` flag directly.
- Added a regression test that verifies the exact `ani-skip -q <MAL_ID> -e <episode>` invocation.
- Added a regression test proving successful ani-skip output is injected into the generated mpv command.
- Kept visible warnings for missing MAL ids, missing `ani-skip`, command failures, and empty output.
- Re-ran the full external-integration mock suite covering downloads, fzf/rofi/dmenu, mpv IPC, VLC, IINA, custom players, history, ranges, provider parsing, HTTP handling, and CLI options.

## 0.3.0 - 2026-09-22

- Fixed `--skip` compatibility across `ani-skip` variants:
  - prefer `-i/--id` for direct MAL id input
  - fall back to `-q/--query` when an installed variant requires it
  - surface missing-id, missing-binary, failed-command, and empty-output warnings
- Hardened downloads:
  - resolved `yt-dlp` / `ffmpeg` executable paths
  - pass referer and user-agent headers
  - report subtitle failures while continuing valid video downloads
  - keep `yt-dlp` preferred with ffmpeg stream-copy fallback
- Fixed multi-episode/range playback so episodes run sequentially instead of immediately replacing one another.
- Added explicit warnings when `--skip` is requested with VLC, IINA, or another non-mpv player.
- Expanded the test harness to 56 unit/integration-style tests covering CLI options, provider fixtures, menus, players, IPC, skip behavior, downloads, ranges, history, and error paths.
- Added `scripts/check-tools.sh` and a standalone build target.
- Updated the README UI image and documentation to match the current fzf-oriented interface more closely.
