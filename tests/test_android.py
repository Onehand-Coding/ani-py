import argparse
import http.server
import re
import subprocess
import threading
import unittest
import urllib.request
from typing import Any
from unittest.mock import patch

import ani_py


def args(**overrides):
    base = dict(
        download=False,
        player=None,
        vlc=False,
        android_player="auto",
        player_flag=[],
        skip=False,
        no_detach=False,
        exit_after_play=False,
        ipc_socket=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


STREAM = ani_py.Stream("1080p", "https://cdn.example/video.m3u8")
KW: dict[str, Any] = dict(
    title="Frieren Episode 1",
    subtitle="https://cdn.example/sub.vtt",
    referer="https://embed.example/",
    mal_id="52991",
    episode="1",
)


class TestAndroidIntent(unittest.TestCase):
    @patch("ani_py.is_android_environment", return_value=True)
    def test_vlc_flag_selects_android_vlc_without_desktop_binary_probe(self, _android):
        pb = ani_py.Playback(args(vlc=True))
        self.assertEqual(pb.player, "android_vlc")

    @patch("ani_py.is_android_environment", return_value=True)
    def test_default_android_mode_uses_auto_intent_dispatch(self, _android):
        pb = ani_py.Playback(args())
        self.assertEqual(pb.player, "android_auto")

    @patch("ani_py.is_android_environment", return_value=True)
    def test_vlc_intent_uses_package_and_subtitle_extra(self, _android):
        pb = ani_py.Playback(args(android_player="vlc"))
        cmd = pb._android_intent("vlc", "http://127.0.0.1:123/video", "Title", "http://127.0.0.1:123/sub")
        self.assertIn("org.videolan.vlc", cmd)
        self.assertIn("video/*", cmd)
        self.assertIn("subtitles_location", cmd)
        self.assertIn("Title", cmd)

    @patch("ani_py.is_android_environment", return_value=True)
    def test_mpv_intent_matches_official_package_contract(self, _android):
        pb = ani_py.Playback(args(android_player="mpv"))
        cmd = pb._android_intent("mpv", "https://example.invalid/master", "Title", None)
        self.assertIn("is.xyz.mpv", cmd)
        self.assertIn("video/any", cmd)
        self.assertNotIn("subtitles_location", cmd)

    @patch("ani_py.warn")
    @patch("ani_py.run_capture")
    @patch("ani_py.shutil.which")
    @patch.object(ani_py.Playback, "_find_rish", return_value="/data/data/com.termux/files/home/rish")
    @patch("ani_py.is_android_environment", return_value=True)
    def test_rish_is_only_used_after_direct_am_failure(self, _android, _rish, which, run, warn):
        pb = ani_py.Playback(args(android_player="vlc"))
        which.side_effect = lambda name: "/data/data/com.termux/files/usr/bin/am" if name == "am" else None
        run.side_effect = [
            subprocess.CompletedProcess([], 1, "", "Failure calling service"),
            subprocess.CompletedProcess([], 0, "Starting: Intent", ""),
        ]
        ok = pb._run_android_intent(pb._android_intent("vlc", STREAM.url, "Title", None))
        self.assertTrue(ok)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0][0], "/data/data/com.termux/files/usr/bin/am")
        self.assertEqual(run.call_args_list[1].args[0][:2], ["/data/data/com.termux/files/home/rish", "-c"])
        self.assertIn("Shizuku", warn.call_args.args[0])

    @patch("ani_py.run_capture")
    @patch("ani_py.shutil.which", return_value="/data/data/com.termux/files/usr/bin/am")
    @patch.object(ani_py.Playback, "_find_rish")
    @patch("ani_py.is_android_environment", return_value=True)
    def test_direct_am_success_does_not_touch_rish(self, _android, rish, _which, run):
        run.return_value = subprocess.CompletedProcess([], 0, "Starting: Intent", "")
        pb = ani_py.Playback(args(android_player="vlc"))
        self.assertTrue(pb._run_android_intent(pb._android_intent("vlc", STREAM.url, "Title", None)))
        rish.assert_not_called()

    @patch.object(ani_py.Playback, "_start_android_relay", return_value=None)
    @patch.object(ani_py.Playback, "_android_chooser", return_value=True)
    @patch.object(ani_py.Playback, "_run_android_intent", return_value=False)
    @patch("ani_py.is_android_environment", return_value=True)
    def test_explicit_player_falls_back_to_normal_android_chooser(self, _android, _intent, chooser, _relay):
        pb = ani_py.Playback(args(android_player="vlc"))
        self.assertEqual(pb.play(STREAM, **KW), 0)
        chooser.assert_called_once_with(STREAM.url)


