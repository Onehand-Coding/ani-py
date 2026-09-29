import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ani_py


class TestCLI(unittest.TestCase):
    def test_common_options_parse(self):
        parser = ani_py.build_parser()
        ns = parser.parse_args(["--dub", "-q", "1080", "-e", "2-4", "--skip", "frieren"])
        self.assertEqual(ns.mode, "dub")
        self.assertEqual(ns.quality, "1080")
        self.assertEqual(ns.episode, "2-4")
        self.assertTrue(ns.skip)
        self.assertEqual(ns.query, ["frieren"])

        sub_ns = parser.parse_args(["--sub-lang", "de", "frieren"])
        self.assertEqual(sub_ns.sub_lang, "de")
        attach_ns = parser.parse_args(["--attach"])
        self.assertTrue(attach_ns.attach)

    def test_environment_defaults(self):
        with patch.dict(os.environ, {
            "ANI_PY_MODE": "dub",
            "ANI_PY_QUALITY": "720",
            "ANI_PY_PLAYER": "mpv",
            "ANI_PY_SKIP_INTRO": "1",
            "ANI_PY_SUB_LANG": "de",
        }, clear=False):
            parser = ani_py.build_parser()
            ns = parser.parse_args(["frieren"])
        self.assertEqual(ns.mode, "dub")
        self.assertEqual(ns.quality, "720")
        self.assertEqual(ns.player, "mpv")
        self.assertTrue(ns.skip)
        self.assertEqual(ns.sub_lang, "de")

    def test_provider_defaults_include_live_preflight_backup(self):
        # The default chain stays hianime-only: no unverified provider
        # belongs in the automatic path. Opt in via --provider-order or
        # ANI_PY_PROVIDER_ORDER.
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

    def test_version_is_a_calendar_version(self):
        # CalVer (YYYY.M.D) is the project convention. This fails loudly if a
        # semver or prerelease string is reintroduced. See CONTRIBUTING.md.
        self.assertRegex(ani_py.VERSION, r"^\d{4}\.\d{1,2}\.\d{1,2}$")

    def test_anilight_provider_can_be_selected_explicitly(self):
        parser = ani_py.build_parser()
        ns = parser.parse_args(["--provider", "anilight", "frieren"])
        self.assertEqual(ns.provider, "anilight")

    def test_default_provider_order_is_hianime_only(self):
        # No unverified provider belongs in the default automatic chain.
        parser = ani_py.build_parser()
        ns = parser.parse_args(["frieren"])
        self.assertEqual(ns.provider, "auto")
        self.assertEqual(ns.provider_order, "hianime")
        ns = parser.parse_args(["--provider-order", "hianime,anilight", "frieren"])
        self.assertEqual(ns.provider_order, "hianime,anilight")

    def test_dash_flag_passthrough_needs_equals_form(self):
        # argparse treats a bare `--flag` value as another option, so the
        # documented `--opt='--flag'` form must parse for both passthroughs.
        parser = ani_py.build_parser()
        ns = parser.parse_args(["--menu-flags=--exact", "frieren"])
        self.assertEqual(ns.menu_flags, "--exact")
        ns = parser.parse_args(["--player-flag=--fs", "--player-flag=--loop", "frieren"])
        self.assertEqual(ns.player_flag, ["--fs", "--loop"])


class FakeUpdateHttp:
    """Minimal stand-in for HttpClient.get on the update path."""

    def __init__(self, body):
        self.body = body
        self.calls = []

    def get(self, url, *, referer=None, headers=None, timeout=15, cookie_jar=None):
        self.calls.append((url, timeout))
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


LOCAL_COPY = b"#!/usr/bin/env python3\nVERSION = '2026.9.29'\n"
REMOTE_COPY = b"#!/usr/bin/env python3\nVERSION = '2026.9.30'\n"


