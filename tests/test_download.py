import argparse
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import ani_py


def args(**overrides):
    base = dict(
        download=True,
        player=None,
        vlc=False,
        player_flag=[],
        skip=False,
        no_detach=False,
        exit_after_play=False,
        ipc_socket=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class TestDownload(unittest.TestCase):
    def make_playback(self):
        return ani_py.Playback(args())

    @patch("ani_py.subprocess.run")
    @patch("ani_py.shutil.which")
    def test_prefers_yt_dlp_and_passes_headers(self, mock_which, mock_run):
        mock_which.side_effect = lambda name: {
            "yt-dlp": "/opt/bin/yt-dlp",
            "ffmpeg": "/usr/bin/ffmpeg",
        }.get(name)
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        pb = self.make_playback()
        stream = ani_py.Stream("1080p", "https://cdn.example/1080.m3u8")

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"ANI_PY_DOWNLOAD_DIR": td}, clear=False):
            rc = pb.download(stream, title="A: Test/Episode?", subtitle=None, referer="https://embed.example/")
            self.assertEqual(rc, 0)
            cmd = mock_run.call_args.args[0]
            self.assertEqual(cmd[0], "/opt/bin/yt-dlp")
            self.assertIn("--referer", cmd)
            self.assertIn("https://embed.example/", cmd)
            self.assertIn("--user-agent", cmd)
            self.assertIn(ani_py.USER_AGENT, cmd)
            self.assertEqual(cmd[-1], stream.url)
            out = cmd[cmd.index("-o") + 1]
            self.assertTrue(out.startswith(td))
            self.assertTrue(out.endswith("A_ Test_Episode_.mp4"))

    @patch("ani_py.subprocess.run")
    @patch("ani_py.shutil.which")
    def test_falls_back_to_ffmpeg(self, mock_which, mock_run):
        mock_which.side_effect = lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        pb = self.make_playback()
        stream = ani_py.Stream("720p", "https://cdn.example/720.m3u8")

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"ANI_PY_DOWNLOAD_DIR": td}, clear=False):
            rc = pb.download(stream, title="Episode 1", subtitle=None, referer="https://embed.example/")
            self.assertEqual(rc, 0)
            cmd = mock_run.call_args.args[0]
            self.assertEqual(cmd[0], "/usr/bin/ffmpeg")
            self.assertIn("-extension_picky", cmd)
            self.assertIn("-referer", cmd)
            self.assertIn("-user_agent", cmd)
            self.assertIn("-c", cmd)
            self.assertIn("copy", cmd)
            self.assertEqual(cmd[-2], "copy")
            self.assertTrue(cmd[-1].endswith("Episode 1.mp4"))

    @patch("ani_py.HttpClient")
    @patch("ani_py.subprocess.run")
    @patch("ani_py.shutil.which", return_value="/opt/bin/yt-dlp")
    def test_subtitle_download_uses_curl_and_referer(self, mock_which, mock_run, mock_http):
        mock_http.return_value.exe = "/opt/bin/curl"
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0),
            subprocess.CompletedProcess(args=[], returncode=0),
        ]
        pb = self.make_playback()
        stream = ani_py.Stream("1080p", "https://cdn.example/video.m3u8")

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"ANI_PY_DOWNLOAD_DIR": td}, clear=False):
            rc = pb.download(
                stream,
                title="Episode 1",
                subtitle="https://cdn.example/sub.vtt",
                referer="https://embed.example/",
            )
            self.assertEqual(rc, 0)
            sub_cmd = mock_run.call_args_list[0].args[0]
            self.assertEqual(sub_cmd[0], "/opt/bin/curl")
            self.assertIn("--fail", sub_cmd)
            self.assertIn("https://embed.example/", sub_cmd)
            self.assertIn("https://cdn.example/sub.vtt", sub_cmd)
            self.assertTrue(sub_cmd[-1].endswith("Episode 1.vtt"))

    @patch("ani_py.warn")
    @patch("ani_py.HttpClient")
    @patch("ani_py.subprocess.run")
    @patch("ani_py.shutil.which", return_value="/opt/bin/yt-dlp")
    def test_subtitle_failure_warns_but_video_still_runs(self, mock_which, mock_run, mock_http, mock_warn):
        mock_http.return_value.exe = "/opt/bin/curl"
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=22),
            subprocess.CompletedProcess(args=[], returncode=0),
        ]
        pb = self.make_playback()
        stream = ani_py.Stream("1080p", "https://cdn.example/video.m3u8")

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"ANI_PY_DOWNLOAD_DIR": td}, clear=False):
            rc = pb.download(stream, title="Episode 1", subtitle="https://bad/sub.vtt", referer="https://embed/")
        self.assertEqual(rc, 0)
        self.assertEqual(mock_run.call_count, 2)
        self.assertIn("Subtitle download failed", mock_warn.call_args.args[0])

    @patch("ani_py.shutil.which", return_value=None)
    def test_missing_download_backend_fails_cleanly(self, mock_which):
        pb = self.make_playback()
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"ANI_PY_DOWNLOAD_DIR": td}, clear=False):
            with patch("ani_py.sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
                pb.download(
                    ani_py.Stream("720p", "https://cdn.example/video.m3u8"),
                    title="Episode 1",
                    subtitle=None,
                    referer="https://embed/",
                )


if __name__ == "__main__":
    unittest.main()
