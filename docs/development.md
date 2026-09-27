# Development

ani-py keeps the runtime Python implementation standard-library-only. External programs such as curl, fzf, mpv, VLC, yt-dlp, ffmpeg, and ani-skip are invoked as subprocesses.

## Environment

Development uses [uv](https://docs.astral.sh/uv/). `pyproject.toml` carries
metadata, `requires-python = ">=3.10"`, and an intentionally empty
`dependencies` list. `[tool.uv] package = false` marks ani-py as a script
project, so uv never tries to build or install a wheel — the shippable
artifacts are the `ani-py` launcher and `dist/ani-py`.

```sh
uv sync
uv run python -m unittest discover -s tests
```

Adding a Python dependency is a project-level decision, not a convenience: it
contradicts the stdlib-only rule in `CONTEXT.md`. Reach for a subprocess call
instead. `tests/test_cli.py` asserts the manifest stays dependency-free and
that its version tracks `ani_py.VERSION`.

## Tests

```sh
./scripts/run-tests.sh
# or
make test
```

On Windows, where `make` and bash are unavailable:

```bat
scripts\run-tests.bat
```

Both entry points run the same four stages: compile, unit tests, help/version
smoke check, standalone build. The batch file drives each stage through `uv
run` and invokes the CLI as `uv run python ani-py`, because the `ani-py`
launcher is a POSIX shebang script that `cmd.exe` cannot spawn directly.
`scripts/*.bat` is checked in with CRLF endings via `.gitattributes`.

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
