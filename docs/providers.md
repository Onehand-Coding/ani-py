# Providers

ani-py keeps provider-specific scraping behind adapters so provider changes do not have to leak into playback, history, or menu code.

## Current policy

| Provider | Status | Automatic by default |
|---|---|---:|
| HiAnime | primary | yes |
| AniLight | experimental / opt-in | no |
| KickAssAnime | experimental / opt-in | no |
| AniNeko | experimental / opt-in | no |
| AniKoto | experimental / opt-in | no |
| Kuhi | experimental / opt-in | no |
| AnimeKai | experimental / manual | no |

The default automatic order is:

```text
hianime
```

AniLight is implemented directly against `api.anilight.live`. Search
results use AniList ids plus the AniLight slug for provider identity, while the
watch response supplies AniLight's separate numeric id for source lookups.

The initial playback path deliberately uses AniLight's `ryu`/AnimeGG backend
through AniLight's stable API proxy. It returns progressive quality-labelled
streams and avoids the HLS segment rewriting required by several other AniLight
backends. Sub and dub are supported where `ryu` has coverage; its sub stream is
hard-subbed rather than a separate WebVTT track. If that portable source is
missing, AniLight fails cleanly so normal provider failover can continue.

AniLight remains opt-in until it has passed the project's live desktop and
Termux smoke matrix. Soft-sub/MegaPlay support and provider-native skip metadata
are not claimed by this adapter yet; `--skip` continues to use `ani-skip`.

KickAssAnime is implemented against its JSON search/show/episode endpoints and
returns direct HLS with the required stream referer plus an `Origin` header that
its segment host enforces (sent for mpv playback and downloads). AniNeko uses its public
HTML episode/server pages and extracts direct HLS from embed pages. AniKoto
uses its AJAX episode/server endpoints, preserves MAL ids for `--skip`, and
tries direct HLS/MP4 sources plus mapper-provided download links. All three are
experimental and opt-in until live provider testing is complete.

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
ani-py --provider kaa "naruto"
ani-py --provider anineko "naruto"
ani-py --provider anikoto "naruto"
ani-py --provider kuhi "frieren"
ani-py --provider-order hianime,anilight,kaa "frieren"
```

Environment overrides:

```text
ANI_PY_PROVIDER
ANI_PY_PROVIDER_ORDER
ANI_PY_ANILIGHT_URL
ANI_PY_ANILIGHT_API_URL
ANI_PY_KAA_URL
ANI_PY_KAA_HLS_URL
ANI_PY_ANINEKO_URL
ANI_PY_ANIKOTO_URL
ANI_PY_ANIKOTO_MAPPER_URL
ANI_PY_KUHI_URL
ANI_PY_ANIMEKAI_URL
```

Use `./scripts/check-providers-live.sh` for a real-network provider check before a release. Mocked parser tests are not evidence that a third-party deployment is currently reachable.
