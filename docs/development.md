# Development

ani-py keeps the runtime Python implementation standard-library-only. External programs such as curl, fzf, mpv, VLC, yt-dlp, ffmpeg, and ani-skip are invoked as subprocesses.

## Tests

```sh
./scripts/run-tests.sh
# or
make test
```

CI runs compile, unit/integration-style tests, and the standalone build on Python 3.10 through 3.13.

The automated suite mocks external player/download commands and includes localhost HTTP fixtures for provider and Android relay contracts. It does not replace real-machine acceptance testing for changing providers and third-party players.

## Build

```sh
make build
./dist/ani-py -V
```

The standalone artifact is the contents of `ani_py.py` copied to `dist/ani-py` and marked executable.

## Release checklist

Before a stable release:

1. CI is green on every supported Python version.
2. `make test` and `make build` pass locally.
3. Desktop mpv playback and private IPC are checked.
4. VLC desktop playback/subtitles are checked.
5. Termux VLC and mpv-android playback/subtitles are checked on a real device.
6. Provider live checks are treated separately from mocked parser tests.
7. Version, changelog, README claims, and installer behavior agree.

## Project notes

`CONTEXT.md` is maintainer-oriented project memory. `CHANGELOG.md` records release history. Detailed provider and Android behavior lives in the neighboring docs rather than the README.