class TestAndroidRelay(unittest.TestCase):
    def test_hls_rewrite_routes_segments_and_key_uris_through_relay(self):
        source = "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"key.bin\"\n#EXTINF:5,\nseg-1.ts\n"
        out = ani_py._rewrite_hls_manifest(source, "https://cdn.example/path/master.m3u8", lambda u: "LOCAL:" + u)
        self.assertIn('URI="LOCAL:https://cdn.example/path/key.bin"', out)
        self.assertIn("LOCAL:https://cdn.example/path/seg-1.ts", out)

    def test_relay_injects_referer_and_rewrites_nested_hls(self):
        expected_ref = "https://embed.example/"

        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: object, **kwargs: object) -> None:
                pass

            def do_GET(self):
                if self.headers.get("Referer") != expected_ref:
                    self.send_response(403)
                    self.end_headers()
                    return
                if self.path == "/master.m3u8":
                    body = b"#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=1280x720\nchild.m3u8\n"
                    ctype = "application/vnd.apple.mpegurl"
                elif self.path == "/child.m3u8":
                    body = b"#EXTM3U\n#EXTINF:5,\nseg.ts\n"
                    ctype = "application/vnd.apple.mpegurl"
                elif self.path == "/seg.ts":
                    body = b"video-bytes"
                    ctype = "video/mp2t"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        relay = ani_py._AndroidRelayHTTPServer(
            ("127.0.0.1", 0), ani_py._AndroidRelayHandler,
            token="token", referer=expected_ref, user_agent="ani-py-test",
        )
        t1 = threading.Thread(target=upstream.serve_forever, daemon=True)
        t2 = threading.Thread(target=relay.serve_forever, daemon=True)
        t1.start(); t2.start()
        try:
            upstream_url = f"http://127.0.0.1:{upstream.server_address[1]}/master.m3u8"
            master = urllib.request.urlopen(relay.relay_url(upstream_url), timeout=3).read().decode()
            child = next(line for line in master.splitlines() if line and not line.startswith("#"))
            self.assertTrue(child.startswith("http://127.0.0.1:"))
            child_body = urllib.request.urlopen(child, timeout=3).read().decode()
            segment = next(line for line in child_body.splitlines() if line and not line.startswith("#"))
            data = urllib.request.urlopen(segment, timeout=3).read()
            self.assertEqual(data, b"video-bytes")
        finally:
            relay.shutdown(); relay.server_close()
            upstream.shutdown(); upstream.server_close()

    def test_relay_preserves_byte_range_requests_and_responses(self):
        expected_ref = "https://embed.example/"
        payload = b"0123456789abcdef"
        seen = {}

        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: object, **kwargs: object) -> None:
                pass

            def do_GET(self):
                seen["referer"] = self.headers.get("Referer")
                seen["range"] = self.headers.get("Range")
                if self.headers.get("Referer") != expected_ref:
                    self.send_response(403)
                    self.end_headers()
                    return
                start = 0
                status = 200
                extra = {}
                if self.headers.get("Range", "").startswith("bytes="):
                    try:
                        start = int(self.headers["Range"].split("=", 1)[1].split("-", 1)[0])
                    except ValueError:
                        start = 0
                    status = 206
                    extra["Content-Range"] = f"bytes {start}-{len(payload) - 1}/{len(payload)}"
                body = payload[start:]
                self.send_response(status)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Accept-Ranges", "bytes")
                for key, value in extra.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        relay = ani_py._AndroidRelayHTTPServer(
            ("127.0.0.1", 0), ani_py._AndroidRelayHandler,
            token="token", referer=expected_ref, user_agent="ani-py-test",
        )
        t1 = threading.Thread(target=upstream.serve_forever, daemon=True)
        t2 = threading.Thread(target=relay.serve_forever, daemon=True)
        t1.start(); t2.start()
        try:
            target = f"http://127.0.0.1:{upstream.server_address[1]}/seg.ts"
            req = urllib.request.Request(relay.relay_url(target), headers={"Range": "bytes=4-"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                self.assertEqual(resp.status, 206)
                self.assertEqual(resp.headers.get("Content-Range"), f"bytes 4-{len(payload) - 1}/{len(payload)}")
                self.assertEqual(resp.headers.get("Accept-Ranges"), "bytes")
                self.assertEqual(resp.read(), payload[4:])
            self.assertEqual(seen["referer"], expected_ref)
            self.assertEqual(seen["range"], "bytes=4-")
        finally:
            relay.shutdown(); relay.server_close()
            upstream.shutdown(); upstream.server_close()


if __name__ == "__main__":
    unittest.main()
