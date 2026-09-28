# AGENTS.md

Rules for coding agents working in this repository. Read `CONTEXT.md` for why
the project is built this way. This file says what is not negotiable.

## What this is

A single-file terminal anime CLI. All app logic lives in `ani_py.py`. It
searches, streams, and downloads anime using the tools already on the user's
machine.

## Non-negotiables

**Standard library only.** No runtime Python dependencies, and none should be
added. Networking, downloads, and playback are delegated to external binaries
(`curl`, `yt-dlp`, `mpv`, `ffmpeg`/`VLC`, `fzf`). If something seems to need a
library, it probably needs an external tool or a few lines of stdlib instead.

**The test gate must pass.** `make test` runs four stages: unit tests, lint,
CLI smoke checks, and the standalone build. All four have to pass before a
change is done. `make build` produces `dist/ani-py`; never hand-edit that file.

**No external network in unit tests.** HTTP is mocked with canned bodies. A
test that reaches the public internet is slow, flaky, and will break when an
upstream site changes. Loopback traffic against a server the test starts
itself is fine, and the Android relay tests do exactly that. Live provider
checks are a separate concern, handled by `scripts/check-providers-live.sh`,
which is not part of `make test`.

**Scraping stays in provider adapters.** Each site is one provider class. Do not
leak provider-specific markup, headers, or endpoints into the app layer.
`ProviderManager` and `App` must not know which site a stream came from.

**Match the surrounding style.** Existing code uses 4-space indents, plain
stdlib types (`Optional`, not `X | None`, outside of annotations), and small
single-purpose helpers. New behavior gets a unittest alongside the code.

## CONTEXT.md handling

`CONTEXT.md` is durable project memory, not a changelog and not a session log.

Update it when a change alters architecture, a decision, a convention, a
gotcha, or a known limitation. Update only the affected sections. Leave
unrelated sections alone.

Things that belong in `CONTEXT.md`:

- Architectural decisions, with the reason and the alternatives that were
  rejected
- Conventions that are not obvious from reading the code
- Gotchas that cost real debugging time, especially network and player quirks
- Known limitations, stated plainly

Things that do not belong:

- Session logs, commit narratives, or "what I did today"
- Restatements of what git already shows
- Duplicated user-facing feature lists

A useful test before you add something: would this still be true and useful to
a new contributor in six months, with no memory of the change? If not, leave it
out.

## Before you finish

- `make test` passes, all four stages
- `CONTEXT.md` reflects the change if it touched architecture or conventions
- New behavior has a test
- No unrelated reformatting crept in
