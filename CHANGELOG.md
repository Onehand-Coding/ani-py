# Changelog

## Unreleased (0.5.2-rc11)

- Added direct experimental KickAssAnime, AniNeko, and AniKoto providers. KAA uses JSON episode APIs and HLS manifests; AniNeko resolves HLS from its server embeds; AniKoto uses AJAX server metadata with direct HLS/MP4 and optional mapper download fallbacks. All remain opt-in while live smoke testing is pending.

- Updated `--skip` integration for current `ani-skip` by passing provider-supplied MAL ids through the direct `-i/--id` interface instead of the legacy numeric `-q/--query` compatibility path.

- Added an experimental direct AniLight provider using its current JSON API:
  AniList/slug identity, AniLight numeric source ids, sub/dub episode mapping,
  MAL ids where exposed, and the portable `ryu`/AnimeGG progressive source
  through AniLight's own proxy. Broader soft-sub/HLS backends are deferred
  until their current CDN/proxy behavior is live-tested with ani-py. AniLight
  remains opt-in and is not in the default automatic chain yet.
- Added `Search another anime` to the interactive playback menu. The current player stays running while a new title and episode are selected, then playback is replaced in the same ani-py session.
- Kept the default automatic provider order at `hianime` only; Kuhi and
  AnimeKai remain experimental opt-in providers.
- Android subtitles use the loopback HLS relay as a native subtitle rendition.
  The injected group now uses a collision-resistant `ani-py-subs` id, leaves
  existing upstream subtitle topology untouched, and carries language/label
  metadata only when the provider actually supplies it.
- Removed automatic shared-storage subtitle staging. VLC still receives its
  `subtitles_location` URL fallback, while both VLC and mpv-android can use
  the relayed WebVTT rendition without leaving per-episode files behind.
- Removed the temporary `--android-vlc-explicit` A/B diagnostic; retained
  `--android-debug` for sanitized intent/relay troubleshooting.
- Unified player selection under `-p/--player` on desktop and Android:
  `-p vlc`, `-p mpv`, and `-p auto` now mean the same thing across
  platforms. Removed the older `-v/--vlc`, `--android-player`, and
  `ANI_PY_ANDROID_PLAYER` interfaces so there is one player contract.
- Fixed Python 3.10/3.11 compatibility in the IINA subtitle argument path and
  fixed Android tests so clean CI runners do not require desktop media players.
- Added `install.sh` / `uninstall.sh` with Linux/Unix and Termux defaults;
  dependency installation is opt-in via `--deps`.
- Trimmed the README and moved Android/provider/development internals into
  `docs/android.md`, `docs/providers.md`, and `docs/development.md`.
- Live-device status carried forward from rc6: VLC 3.7.1 and mpv-android both
  played the relayed stream with subtitles on the project owner's Termux
  device. The tested VLC setup needed `--sub-language=eng` once for English
  auto-selection; this is documented as a player/device observation, not a
  universal VLC requirement.

## 0.5.1 - 2026-09-24

- Reworked Termux/Android playback around Android `ACTION_VIEW` intents instead of package-manager preflight checks. `pm path` is no longer required.
- Added `--android-player auto|vlc|mpv` and `ANI_PY_ANDROID_PLAYER`; `-v/--vlc` selects VLC for Android when running under Termux.
- `auto` uses Android's normal resolver/default-player path, while explicit VLC/mpv modes pin `org.videolan.vlc` or `is.xyz.mpv`.
- Added a stdlib-only localhost media relay for Android. It injects provider Referer/User-Agent headers, forwards byte ranges, and rewrites nested HLS playlists, key URIs, and segment URLs through the relay.
- Added VLC `subtitles_location` intent forwarding.
- Added normal Termux chooser fallback and optional `rish`/Shizuku targeted retry. Shizuku is never a dependency.
- Relay processes are detached with an idle timeout so `Detach & exit` can leave Android playback running.
- Removed the earlier `--android-relay auto|always|never` opt-in; the relay is now automatic with raw-URL fallback when it cannot start.
- Added Android/relay regression coverage, including a real localhost fixture that rejects requests without the expected Referer. Test suite now has 96 tests.

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
