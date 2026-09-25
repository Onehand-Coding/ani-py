# Providers

ani-py keeps provider-specific scraping behind adapters so provider changes do not have to leak into playback, history, or menu code.

## Current policy

| Provider | Status | Automatic by default |
|---|---|---:|
| HiAnime | primary | yes |
| Kuhi | experimental / opt-in | no |
| AnimeKai | experimental / manual | no |

The default automatic order is:

```text
hianime
```

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
ani-py --provider kuhi "frieren"
ani-py --provider-order hianime,kuhi "frieren"
```

Environment overrides:

```text
ANI_PY_PROVIDER
ANI_PY_PROVIDER_ORDER
ANI_PY_KUHI_URL
ANI_PY_ANIMEKAI_URL
```

Use `./scripts/check-providers-live.sh` for a real-network provider check before a release. Mocked parser tests are not evidence that a third-party deployment is currently reachable.
