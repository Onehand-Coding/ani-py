# Providers

ani-py keeps provider-specific scraping behind adapters so provider changes do not have to leak into playback, history, or menu code.

## Current policy

| Provider | Status | Automatic by default |
|---|---|---:|
| HiAnime | primary | yes |
| AniLight | experimental / opt-in | no |

## Retired providers

Kuhi and AnimeKai were removed in 0.5.2-rc11 after their upstreams went away.
The Kuhi public deployment stopped resolving, AnimeKai lost its last trusted
domain, and neither could return a stream. Both are gone rather than left
disabled: a provider that cannot resolve costs failover time and implies a
safety net that is not there. Re-adding one is a normal provider contribution
once a live source has been verified end to end.

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

## Commands

```sh
ani-py --list-providers
ani-py --provider hianime "frieren"
ani-py --provider anilight "frieren"
ani-py --provider-order hianime,anilight "frieren"
```

Environment overrides:

```text
ANI_PY_PROVIDER
ANI_PY_PROVIDER_ORDER
ANI_PY_ANILIGHT_URL
ANI_PY_ANILIGHT_API_URL
```

Use `./scripts/check-providers-live.sh` for a real-network provider check before a release. Mocked parser tests are not evidence that a third-party deployment is currently reachable.