class TestUpdateCommand(unittest.TestCase):
    """`--update` self-replaces the running script from main."""

    def _target(self, tmp, content=LOCAL_COPY):
        path = Path(tmp) / "ani_py.py"
        path.write_bytes(content)
        return path

    def _run(self, http, target):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = ani_py.run_update(http=http, target=target)
        return code, buffer.getvalue()

    def test_update_flag_parses_in_both_forms(self):
        parser = ani_py.build_parser()
        self.assertTrue(parser.parse_args(["-U"]).update)
        self.assertTrue(parser.parse_args(["--update"]).update)
        # It must not collide with the free-form search query.
        self.assertFalse(parser.parse_args(["update"]).update)

    def test_matching_copy_is_reported_as_up_to_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            code, output = self._run(
                FakeUpdateHttp(LOCAL_COPY.decode()), target
            )
            self.assertEqual(code, 0)
            self.assertIn("up to date", output)
            self.assertEqual(target.read_bytes(), LOCAL_COPY)

    def test_newer_copy_replaces_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            code, output = self._run(
                FakeUpdateHttp(REMOTE_COPY.decode()), target
            )
            self.assertEqual(code, 0)
            self.assertEqual(target.read_bytes(), REMOTE_COPY)
            self.assertIn(str(target), output)

    def test_replacement_stays_executable_and_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            self._run(FakeUpdateHttp(REMOTE_COPY.decode()), target)
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)
            self.assertEqual(
                sorted(p.name for p in Path(tmp).iterdir()), ["ani_py.py"]
            )

    def test_older_remote_version_is_refused(self):
        # A dev checkout ahead of main must not be silently rolled back.
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            body = "VERSION = '2026.9.28'\n"
            code, output = self._run(FakeUpdateHttp(body), target)
            self.assertEqual(code, 1)
            self.assertEqual(target.read_bytes(), LOCAL_COPY)
            self.assertIn("older", output)

    def test_same_version_different_content_still_updates(self):
        # A code-only change on main keeps the version string; that must update.
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            body = LOCAL_COPY.decode() + "# a later commit\n"
            code, _ = self._run(FakeUpdateHttp(body), target)
            self.assertEqual(code, 0)
            self.assertEqual(target.read_bytes(), body.encode())

    def test_calendar_versions_sort_chronologically(self):
        def key(version: str) -> tuple:
            parsed = ani_py._version_key(version)
            assert parsed is not None, version
            return parsed

        self.assertLess(key("2026.9.28"), key("2026.9.29"))
        # Month rollover must not degrade into a string comparison.
        self.assertLess(key("2026.9.9"), key("2026.10.1"))
        self.assertLess(key("2026.12.31"), key("2027.1.1"))
        # Legacy semver must still parse, or the guard silently disables itself
        # against every copy installed before the CalVer switch.
        self.assertEqual(key("0.5.2-rc11"), (0, 5, 2))
        self.assertEqual(key("0.5.2"), (0, 5, 2))
        self.assertLess(key("0.5.2-rc11"), key("2026.9.29"))
        # A version with no leading digits disables the guard rather than raising.
        self.assertIsNone(ani_py._version_key("not-a-version"))

    def test_legacy_install_updates_onto_calendar_version(self):
        # The transition that every currently-installed user hits: their copy
        # still carries legacy semver, and main is a CalVer date. Must update.
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(
                tmp, b"#!/usr/bin/env python3\nVERSION = '0.5.2-rc11'\n"
            )
            body = "#!/usr/bin/env python3\nVERSION = '2026.9.29'\n"
            code, _ = self._run(FakeUpdateHttp(body), target)
            self.assertEqual(code, 0)
            self.assertEqual(target.read_bytes(), body.encode())

    def test_non_script_payload_is_refused(self):
        # A 200 HTML error page must never overwrite a working install.
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            code, output = self._run(
                FakeUpdateHttp("<html>nope</html>"), target
            )
            self.assertEqual(code, 1)
            self.assertEqual(target.read_bytes(), LOCAL_COPY)
            self.assertIn("does not look like", output)

    def test_permission_error_advises_sudo(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            with patch.object(
                ani_py.os, "replace", side_effect=PermissionError("denied")
            ):
                code, output = self._run(
                    FakeUpdateHttp(REMOTE_COPY.decode()), target
                )
            self.assertEqual(code, 1)
            self.assertEqual(target.read_bytes(), LOCAL_COPY)
            self.assertIn("sudo", output)

    def test_network_failure_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            code, output = self._run(
                FakeUpdateHttp(ani_py.HttpError("network unreachable")), target
            )
            self.assertEqual(code, 1)
            self.assertEqual(target.read_bytes(), LOCAL_COPY)
            self.assertIn("network unreachable", output)

    def test_update_fetches_from_the_main_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self._target(tmp)
            http = FakeUpdateHttp(REMOTE_COPY.decode())
            self._run(http, target)
            url = http.calls[0][0]
            self.assertIn("Onehand-Coding/ani-py", url)
            self.assertIn("/main/ani_py.py", url)


if __name__ == "__main__":
    unittest.main()
