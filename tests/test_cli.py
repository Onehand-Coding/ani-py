import os
import re
import unittest
from unittest.mock import patch

import ani_py


def _read_pyproject():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as handle:
        return handle.read()


def _pyproject_string_field(text, key):
    # Python 3.10 has no tomllib and the project stays dependency-free, so
    # scan for the single `key = "value"` line instead of parsing TOML.
    match = re.search(r'^%s = "([^"]+)"' % re.escape(key), text, re.MULTILINE)
    return match.group(1) if match else None


def _repo_path(relative):
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), relative)


def _read_repo_bytes(relative):
    with open(_repo_path(relative), "rb") as handle:
        return handle.read()


def _read_repo_text(relative):
    with open(_repo_path(relative), encoding="utf-8") as handle:
        return handle.read()


class TestCLI(unittest.TestCase):
    def test_common_options_parse(self):
        parser = ani_py.build_parser()
        ns = parser.parse_args(["--dub", "-q", "1080", "-e", "2-4", "--skip", "frieren"])
        self.assertEqual(ns.mode, "dub")
        self.assertEqual(ns.quality, "1080")
        self.assertEqual(ns.episode, "2-4")
        self.assertTrue(ns.skip)
        self.assertEqual(ns.query, ["frieren"])

    def test_environment_defaults(self):
        with patch.dict(os.environ, {
            "ANI_PY_MODE": "dub",
            "ANI_PY_QUALITY": "720",
            "ANI_PY_PLAYER": "mpv",
            "ANI_PY_SKIP_INTRO": "1",
        }, clear=False):
            parser = ani_py.build_parser()
            ns = parser.parse_args(["frieren"])
        self.assertEqual(ns.mode, "dub")
        self.assertEqual(ns.quality, "720")
        self.assertEqual(ns.player, "mpv")
        self.assertTrue(ns.skip)

    def test_provider_defaults_include_live_preflight_backup(self):
        # Local divergence from upstream: this checkout keeps the default
        # chain at hianime-only (Kuhi's public instance is undeployed).
        # Opt in via --provider-order or ANI_PY_PROVIDER_ORDER.
        parser = ani_py.build_parser()
        with patch.dict(os.environ, {}, clear=True):
            ns = parser.parse_args(["frieren"])
        self.assertEqual(ns.provider, "auto")
        self.assertEqual(ns.provider_order, "hianime")

    def test_player_option_is_unified_short_and_long(self):
        parser = ani_py.build_parser()
        ns = parser.parse_args(["-p", "vlc", "frieren"])
        self.assertEqual(ns.player, "vlc")
        ns = parser.parse_args(["--player", "mpv", "frieren"])
        self.assertEqual(ns.player, "mpv")

    def test_android_debug_flag_defaults_off_and_parses(self):
        parser = ani_py.build_parser()
        ns = parser.parse_args(["frieren"])
        self.assertFalse(ns.android_debug)
        ns = parser.parse_args(["--android-debug", "frieren"])
        self.assertTrue(ns.android_debug)

    def test_player_environment_default(self):
        with patch.dict(os.environ, {"ANI_PY_PLAYER": "vlc"}, clear=False):
            parser = ani_py.build_parser()
            ns = parser.parse_args(["frieren"])
        self.assertEqual(ns.player, "vlc")

    def test_removed_player_aliases_are_rejected(self):
        parser = ani_py.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["-v", "frieren"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["--android-player", "mpv", "frieren"])

    def test_version_is_current(self):
        self.assertEqual(ani_py.VERSION, "0.5.2-rc10")

    def test_pyproject_version_matches_ani_py(self):
        # PEP 440 has no dash: "0.5.2-rc10" is published as "0.5.2rc10".
        self.assertEqual(
            _pyproject_string_field(_read_pyproject(), "version"),
            ani_py.VERSION.replace("-", ""),
        )

    def test_pyproject_keeps_ani_py_dependency_free(self):
        # The uv manifest exists for environment + metadata only. Runtime work
        # belongs in subprocess calls, never in a dependency.
        text = _read_pyproject()
        self.assertIn("dependencies = []", text)
        self.assertIn("package = false", text)
        self.assertEqual(_pyproject_string_field(text, "requires-python"), ">=3.10")

    def test_anilight_provider_can_be_selected_explicitly(self):
        parser = ani_py.build_parser()
        ns = parser.parse_args(["--provider", "anilight", "frieren"])
        self.assertEqual(ns.provider, "anilight")

    def test_default_provider_order_is_hianime_only(self):
        # No unverified provider belongs in the default automatic chain:
        # Kuhi's public instance is gone and AnimeKai needs an explicit mirror.
        parser = ani_py.build_parser()
        ns = parser.parse_args(["frieren"])
        self.assertEqual(ns.provider, "auto")
        self.assertEqual(ns.provider_order, "hianime")
        ns = parser.parse_args(["--provider-order", "hianime,kuhi", "frieren"])
        self.assertEqual(ns.provider_order, "hianime,kuhi")

    def test_dash_flag_passthrough_needs_equals_form(self):
        # argparse treats a bare `--flag` value as another option, so the
        # documented `--opt='--flag'` form must parse for both passthroughs.
        parser = ani_py.build_parser()
        ns = parser.parse_args(["--menu-flags=--exact", "frieren"])
        self.assertEqual(ns.menu_flags, "--exact")
        ns = parser.parse_args(["--player-flag=--fs", "--player-flag=--loop", "frieren"])
        self.assertEqual(ns.player_flag, ["--fs", "--loop"])

    def test_windows_batch_files_use_crlf(self):
        # cmd.exe tolerates LF, but a stray LF inside a batch file is a
        # classic source of "the line is ignored" confusion, and
        # .gitattributes only pins CRLF at checkout time.
        for name in ("ani-py.bat", "install.bat", os.path.join("scripts", "run-tests.bat")):
            with self.subTest(script=name):
                data = _read_repo_bytes(name)
                self.assertIn(b"\r\n", data)
                self.assertNotIn(b"\n", data.replace(b"\r\n", b""))

    def test_windows_player_probe_finds_vlc_without_path(self):
        # VLC's installer never adds itself to PATH, so the Windows branch
        # must probe Program Files explicitly or `--player vlc` stays broken.
        src = _read_repo_text("ani_py.py")
        self.assertIn("def default_vlc_paths()", src)
        self.assertIn('os.environ.get("ProgramFiles")', src)
        self.assertIn('"VideoLAN" / "VLC" / "vlc.exe"', src)
        # both the auto chain and the explicit --player vlc path need it
        self.assertIn('which_first(["mpv.exe", "vlc.exe", *default_vlc_paths()])', src)
        self.assertIn("which_first([requested_player, *fallback])", src)

    def test_vlc_is_reachable_through_the_windows_probe(self):
        # Behavioural check: an explicit --player vlc must resolve to a real
        # VLC binary. Skipped off Windows and when VLC is not installed.
        if os.name != "nt":
            self.skipTest("Windows-only probe")
        vlc = [p for p in ani_py.default_vlc_paths() if os.path.exists(p)]
        if not vlc:
            self.skipTest("VLC is not installed under Program Files")
        obj = object.__new__(ani_py.Playback)
        obj.args = ani_py.build_parser().parse_args(["x"])
        obj.args.player = "vlc"
        self.assertEqual(obj._detect_player().lower(), vlc[0].lower())

    def test_install_bat_probes_uv_before_installing_an_interpreter(self):
        # `uv python install` aborts with "Executable already exists ... not
        # managed by uv" when an unmanaged shim occupies the uv bin dir, so
        # the installer must probe `uv python find` first.
        text = _read_repo_text("install.bat")
        probe = text.index("uv python find 3.12")
        install = text.index("uv python install 3.12")
        self.assertLess(probe, install, "probe must come before the install")
        self.assertIn("if not errorlevel 1 (\n  echo   a uv-managed Python 3.12 is already available", text)
        # uv itself is bootstrapped, not assumed
        self.assertIn("call :winget_one astral-sh.uv uv", text)

    def test_install_bat_never_reuses_the_tmp_env_var(self):
        # TMP/TMPDIR are standard env vars: child processes (uv, curl) treat
        # them as a temp *directory*, so reusing the name makes uv mkdir a
        # directory where the download expects a file and curl fails with 23.
        lines = _read_repo_text("install.bat").splitlines()
        checked = 0
        for line in lines:
            stripped = line.strip()
            if stripped.lower().startswith("rem") or stripped.startswith("::"):
                continue
            checked += 1
            self.assertNotRegex(stripped, r'(?i)^set\s+"?TMP=')
            self.assertNotRegex(stripped, r'(?<!_)\bTMP\b(?!DIR)')
        self.assertGreater(checked, 50, "batch body was not actually inspected")
        self.assertIn('set "ANI_PY_TMP=%TEMP%', "\n".join(lines))

    def test_install_bat_captures_script_dir_before_any_shift(self):
        # cmd rewrites %~dp0 once `shift` has run, so the launcher lookup
        # must use a value captured up front. See CONTEXT.md gotchas.
        lines = _read_repo_text("install.bat").splitlines()
        capture = next(i for i, l in enumerate(lines) if l.strip() == 'set "SCRIPT_DIR=%~dp0"')
        first_shift = next(i for i, l in enumerate(lines) if l.strip().startswith("shift"))
        self.assertLess(capture, first_shift)
        text = "\n".join(lines)
        self.assertNotIn('set "STUB_SRC=%~dp0', text)
        self.assertIn('set "STUB_SRC=%SCRIPT_DIR%ani-py.bat"', text)

    def test_batch_option_parsing_keeps_shift_inside_the_if(self):
        # `if cond set X & shift & goto :lbl` runs shift/goto unconditionally
        # because & separates top-level commands.
        for line in _read_repo_text("install.bat").splitlines():
            stripped = line.strip()
            if "shift" in stripped and stripped.startswith("if "):
                self.assertIn("(", stripped, f"unguarded shift: {stripped}")

    def test_powershell_payload_uses_single_quotes(self):
        # cmd strips doubled "" before PowerShell parses the payload, which
        # corrupts string arguments. Single quotes survive cmd untouched.
        text = _read_repo_text("install.bat")
        for line in text.splitlines():
            if line.strip().startswith("powershell ") and '-Command' in line:
                self.assertNotIn('""', line, f"doubled quotes will be stripped: {line.strip()}")

    def test_launcher_prefers_uv_then_python_then_py(self):
        # uv first so a checkout resolves through pyproject.toml/uv.lock;
        # python and the py launcher remain the fallbacks.
        text = _read_repo_text("ani-py.bat")
        uv = text.index("uv run python")
        self.assertLess(uv, text.index("python \"%ANI_PY%\""))
        self.assertNotIn("shift", text, "%~dp0 is only safe without shift")

    def test_launcher_forwards_arguments_verbatim(self):
        text = _read_repo_text("ani-py.bat")
        self.assertIn('set "ANI_PY=%~dp0ani-py"', text)
        for interpreter in ("uv run python", "python", "py -3"):
            self.assertIn(f'{interpreter} "%ANI_PY%" %*', text)


if __name__ == "__main__":
    unittest.main()
