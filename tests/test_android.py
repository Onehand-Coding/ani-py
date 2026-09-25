import argparse
import http.server
import io
import json
from contextlib import redirect_stderr
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from unittest.mock import patch

import ani_py


def args(**overrides):
    base = dict(
        download=False,
        player=None,
        android_debug=False,
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
    def test_unified_player_selects_android_vlc_without_desktop_binary_probe(self, _android):
        pb = ani_py.Playback(args(player="vlc"))
        self.assertEqual(pb.player, "android_vlc")

    @patch("ani_py.is_android_environment", return_value=True)
    def test_default_android_mode_uses_auto_intent_dispatch(self, _android):
        pb = ani_py.Playback(args())
        self.assertEqual(pb.player, "android_auto")

    @patch("ani_py.is_android_environment", return_value=True)
    def test_legacy_android_player_env_remains_compatible(self, _android):
        with patch.dict(ani_py.os.environ, {"ANI_PY_ANDROID_PLAYER": "mpv"}, clear=False):
            pb = ani_py.Playback(args())
        self.assertEqual(pb.player, "android_mpv")

    @patch("ani_py.is_android_environment", return_value=True)
    def test_vlc_intent_uses_package_and_subtitle_extra(self, _android):
        pb = ani_py.Playback(args(player="vlc"))
        cmd = pb._android_intent("vlc", "http://127.0.0.1:123/video", "Title", "http://127.0.0.1:123/sub")
        self.assertIn("org.videolan.vlc", cmd)
        self.assertIn("video/*", cmd)
        self.assertIn("subtitles_location", cmd)
        self.assertIn("Title", cmd)

    @patch("ani_py.is_android_environment", return_value=True)
    def test_mpv_intent_matches_official_package_contract(self, _android):
        pb = ani_py.Playback(args(player="mpv"))
        cmd = pb._android_intent("mpv", "https://example.invalid/master", "Title", None)
        self.assertIn("is.xyz.mpv", cmd)
        self.assertIn("video/any", cmd)
        self.assertNotIn("subtitles_location", cmd)

    @patch("ani_py.is_android_environment", return_value=True)
    def test_intent_debug_is_sanitized(self, _android):
        pb = ani_py.Playback(args(player="vlc", android_debug=True))
        cmd = pb._android_intent(
            "vlc",
            "http://127.0.0.1:43210/secret-token/video",
            "Title",
            "http://127.0.0.1:43210/secret-token/subtitle/encoded.vtt",
        )
        output = io.StringIO()
        with redirect_stderr(output):
            pb._print_android_intent_debug(
                cmd,
                "http://127.0.0.1:43210/secret-token/subtitle/encoded.vtt",
                ".vtt",
            )
        text = output.getvalue()
        self.assertIn("target: VLC package", text)
        self.assertIn("subtitle extra: present", text)
        self.assertIn("subtitle extra scheme: http", text)
        self.assertIn("subtitle suffix: .vtt", text)
        self.assertNotIn("secret-token", text)
        self.assertNotIn("127.0.0.1", text)

    @patch.object(ani_py.Playback, "_find_rish", return_value=None)
    @patch("ani_py.run_capture")
    @patch("ani_py.shutil.which", return_value="/data/data/com.termux/files/usr/bin/am")
    @patch("ani_py.is_android_environment", return_value=True)
    def test_intent_result_debug_reports_return_code_without_url(self, _android, _which, run_capture, _rish):
        run_capture.return_value = subprocess.CompletedProcess(
            [], 0,
            "Starting: Intent { act=android.intent.action.VIEW dat=http://127.0.0.1:43210/secret-token/video }",
            "",
        )
        pb = ani_py.Playback(args(player="vlc", android_debug=True))
        output = io.StringIO()
        with redirect_stderr(output):
            ok = pb._run_android_intent(pb._android_intent(
                "vlc", "http://127.0.0.1:43210/secret-token/video", "Title", None,
            ))
        text = output.getvalue()
        self.assertTrue(ok)
        self.assertIn("Android intent result:", text)
        self.assertIn("return code: 0", text)
        self.assertIn("Starting: Intent", text)
        self.assertNotIn("secret-token", text)
        self.assertNotIn("127.0.0.1", text)

    @patch("ani_py.warn")
    @patch("ani_py.run_capture")
    @patch("ani_py.shutil.which")
    @patch.object(ani_py.Playback, "_find_rish", return_value="/data/data/com.termux/files/home/rish")
    @patch("ani_py.is_android_environment", return_value=True)
    def test_rish_is_only_used_after_direct_am_failure(self, _android, _rish, which, run, warn):
        pb = ani_py.Playback(args(player="vlc"))
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
        pb = ani_py.Playback(args(player="vlc"))
        self.assertTrue(pb._run_android_intent(pb._android_intent("vlc", STREAM.url, "Title", None)))
        rish.assert_not_called()

    @patch.object(ani_py.Playback, "_start_android_relay", return_value=None)
    @patch.object(ani_py.Playback, "_android_chooser", return_value=True)
    @patch.object(ani_py.Playback, "_run_android_intent", return_value=False)
    @patch("ani_py.is_android_environment", return_value=True)
    def test_explicit_player_falls_back_to_normal_android_chooser(self, _android, _intent, chooser, _relay):
        pb = ani_py.Playback(args(player="vlc"))
        self.assertEqual(pb.play(STREAM, **KW), 0)
        chooser.assert_called_once_with(STREAM.url)


    @patch.object(ani_py.Playback, "_start_android_relay", return_value=None)
    @patch.object(ani_py.Playback, "_android_chooser", return_value=True)
    @patch.object(ani_py.Playback, "_run_android_intent", return_value=False)
    @patch("ani_py.is_android_environment", return_value=True)
    def test_play_android_gives_relay_original_subtitle(self, _android, _intent, chooser, start):
        pb = ani_py.Playback(args(player="vlc"))
        self.assertEqual(pb.play(STREAM, **KW), 0)
        start.assert_called_once_with(
            KW["referer"],
            KW["subtitle"],
            subtitle_language=None,
            subtitle_label=None,
        )


class TestAndroidRelay(unittest.TestCase):
    def test_hls_rewrite_routes_segments_and_key_uris_through_relay(self):
        source = "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"key.bin\"\n#EXTINF:5,\nseg-1.ts\n"
        out = ani_py._rewrite_hls_manifest(source, "https://cdn.example/path/master.m3u8", lambda u: "LOCAL:" + u)
        self.assertIn('URI="LOCAL:https://cdn.example/path/key.bin"', out)
        self.assertIn("LOCAL:https://cdn.example/path/seg-1.ts", out)

    def test_hls_rewrite_embeds_subtitle_rendition_in_master_playlist(self):
        source = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720\n"
            "child.m3u8\n"
        )
        subtitle_url = "http://127.0.0.1:43210/token/subtitle/encoded.vtt"
        out = ani_py._rewrite_hls_manifest(
            source,
            "https://cdn.example/path/master.m3u8",
            lambda u: "LOCAL:" + u,
            subtitle_url=subtitle_url,
        )
        self.assertIn('#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="ani-py-subs"', out)
        self.assertIn('URI="http://127.0.0.1:43210/token/subtitle/encoded.vtt"', out)
        self.assertIn('SUBTITLES="ani-py-subs"', out)
        self.assertNotIn("LANGUAGE=", out)

    def test_hls_rewrite_uses_known_subtitle_metadata(self):
        source = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=1000000\n"
            "child.m3u8\n"
        )
        out = ani_py._rewrite_hls_manifest(
            source,
            "https://cdn.example/master.m3u8",
            lambda u: "LOCAL:" + u,
            subtitle_url="http://127.0.0.1/sub.vtt",
            subtitle_language="es",
            subtitle_label="Spanish",
        )
        self.assertIn('NAME="Spanish"', out)
        self.assertIn('LANGUAGE="es"', out)
        self.assertIn('GROUP-ID="ani-py-subs"', out)

    def test_hls_rewrite_preserves_existing_upstream_subtitle_topology(self):
        source = (
            "#EXTM3U\n"
            '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="upstream",NAME="English",'
            'LANGUAGE="en",URI="existing.m3u8"\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=1000000,SUBTITLES="upstream"\n'
            "child.m3u8\n"
        )
        out = ani_py._rewrite_hls_manifest(
            source,
            "https://cdn.example/master.m3u8",
            lambda u: "LOCAL:" + u,
            subtitle_url="http://127.0.0.1/ani.vtt",
        )
        self.assertIn('SUBTITLES="upstream"', out)
        self.assertNotIn('SUBTITLES="ani-py-subs"', out)
        self.assertNotIn('GROUP-ID="ani-py-subs"', out)
        self.assertIn('URI="LOCAL:https://cdn.example/existing.m3u8"', out)

    def test_hls_rewrite_does_not_add_subtitle_group_to_media_playlist(self):
        source = "#EXTM3U\n#EXTINF:5,\nseg-1.ts\n"
        out = ani_py._rewrite_hls_manifest(
            source,
            "https://cdn.example/path/child.m3u8",
            lambda u: "LOCAL:" + u,
            subtitle_url="http://127.0.0.1:43210/token/subtitle/encoded.vtt",
        )
        self.assertNotIn("SUBTITLES=", out)
        self.assertIn("LOCAL:https://cdn.example/path/seg-1.ts", out)

    def test_relay_wraps_media_playlist_with_subtitle_master(self):
        payload = b"WEBVTT\n\n00:00:01.000 --> 00:00:05.000\nwrapped subtitle\n"
        seen = {}

        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: object, **kwargs: object) -> None:
                pass

            def do_GET(self):
                seen["referer"] = self.headers.get("Referer")
                if self.path == "/index.m3u8":
                    body = b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:5,\nseg.ts\n"
                    content_type = "application/vnd.apple.mpegurl"
                elif self.path == "/sub.vtt":
                    body = payload
                    content_type = "application/octet-stream"
                elif self.path == "/seg.ts":
                    body = b"video-bytes"
                    content_type = "video/mp2t"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        self.addCleanup(upstream.shutdown)
        self.addCleanup(upstream.server_close)
        upstream_base = f"http://127.0.0.1:{upstream.server_address[1]}"
        relay = ani_py._AndroidRelayHTTPServer(
            ("127.0.0.1", 0), ani_py._AndroidRelayHandler,
            token="token", referer="https://embed.example/", user_agent="ani-py-test",
            subtitle_target=upstream_base + "/sub.vtt",
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        self.addCleanup(relay.shutdown)
        self.addCleanup(relay.server_close)
        try:
            video_url = relay.relay_url(upstream_base + "/index.m3u8")
            master = urllib.request.urlopen(video_url, timeout=3).read().decode()
            self.assertIn('#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="ani-py-subs"', master)
            self.assertIn('SUBTITLES="ani-py-subs"', master)
            variant_line = next(
                line for line in master.splitlines()
                if line.startswith("http://127.0.0.1:")
            )
            self.assertTrue(variant_line.endswith("?variant=1"), variant_line)
            media = urllib.request.urlopen(variant_line, timeout=3).read().decode()
            self.assertNotIn("EXT-X-MEDIA", media)
            segment = next(line for line in media.splitlines() if line and not line.startswith("#"))
            self.assertEqual(urllib.request.urlopen(segment, timeout=3).read(), b"video-bytes")
            media_line = next(
                line for line in master.splitlines()
                if line.startswith("#EXT-X-MEDIA:TYPE=SUBTITLES")
            )
            rendition_url = media_line.split('URI="', 1)[1].rstrip('"')
            self.assertIn("/sublist/5/", rendition_url)
            with urllib.request.urlopen(rendition_url, timeout=3) as response:
                self.assertTrue(
                    response.headers.get("Content-Type", "").startswith("application/vnd.apple.mpegurl")
                )
                sub_playlist = response.read().decode()
            self.assertIn("#EXT-X-ENDLIST", sub_playlist)
            self.assertIn("#EXTINF:5,", sub_playlist)
            sub_segment = next(
                line for line in sub_playlist.splitlines()
                if line.startswith("http://127.0.0.1:")
            )
            with urllib.request.urlopen(sub_segment, timeout=3) as response:
                self.assertEqual(response.headers.get("Content-Type"), "text/vtt; charset=utf-8")
                self.assertEqual(response.read(), payload)
            self.assertEqual(seen.get("referer"), "https://embed.example/")
        finally:
            relay.shutdown()
            relay.server_close()
            upstream.shutdown()
            upstream.server_close()

    def test_hls_media_duration_sums_extinf_entries(self):
        text = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:5.5,\na.ts\n#EXTINF:4.5,\nb.ts\n"
        self.assertEqual(ani_py._hls_media_duration(text), 10)
        self.assertEqual(ani_py._hls_media_duration("#EXTM3U\n#EXTINF:5,\n"), 5)
        self.assertIsNone(ani_py._hls_media_duration("not a playlist"))
        self.assertIsNone(ani_py._hls_media_duration(""))

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

    def test_relay_embeds_configured_subtitle_in_master_playlist(self):
        payload = b"WEBVTT\n\n00:00:01.000 --> 00:00:05.000\nembedded subtitle\n"
        seen = {}

        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: object, **kwargs: object) -> None:
                pass

            def do_GET(self):
                seen["referer"] = self.headers.get("Referer")
                if self.path == "/master.m3u8":
                    body = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\nchild.m3u8\n"
                    content_type = "application/vnd.apple.mpegurl"
                elif self.path == "/child.m3u8":
                    body = b"#EXTM3U\n#EXTINF:5,\nseg.ts\n"
                    content_type = "application/vnd.apple.mpegurl"
                elif self.path == "/sub.vtt":
                    body = payload
                    content_type = "application/octet-stream"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        self.addCleanup(upstream.shutdown)
        self.addCleanup(upstream.server_close)
        upstream_base = f"http://127.0.0.1:{upstream.server_address[1]}"
        relay = ani_py._AndroidRelayHTTPServer(
            ("127.0.0.1", 0), ani_py._AndroidRelayHandler,
            token="token", referer="https://embed.example/", user_agent="ani-py-test",
            subtitle_target=upstream_base + "/sub.vtt",
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        self.addCleanup(relay.shutdown)
        self.addCleanup(relay.server_close)
        try:
            master = urllib.request.urlopen(
                relay.relay_url(upstream_base + "/master.m3u8"), timeout=3
            ).read().decode()
            media_line = next(
                line for line in master.splitlines()
                if line.startswith("#EXT-X-MEDIA:TYPE=SUBTITLES")
            )
            self.assertIn('SUBTITLES="ani-py-subs"', master)
            subtitle_url = media_line.split('URI="', 1)[1].rstrip('"')
            with urllib.request.urlopen(subtitle_url, timeout=3) as response:
                self.assertEqual(response.headers.get("Content-Type"), "text/vtt; charset=utf-8")
                self.assertEqual(response.read(), payload)
            self.assertEqual(seen.get("referer"), "https://embed.example/")
        finally:
            relay.shutdown()
            relay.server_close()
            upstream.shutdown()
            upstream.server_close()

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

    def test_debug_log_proves_lifecycle_requests_and_redacts_signed_urls(self):
        webvtt = b"WEBVTT\n\n00:00:01.000 --> 00:00:05.000\nlogged subtitle\n"

        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: object, **kwargs: object) -> None:
                pass

            def do_GET(self):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/master.m3u8":
                    body = b"#EXTM3U\n#EXTINF:5,\n"
                    content_type = "application/vnd.apple.mpegurl"
                    status = 200
                elif path == "/sub.vtt":
                    body = webvtt
                    content_type = "application/octet-stream"
                    status = 200
                else:
                    body = b""
                    content_type = "text/plain"
                    status = 404
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        self.addCleanup(upstream.server_close)
        self.addCleanup(upstream.shutdown)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            ready_path = root / "ready.json"
            log_path = root / "android-relay.log"
            config_path.write_text(json.dumps({
                "ready": str(ready_path),
                "token": "relay-secret-token",
                "referer": "https://embed.example/",
                "user_agent": "ani-py-test",
                "idle_timeout": 60,
                "debug": True,
                "log_file": str(log_path),
            }), encoding="utf-8")
            proc = subprocess.Popen(
                [sys.executable, str(Path(ani_py.__file__)), "--_android-relay-config", str(config_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            def stop_relay() -> None:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=2)

            self.addCleanup(stop_relay)
            self.assertIsNotNone(proc.stderr)
            if proc.stderr is not None:
                self.addCleanup(proc.stderr.close)
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and not ready_path.exists():
                returncode = proc.poll()
                if returncode is not None:
                    stderr = proc.stderr.read() if proc.stderr else ""
                    self.fail(f"relay exited before readiness: {returncode}; {stderr}")
                time.sleep(0.02)
            self.assertTrue(ready_path.exists(), "relay did not publish readiness")

            ready = json.loads(ready_path.read_text(encoding="utf-8"))
            endpoint = ani_py.AndroidRelayEndpoint(port=int(ready["port"]), token=ready["token"])
            upstream_base = f"http://127.0.0.1:{upstream.server_address[1]}"
            signed_subtitle = upstream_base + "/sub.vtt?token=signed-secret-value"
            with urllib.request.urlopen(endpoint.url_for(upstream_base + "/master.m3u8"), timeout=3):
                pass
            with urllib.request.urlopen(endpoint.subtitle_url_for(signed_subtitle), timeout=3) as response:
                self.assertEqual(response.headers.get("Content-Type"), "text/vtt; charset=utf-8")
            try:
                urllib.request.urlopen(endpoint.url_for(upstream_base + "/missing.ts"), timeout=3)
            except urllib.error.HTTPError as exc:
                exc.close()
            else:
                self.fail("missing upstream resource should fail through relay")

            proc.terminate()
            proc.wait(timeout=4)
            log = log_path.read_text(encoding="utf-8")

        self.assertIn("[android-relay] started", log)
        self.assertIn("[android-relay] video GET hls -> 200 application/vnd.apple.mpegurl", log)
        self.assertIn("[android-relay] subtitle GET .vtt -> 200 text/vtt", log)
        self.assertIn("[android-relay] video GET segment -> 404", log)
        self.assertIn("[android-relay] stopped", log)
        self.assertNotIn(signed_subtitle, log)
        self.assertNotIn("signed-secret-value", log)
        self.assertNotIn("relay-secret-token", log)


class TestAndroidSubtitleRelay(unittest.TestCase):
    WEBVTT = b"WEBVTT\n\n00:00:01.000 --> 00:00:05.000\nani-py Android subtitle test\n"
    SRT = b"1\n00:00:01,000 --> 00:00:05,000\nani-py Android subtitle test\n"

    def _run_servers(self, upstream_handler):
        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), upstream_handler)
        relay = ani_py._AndroidRelayHTTPServer(
            ("127.0.0.1", 0), ani_py._AndroidRelayHandler,
            token="token", referer="https://embed.example/", user_agent="ani-py-test",
        )
        t1 = threading.Thread(target=upstream.serve_forever, daemon=True)
        t2 = threading.Thread(target=relay.serve_forever, daemon=True)
        t1.start(); t2.start()
        self.addCleanup(relay.shutdown)
        self.addCleanup(relay.server_close)
        self.addCleanup(upstream.shutdown)
        self.addCleanup(upstream.server_close)
        return upstream, relay

    def test_a_subtitle_url_for_ends_with_vtt(self):
        endpoint = ani_py.AndroidRelayEndpoint(port=43210, token="tok")
        url = endpoint.subtitle_url_for("https://cdn.example/subtitles/en.vtt?token=abc")
        self.assertTrue(url.endswith(".vtt"), url)
        self.assertIn("/subtitle/", url)

    def test_a_subtitle_url_for_defaults_to_vtt_without_extension(self):
        endpoint = ani_py.AndroidRelayEndpoint(port=43210, token="tok")
        url = endpoint.subtitle_url_for("https://cdn.example/sub?id=123")
        self.assertTrue(url.endswith(".vtt"), url)

    def test_a_subtitle_url_for_preserves_srt_ass_ssa(self):
        endpoint = ani_py.AndroidRelayEndpoint(port=43210, token="tok")
        for suffix in (".srt", ".ass", ".ssa"):
            with self.subTest(suffix=suffix):
                url = endpoint.subtitle_url_for(f"https://cdn.example/sub{suffix}")
                self.assertTrue(url.endswith(suffix), url)

    def test_b_subtitle_relay_referer_mime_and_body(self):
        payload = self.WEBVTT
        seen = {}

        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: object, **kwargs: object) -> None:
                pass

            def do_GET(self):
                seen["referer"] = self.headers.get("Referer")
                if self.path != "/sub.vtt":
                    self.send_response(404)
                    self.end_headers()
                    return
                if self.headers.get("Referer") != "https://embed.example/":
                    self.send_response(403)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/vtt; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        upstream, relay = self._run_servers(Upstream)
        endpoint = ani_py.AndroidRelayEndpoint(port=relay.server_address[1], token="token")
        upstream_url = f"http://127.0.0.1:{upstream.server_address[1]}/sub.vtt"
        relay_url = endpoint.subtitle_url_for(upstream_url)
        self.assertTrue(relay_url.endswith(".vtt"), relay_url)
        with urllib.request.urlopen(relay_url, timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.headers.get("Content-Type", "").startswith("text/vtt"))
            self.assertEqual(resp.read(), payload)
        self.assertEqual(seen.get("referer"), "https://embed.example/")

    def test_c_subtitle_relay_forces_vtt_mime_on_octet_stream(self):
        payload = self.WEBVTT

        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: object, **kwargs: object) -> None:
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        upstream, relay = self._run_servers(Upstream)
        endpoint = ani_py.AndroidRelayEndpoint(port=relay.server_address[1], token="token")
        upstream_url = f"http://127.0.0.1:{upstream.server_address[1]}/sub.vtt"
        with urllib.request.urlopen(endpoint.subtitle_url_for(upstream_url), timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "text/vtt; charset=utf-8")
            self.assertEqual(resp.read(), payload)

    def test_d_srt_mime_mapping(self):
        payload = self.SRT

        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: object, **kwargs: object) -> None:
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        upstream, relay = self._run_servers(Upstream)
        endpoint = ani_py.AndroidRelayEndpoint(port=relay.server_address[1], token="token")
        upstream_url = f"http://127.0.0.1:{upstream.server_address[1]}/sub.srt"
        relay_url = endpoint.subtitle_url_for(upstream_url)
        self.assertTrue(relay_url.endswith(".srt"), relay_url)
        with urllib.request.urlopen(relay_url, timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "application/x-subrip; charset=utf-8")
            self.assertEqual(resp.read(), payload)

    def test_f_malformed_subtitle_paths_do_not_crash(self):
        relay = ani_py._AndroidRelayHTTPServer(
            ("127.0.0.1", 0), ani_py._AndroidRelayHandler,
            token="token", referer="https://embed.example/", user_agent="ani-py-test",
        )
        t = threading.Thread(target=relay.serve_forever, daemon=True)
        t.start()
        self.addCleanup(relay.shutdown)
        self.addCleanup(relay.server_close)
        base = f"http://127.0.0.1:{relay.server_address[1]}"
        for bad_path in ("/token/subtitle/!!!.vtt", "/wrong-token/subtitle/abc.vtt", "/token/subtitle/abc.txt",
                           "/token/sublist/abc/!!!.vtt", "/token/sublist/10/!!!.vtt"):
            path = bad_path.replace("token", "token" if "wrong" not in bad_path else "wrong")
            code = None
            try:
                urllib.request.urlopen(base + path, timeout=3)
            except urllib.error.HTTPError as exc:
                code = exc.code
                exc.close()
            self.assertIn(code, (403, 404), path)

    @patch("ani_py.is_android_environment", return_value=True)
    def test_vlc_intent_subtitle_relay_url_ends_with_extension(self, _android):
        endpoint = ani_py.AndroidRelayEndpoint(port=43210, token="tok")
        subtitle_url = endpoint.subtitle_url_for("https://cdn.example/sub.vtt")
        pb = ani_py.Playback(args(player="vlc"))
        cmd = pb._android_intent("vlc", "http://127.0.0.1:43210/video", "Title", subtitle_url)
        idx = cmd.index("subtitles_location")
        self.assertTrue(cmd[idx + 1].endswith(".vtt"), cmd)
        self.assertIn("-a", cmd)
        self.assertIn("android.intent.action.VIEW", cmd)
        self.assertIn("-t", cmd)
        self.assertIn("video/*", cmd)
        self.assertIn("-p", cmd)
        self.assertIn("org.videolan.vlc", cmd)
        self.assertIn("--es", cmd)
        self.assertIn("title", cmd)


if __name__ == "__main__":
    unittest.main()
