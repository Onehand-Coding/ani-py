import os
import unittest
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

    def test_android_options_parse(self):
        parser = ani_py.build_parser()
        ns = parser.parse_args(["--android-player", "vlc", "frieren"])
        self.assertEqual(ns.android_player, "vlc")

    def test_android_debug_flag_defaults_off_and_parses(self):
        parser = ani_py.build_parser()
        ns = parser.parse_args(["frieren"])
        self.assertFalse(ns.android_debug)
        self.assertFalse(ns.android_vlc_explicit)
        ns = parser.parse_args(["--android-debug", "frieren"])
        self.assertTrue(ns.android_debug)
        ns = parser.parse_args(["--android-vlc-explicit", "frieren"])
        self.assertTrue(ns.android_vlc_explicit)

    def test_android_player_option_and_environment_default(self):
        with patch.dict(os.environ, {"ANI_PY_ANDROID_PLAYER": "vlc"}, clear=False):
            parser = ani_py.build_parser()
            ns = parser.parse_args(["frieren"])
        self.assertEqual(ns.android_player, "vlc")

        parser = ani_py.build_parser()
        ns = parser.parse_args(["--android-player", "mpv", "frieren"])
        self.assertEqual(ns.android_player, "mpv")

    def test_version_is_current(self):
        self.assertEqual(ani_py.VERSION, "0.5.2-rc6")

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


if __name__ == "__main__":
    unittest.main()
