import argparse
import subprocess
import unittest
from unittest.mock import Mock, patch

import ani_py


def args(**overrides):
    base = dict(
        download=False,
        player=None,
        player_flag=[],
        skip=False,
        no_detach=False,
        exit_after_play=False,
        ipc_socket=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


STREAM = ani_py.Stream("1080p", "https://cdn.example/video.m3u8")
KW = dict(
    title="Frieren Episode 1",
    subtitle="https://cdn.example/sub.vtt",
    referer="https://embed.example/",
    mal_id="52991",
    episode="1",
)


class TestPlayers(unittest.TestCase):
    @patch("ani_py.subprocess.Popen")
    @patch("ani_py.which_first", return_value="/usr/bin/vlc")
    def test_vlc_command(self, mock_which, mock_popen):
        proc = Mock()
        proc.poll.return_value = None
        mock_popen.return_value = proc
        pb = ani_py.Playback(args(player="vlc"))
        rc = pb.play(STREAM, **KW)
        self.assertEqual(rc, 0)
        cmd = mock_popen.call_args.args[0]
        self.assertEqual(cmd[0], "/usr/bin/vlc")
        self.assertIn("--http-referrer=https://embed.example/", cmd)
        self.assertIn("--play-and-exit", cmd)
        self.assertIn(":input-slave=https://cdn.example/sub.vtt", cmd)

    @patch("ani_py.subprocess.Popen")
    @patch("ani_py.which_first", return_value="/Applications/IINA.app/Contents/MacOS/iina-cli")
    def test_iina_command(self, mock_which, mock_popen):
        proc = Mock()
        proc.poll.return_value = None
        mock_popen.return_value = proc
        pb = ani_py.Playback(args(player="iina"))
        rc = pb.play(STREAM, **KW)
        self.assertEqual(rc, 0)
        cmd = mock_popen.call_args.args[0]
        self.assertEqual(cmd[0], "/Applications/IINA.app/Contents/MacOS/iina-cli")
        self.assertIn("--mpv-referrer=https://embed.example/", cmd)
        self.assertTrue(any(x.startswith("--mpv-sub-files=") for x in cmd))

    @patch("ani_py.subprocess.Popen")
    @patch("ani_py.which_first", return_value="/opt/bin/my-player")
    def test_custom_player_command_and_flags(self, mock_which, mock_popen):
        proc = Mock()
        proc.poll.return_value = None
        mock_popen.return_value = proc
        pb = ani_py.Playback(args(player="my-player", player_flag=["--foo"]))
        rc = pb.play(STREAM, **KW)
        self.assertEqual(rc, 0)
        cmd = mock_popen.call_args.args[0]
        self.assertEqual(cmd, ["/opt/bin/my-player", "--foo", STREAM.url])

    @patch("ani_py.subprocess.run")
    @patch.object(ani_py.Playback, "_ipc_supported", return_value=False)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_mpv_foreground_range_disables_keep_open(self, mock_which, mock_ipc, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        pb = ani_py.Playback(args(player="mpv"))
        rc = pb.play(STREAM, foreground=True, keep_open=False, **KW)
        self.assertEqual(rc, 0)
        cmd = mock_run.call_args.args[0]
        self.assertIn("--keep-open=no", cmd)
        self.assertIn("--referrer=https://embed.example/", cmd)
        self.assertIn("--sub-file=https://cdn.example/sub.vtt", cmd)

    @patch("ani_py.warn")
    @patch("ani_py.subprocess.Popen")
    @patch("ani_py.which_first", return_value="/usr/bin/vlc")
    def test_skip_with_vlc_warns_instead_of_silently_ignoring(self, mock_which, mock_popen, mock_warn):
        proc = Mock()
        proc.poll.return_value = None
        mock_popen.return_value = proc
        pb = ani_py.Playback(args(player="vlc", skip=True))
        pb.play(STREAM, **KW)
        self.assertIn("only with mpv", mock_warn.call_args.args[0])

    @patch.object(ani_py.Playback, "active", return_value=True)
    @patch.object(ani_py.Playback, "_wait_path", return_value=True)
    @patch.object(ani_py.Playback, "_ipc")
    @patch.object(ani_py.Playback, "_ipc_supported", return_value=True)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_mpv_replace_uses_ipc_not_second_process(self, mock_which, mock_supported, mock_ipc, mock_wait, mock_active):
        pb = ani_py.Playback(args(player="mpv"))
        pb.ipc_path = ani_py.Path("/tmp/test.sock")
        rc = pb.replace(STREAM, **KW)
        self.assertEqual(rc, 0)
        commands = [call.args[0] for call in mock_ipc.call_args_list]
        self.assertIn(["loadfile", STREAM.url, "replace"], commands)
        self.assertIn(["sub-add", KW["subtitle"], "select"], commands)

    @patch.object(ani_py.Playback, "active", return_value=True)
    @patch.object(ani_py.Playback, "_ipc")
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_replay_seeks_current_mpv(self, mock_which, mock_ipc, mock_active):
        pb = ani_py.Playback(args(player="mpv"))
        pb.ipc_path = ani_py.Path("/tmp/test.sock")
        self.assertTrue(pb.replay())
        commands = [call.args[0] for call in mock_ipc.call_args_list]
        self.assertEqual(commands[0], ["seek", 0, "absolute"])
        self.assertEqual(commands[1], ["set_property", "pause", False])

    @patch("ani_py.subprocess.run")
    @patch.object(ani_py.Playback, "_ipc_supported", return_value=False)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_no_detach_runs_player_synchronously(self, mock_which, mock_ipc, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        pb = ani_py.Playback(args(player="mpv", no_detach=True))
        rc = pb.play(STREAM, **KW)
        self.assertEqual(rc, 0)
        self.assertIn("--keep-open=yes", mock_run.call_args.args[0])

    @patch("ani_py.subprocess.run")
    @patch.object(ani_py.Playback, "_ipc_supported", return_value=False)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_exit_after_play_runs_synchronously(self, mock_which, mock_ipc, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=7)
        pb = ani_py.Playback(args(player="mpv", exit_after_play=True))
        self.assertEqual(pb.play(STREAM, **KW), 7)


if __name__ == "__main__":
    unittest.main()
