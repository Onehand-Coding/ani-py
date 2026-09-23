import argparse
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen
from unittest.mock import patch

import ani_py


def args(**overrides):
    base = dict(
        download=False,
        player=None,
        vlc=False,
        player_flag=[],
        skip=False,
        no_detach=False,
        exit_after_play=False,
        ipc_socket=None,
        android_player="auto",
        android_relay="never",
    )
    base.update(overrides)
    return argparse.Namespace(**base)


STREAM = ani_py.Stream("1080p", "https://cdn.example/video/master.m3u8")
KW = dict(
    title="Frieren Episode 1",
    subtitle=None,
    referer="https://embed.example/",
    mal_id="52991",
    episode="1",
)


class TestTermuxPlayers(unittest.TestCase):
    @patch.object(ani_py.Playback, "_is_android_env", return_value=True)
    @patch.object(ani_py.Playback, "_android_package_installed")
    def test_auto_prefers_mpv_android(self, installed, _env):
        installed.side_effect = lambda package: package == "is.xyz.mpv"
        pb = ani_py.Playback(args())
        self.assertEqual(pb.player, "android_mpv")
        self.assertEqual(pb._android_package, "is.xyz.mpv")

    @patch.object(ani_py.Playback, "_is_android_env", return_value=True)
    @patch.object(ani_py.Playback, "_android_package_installed")
    def test_auto_falls_back_to_vlc_android(self, installed, _env):
        installed.side_effect = lambda package: package == "org.videolan.vlc"
        pb = ani_py.Playback(args())
        self.assertEqual(pb.player, "android_vlc")
        self.assertEqual(pb._android_package, "org.videolan.vlc")

    @patch.object(ani_py.Playback, "_is_android_env", return_value=True)
    @patch.object(ani_py.Playback, "_android_package_installed", return_value=True)
    def test_dash_vlc_selects_android_vlc(self, _installed, _env):
        pb = ani_py.Playback(args(vlc=True))
        self.assertEqual(pb.player, "android_vlc")

    @patch("ani_py.run_capture")
    @patch.object(ani_py.Playback, "_android_tool", return_value="/system/bin/am")
    @patch.object(ani_py.Playback, "_is_android_env", return_value=True)
    @patch.object(ani_py.Playback, "_android_package_installed", return_value=True)
    def test_mpv_android_intent(self, _installed, _env, _tool, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "Starting: Intent"
        run.return_value.stderr = ""
        pb = ani_py.Playback(args(android_player="mpv", android_relay="never"))
        self.assertEqual(pb.play(STREAM, **KW), 0)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:4], ["/system/bin/am", "start", "-a", "android.intent.action.VIEW"])
        self.assertIn("video/any", cmd)
        self.assertIn("is.xyz.mpv", cmd)
        self.assertIn(STREAM.url, cmd)
        self.assertIn("--es", cmd)
        self.assertIn("title", cmd)
        self.assertTrue(pb.active())

    @patch("ani_py.run_capture")
    @patch.object(ani_py.Playback, "_android_tool", return_value="/system/bin/am")
    @patch.object(ani_py.Playback, "_is_android_env", return_value=True)
    @patch.object(ani_py.Playback, "_android_package_installed", return_value=True)
    def test_vlc_android_intent(self, _installed, _env, _tool, run):
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        pb = ani_py.Playback(args(android_player="vlc", android_relay="never"))
        self.assertEqual(pb.play(STREAM, **KW), 0)
        cmd = run.call_args.args[0]
        self.assertIn("video/*", cmd)
        self.assertIn("org.videolan.vlc", cmd)

    @patch("ani_py.warn")
    @patch("ani_py.run_capture")
    @patch.object(ani_py.Playback, "_android_tool", return_value="/system/bin/am")
    @patch.object(ani_py.Playback, "_is_android_env", return_value=True)
    @patch.object(ani_py.Playback, "_android_package_installed", return_value=True)
    def test_skip_warns_on_android(self, _installed, _env, _tool, run, warn):
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        pb = ani_py.Playback(args(android_player="mpv", android_relay="never", skip=True))
        pb.play(STREAM, **KW)
        self.assertTrue(any("not available through Android" in c.args[0] for c in warn.call_args_list))

    @patch("ani_py.warn")
    @patch("ani_py.run_capture")
    @patch.object(ani_py.Playback, "_android_tool", return_value="/system/bin/am")
    @patch.object(ani_py.Playback, "_is_android_env", return_value=True)
    @patch.object(ani_py.Playback, "_android_package_installed", return_value=True)
    def test_external_subtitle_warns_on_android(self, _installed, _env, _tool, run, warn):
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        pb = ani_py.Playback(args(android_player="mpv", android_relay="never"))
        kw = dict(KW)
        kw["subtitle"] = "https://cdn.example/sub.vtt"
        pb.play(STREAM, **kw)
        self.assertTrue(any("subtitle" in c.args[0].lower() for c in warn.call_args_list))

    @patch.object(ani_py.Playback, "_is_android_env", return_value=True)
    @patch.object(ani_py.Playback, "_android_package_installed", return_value=True)
    def test_auto_relay_requires_controller(self, _installed, _env):
        pb = ani_py.Playback(args(android_player="mpv", android_relay="auto"))
        with patch.object(ani_py.AndroidMediaRelay, "start", autospec=True) as start:
            fake = unittest.mock.Mock()
            fake.local_url.return_value = "http://127.0.0.1:1234/token"
            start.return_value = fake
            got = pb._android_media_url(STREAM.url, KW["referer"])
        self.assertEqual(got, "http://127.0.0.1:1234/token")
        self.assertTrue(pb.requires_controller())
        self.assertFalse(pb.can_detach())


class TestAndroidRelay(unittest.TestCase):
    def test_relay_forwards_referer_and_rewrites_hls_children(self):
        seen = []

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_GET(self):
                seen.append((self.path, self.headers.get("Referer")))
                if self.path == "/master.m3u8":
                    body = b"#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=1280x720\nvariant/720.m3u8\n"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/variant/720.m3u8":
                    body = b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\nseg.ts\n'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                body = b"x"
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", "1")
                self.end_headers()
                self.wfile.write(body)

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        relay = ani_py.AndroidMediaRelay("https://embed.example/").start()
        try:
            port = upstream.server_address[1]
            master = f"http://127.0.0.1:{port}/master.m3u8"
            with urlopen(relay.local_url(master), timeout=2) as r:
                text = r.read().decode()
            child = next(line for line in text.splitlines() if line.startswith("http://127.0.0.1:"))
            with urlopen(child, timeout=2) as r:
                variant = r.read().decode()
            self.assertIn("http://127.0.0.1:", variant)
            self.assertNotIn('URI="key.bin"', variant)
            self.assertIn(("/master.m3u8", "https://embed.example/"), seen)
            self.assertIn(("/variant/720.m3u8", "https://embed.example/"), seen)
        finally:
            relay.stop()
            upstream.shutdown()
            upstream.server_close()


if __name__ == "__main__":
    unittest.main()
