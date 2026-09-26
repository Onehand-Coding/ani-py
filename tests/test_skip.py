import argparse
import subprocess
import unittest
from unittest.mock import patch

import ani_py


def args(**overrides):
    base = dict(
        download=False,
        player="mpv",
        player_flag=[],
        skip=True,
        no_detach=False,
        exit_after_play=False,
        ipc_socket=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class TestAniSkip(unittest.TestCase):
    def make_playback(self, **overrides):
        with patch("ani_py.which_first", return_value="/usr/bin/mpv"):
            return ani_py.Playback(args(**overrides))

    @patch("ani_py.run_capture")
    @patch("ani_py.shutil.which", return_value="/usr/bin/ani-skip")
    def test_skip_uses_direct_mal_id_and_parses_mpv_flags(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="--chapters-file=/tmp/chapters --script-opts=skip-op_start=12.5,skip-op_end=101.2\n",
            stderr="",
        )
        pb = self.make_playback()
        result = pb._skip_args("52299", "5")

        mock_run.assert_called_once_with(["/usr/bin/ani-skip", "-i", "52299", "-e", "5"])
        self.assertEqual(
            result,
            ["--chapters-file=/tmp/chapters", "--script-opts=skip-op_start=12.5,skip-op_end=101.2"],
        )

    @patch("ani_py.warn")
    def test_skip_warns_when_mal_id_is_missing(self, mock_warn):
        pb = self.make_playback()
        self.assertEqual(pb._skip_args(None, "5"), [])
        mock_warn.assert_called_once()
        self.assertIn("MAL id", mock_warn.call_args.args[0])

    @patch("ani_py.warn")
    @patch("ani_py.shutil.which", return_value=None)
    def test_skip_warns_when_binary_is_missing(self, mock_which, mock_warn):
        pb = self.make_playback()
        self.assertEqual(pb._skip_args("52299", "5"), [])
        self.assertIn("not installed", mock_warn.call_args.args[0])

    @patch("ani_py.warn")
    @patch("ani_py.run_capture")
    @patch("ani_py.shutil.which", return_value="/usr/bin/ani-skip")
    def test_skip_failure_is_not_silent(self, mock_which, mock_run, mock_warn):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="provider error"
        )
        pb = self.make_playback()
        self.assertEqual(pb._skip_args("52299", "5"), [])
        self.assertIn("provider error", mock_warn.call_args.args[0])

    @patch("ani_py.warn")
    @patch("ani_py.run_capture")
    @patch("ani_py.shutil.which", return_value="/usr/bin/ani-skip")
    def test_skip_empty_output_warns(self, mock_which, mock_run, mock_warn):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="\n", stderr=""
        )
        pb = self.make_playback()
        self.assertEqual(pb._skip_args("52299", "5"), [])
        self.assertIn("returned no mpv flags", mock_warn.call_args.args[0])

    @patch("ani_py.run_capture")
    @patch("ani_py.shutil.which", return_value="/usr/bin/ani-skip")
    @patch.object(ani_py.Playback, "_ipc_supported", return_value=False)
    def test_skip_flags_are_injected_into_mpv_command(self, mock_ipc, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="--chapters-file=/tmp/c --script-opts=skip-op_start=10,skip-op_end=90\n",
            stderr="",
        )
        pb = self.make_playback()
        cmd = pb._mpv_command(
            ani_py.Stream("1080p", "https://cdn.example/video.m3u8"),
            title="Episode", subtitle=None, referer="https://embed.example/",
            mal_id="52299", episode="5", keep_open=True,
        )
        self.assertIn("--chapters-file=/tmp/c", cmd)
        self.assertIn("--script-opts=skip-op_start=10,skip-op_end=90", cmd)
        self.assertEqual(cmd[-1], "https://cdn.example/video.m3u8")


if __name__ == "__main__":
    unittest.main()
