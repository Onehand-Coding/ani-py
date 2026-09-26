# Providers

ani-py keeps provider-specific scraping behind adapters so provider changes do not have to leak into playback, history, or menu code.

## Current policy

| Provider | Status | Automatic by default |
|---|---|---:|
| HiAnime | primary | yes |
| AniLight | experimental / opt-in | no |
| Kuhi | experimental / opt-in | no |
| AnimeKai | experimental / manual | no |

The default automatic order is:

```text
hianime
```

AniLight is implemented directly against `api.anilight.live` and MegaPlay.
Search results use AniList ids plus the AniLight slug for provider identity.
Episode resolution supports sub/dub, direct HLS, WebVTT subtitle tracks, MAL
metadata where available, and provider-supplied intro/outro timestamps. The HLS
and subtitle requests use the MegaPlay Referer, so the same Android loopback
relay used by HiAnime can carry the stream without a separate browser backend.

AniLight remains opt-in until it has passed the project's live desktop and
Termux smoke matrix. Provider-native skip timestamps are preserved in
`StreamBundle`; `--skip` still uses `ani-skip` for playback at this stage.

Kuhi remains available for explicit use or a user-configured failover order, but a configured endpoint must pass a media extraction preflight before automatic use. The previously used public Kuhi deployment has been unreliable/undeployed.

AnimeKai has no trusted hardcoded default domain. A compatible mirror must be supplied explicitly:

```sh
ANI_PY_ANIMEKAI_URL=https://your-mirror.example \
  ani-py --provider animekai "frieren"
```

## Commands

```sh
ani-py --list-providers
ani-py --provider hianime "frieren"
ani-py --provider anilight "frieren"
ani-py --provider kuhi "frieren"
ani-py --provider-order hianime,anilight "frieren"
```

Environment overrides:

```text
ANI_PY_PROVIDER
ANI_PY_PROVIDER_ORDER
ANI_PY_ANILIGHT_URL
ANI_PY_ANILIGHT_API_URL
ANI_PY_KUHI_URL
ANI_PY_ANIMEKAI_URL
```

Use `./scripts/check-providers-live.sh` for a real-network provider check before a release. Mocked parser tests are not evidence that a third-party deployment is currently reachable.
