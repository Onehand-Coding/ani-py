# Contributing

Thanks for looking at ani-py. A short guide to getting set up and making a
change that fits the project.

## What it is

A single-file terminal anime CLI. All application logic lives in `ani_py.py`.
Networking, downloads, and playback are handed off to tools that are probably
already on your machine: `curl`, `yt-dlp`, `mpv`, `ffmpeg` or VLC, and `fzf`.

## Requirements

- Python 3.10 or newer
- `curl` (required)
- `yt-dlp` (downloads and subtitle fetching)
- `mpv` or `VLC` (playback)
- `fzf` (menus; there is a numbered fallback if it is missing)

`scripts/check-tools.sh` reports what is present.

## Running it

```sh
./ani-py --list-providers     # see the configured providers
./ani-py "frieren"            # search, then pick from the menu
./ani-py --help               # full option list
```

## The test gate

Everything runs through one command:

```sh
make test
```

It runs four stages and all of them must pass:

1. the unittest suite
2. lint
3. CLI smoke checks
4. the standalone build

`make build` regenerates `dist/ani-py`. That file is generated, so never edit it
by hand.

## Ground rules

- **Standard library only.** No runtime Python dependencies. New capability
  comes from external tools or plain stdlib, not from installing a package.
- **No external network in unit tests.** HTTP is mocked with canned response
  bodies. The Android relay tests do make loopback calls, against a server the
  test starts itself, which is fine. Live provider checks are separate, via
  `scripts/check-providers-live.sh`.
- **Scraping stays in providers.** Each site is one provider class. The app
  layer should not know where a stream came from.
- **Match the existing style** and keep changes scoped. Unrelated reformatting
  makes review harder.

`AGENTS.md` has the full version of these rules, and is aimed at coding agents
in particular.

## Adding a provider

1. Add a provider class in `ani_py.py` implementing `search()`, `episodes()`,
   and `resolve()`. Keep every site-specific detail inside the class.
2. Register it in `App` and the CLI `--provider` choices.
3. Add `tests/test_provider_<name>.py` using mocked responses.
4. Add a live entry to `scripts/check-providers-live.sh`.
5. Update `docs/providers.md` and the provider list in `CONTEXT.md`.

A provider is only useful if it can actually resolve a stream with audio. A
site that returns a catalog but no playable source, or that resolves to a
silent video, does not count. Test it against a real episode before claiming
it works, and remove providers that stop working rather than leaving them
disabled. Two providers have already been retired this way, and the reasoning
is recorded in `CONTEXT.md`.

## Project memory

`CONTEXT.md` records architectural decisions, conventions, and gotchas, along
with the reasoning behind them. It is not a history of changes; that lives in
git.

If your change alters how the project is built, or teaches you a gotcha worth
passing on, add a short note to the relevant `CONTEXT.md` section. Keep it
brief and update only what your change affects.
