import argparse
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from typing import Optional, TypedDict
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
class _PlayKw(TypedDict):
    """Exact keyword set for Playback.play.

    An untyped dict makes pyright assume **KW could carry any string key,
    including the bool-valued `foreground`/`keep_open` parameters. Naming the
    keys keeps `pb.play(STREAM, **KW)` precisely checkable.
    """

    title: str
    subtitle: Optional[str]
    referer: str
    mal_id: Optional[str]
    episode: str


KW = _PlayKw(
    title="Frieren Episode 1",
    subtitle="https://cdn.example/sub.vtt",
    referer="https://embed.example/",
    mal_id="52991",
    episode="1",
)


class FakeIpcServer:
    """Minimal stand-in for mpv's JSON IPC socket.

    Real mpv behaviour this reproduces: one client only, unsolicited events,
    and replies echoing request_id.
    """

    def __init__(self, replies=None):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "ipc.sock")
        self.replies = replies or {}
        self.commands = []
        self.conn = None
        self._lock = threading.Lock()
        self._closed = False
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(self.path)
        listener.listen(1)
        self._listener = listener
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            conn, _ = self._listener.accept()
        except OSError:
            return
        with self._lock:
            self.conn = conn
        buf = b""
        while True:
            try:
                chunk = conn.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                msg = json.loads(line)
                self.commands.append(msg)
                with self._lock:
                    # Once closed, never answer again. Closing a socket while
                    # another thread is blocked in recv() is not reliable, so a
                    # flag -- not the close itself -- is what makes shutdown
                    # deterministic for the client under test.
                    if self._closed:
                        return
                key = tuple(msg.get("command", []))
                reply = {
                    "data": self.replies.get(key),
                    "error": "success",
                    "request_id": msg.get("request_id"),
                }
                try:
                    conn.sendall((json.dumps(reply) + "\n").encode())
                except OSError:
                    return

    def emit(self, event, **data):
        """Push an unsolicited event, as mpv does to a connected client."""
        with self._lock:
            conn = self.conn
        if conn is None:
            self._thread.join(timeout=2)
            with self._lock:
                conn = self.conn
        if conn is None:
            return
        msg = {"event": event}
        if data:
            msg.update(data)
        conn.sendall((json.dumps(msg) + "\n").encode())

    def close(self):
        with self._lock:
            self._closed = True
            conn, self.conn = self.conn, None
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        self._listener.close()


def make_playback(**overrides):
    """A Playback whose player lookup is mocked.

    Playback() resolves the player binary during construction. Tests here
    exercise IPC behaviour, not player discovery, and CI runners have no mpv,
    so the lookup is mocked the way the rest of this file does.
    """
    with patch("ani_py.which_first", return_value="/usr/bin/mpv"), patch(
        "ani_py.is_android_environment", return_value=False
    ):
        return ani_py.Playback(args(player="mpv", **overrides))


def session_playback(server, replies=None):
    pb = make_playback()
    pb.ipc_path = ani_py.Path(server.path)
    pb.proc = Mock()
    pb.proc.poll.return_value = None
    session = ani_py._IpcSession(ani_py.Path(server.path))
    assert session.open()
    pb._session = session
    return pb


class TestIpcSession(unittest.TestCase):
    def test_command_roundtrip_matches_request_id(self):
        server = FakeIpcServer({("get_property", "idle-active"): False})
        pb = session_playback(server)
        try:
            self.assertIs(pb._ipc(["get_property", "idle-active"]), False)
            self.assertEqual(server.commands[0]["request_id"], 1)
        finally:
            pb._cleanup_ipc()
            server.close()

    def test_end_file_reason_is_captured(self):
        server = FakeIpcServer()
        pb = session_playback(server)
        try:
            session = pb._session
            assert session is not None
            seq = session.end_file_seq()
            server.emit("end-file", reason="eof", playlist_entry_id=1)
            deadline = time.monotonic() + 3
            while session.end_file_seq() == seq and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(session.wait_end_file(seq, timeout=1), "eof")
        finally:
            pb._cleanup_ipc()
            server.close()

    def test_wait_for_completion_maps_eof(self):
        server = FakeIpcServer()
        pb = session_playback(server)
        threading.Timer(0.2, lambda: server.emit("end-file", reason="eof")).start()
        try:
            self.assertEqual(pb.wait_for_completion(), "eof")
        finally:
            pb._cleanup_ipc()
            server.close()

    def test_wait_for_completion_maps_error_to_failed(self):
        server = FakeIpcServer()
        pb = session_playback(server)
        threading.Timer(0.2, lambda: server.emit("end-file", reason="error")).start()
        try:
            self.assertEqual(pb.wait_for_completion(), "failed")
        finally:
            pb._cleanup_ipc()
            server.close()

    def test_wait_for_completion_maps_quit_to_closed(self):
        server = FakeIpcServer()
        pb = session_playback(server)
        threading.Timer(0.2, lambda: server.emit("end-file", reason="quit")).start()
        try:
            self.assertEqual(pb.wait_for_completion(), "closed")
        finally:
            pb._cleanup_ipc()
            server.close()

    def test_wait_for_completion_stops_on_player_exit(self):
        server = FakeIpcServer()
        pb = session_playback(server)
        proc = Mock()
        proc.poll.return_value = 0
        pb.proc = proc
        try:
            self.assertEqual(pb.wait_for_completion(), "closed")
            self.assertIsNone(pb._session)
        finally:
            server.close()

    def test_wait_for_completion_reports_a_stalled_source(self):
        server = FakeIpcServer({("get_property", "paused-for-cache"): True})
        pb = session_playback(server)
        saved = ani_py.IPC_STALL_SECONDS
        ani_py.IPC_STALL_SECONDS = 0.5
        try:
            self.assertEqual(pb.wait_for_completion(), "stalled")
        finally:
            ani_py.IPC_STALL_SECONDS = saved
            pb._cleanup_ipc()
            server.close()

    def test_reached_eof_reports_true_at_the_end_of_a_file(self):
        server = FakeIpcServer({("get_property", "eof-reached"): True})
        pb = session_playback(server)
        try:
            self.assertIs(pb.reached_eof(), True)
        finally:
            pb._cleanup_ipc()
            server.close()

    def test_reached_eof_reports_false_while_still_playing(self):
        server = FakeIpcServer({("get_property", "eof-reached"): False})
        pb = session_playback(server)
        try:
            self.assertIs(pb.reached_eof(), False)
        finally:
            pb._cleanup_ipc()
            server.close()

    def test_reached_eof_is_unknown_without_a_session(self):
        # False would mean "did not finish" and would erase a recorded finish.
        pb = make_playback()
        self.assertIsNone(pb.reached_eof())

    def test_reached_eof_is_unknown_once_the_player_is_gone(self):
        server = FakeIpcServer()
        pb = session_playback(server)
        session = pb._session
        assert session is not None
        try:
            server.close()
            deadline = time.monotonic() + 5
            while not session.is_closed() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNone(pb.reached_eof())
        finally:
            pb._cleanup_ipc()

    def test_active_is_true_while_mpv_idles_with_no_file(self):
        """`path` is unavailable when idle; probing it made ani-py think mpv died."""
        server = FakeIpcServer()
        pb = session_playback(server)
        try:
            self.assertTrue(pb.active())
            probed = [tuple(c.get("command", [])) for c in server.commands]
            self.assertIn(("get_property", "idle-active"), probed)
        finally:
            pb._cleanup_ipc()
            server.close()

    def test_closed_session_is_not_active(self):
        server = FakeIpcServer()
        pb = session_playback(server)
        try:
            self.assertTrue(pb.active())
        finally:
            server.close()
        # Closing the peer is asynchronous; wait for the reader to see the EOF
        # rather than racing it.
        session = pb._session
        assert session is not None
        deadline = time.monotonic() + 5
        while not session.is_closed() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(pb.active())
        pb._cleanup_ipc()

    def test_play_drops_a_session_left_by_a_dead_player(self):
        pb = make_playback()
        stale = Mock()
        pb._session = stale
        with patch.object(ani_py.Playback, "active", return_value=False), \
                patch.object(ani_py.subprocess, "Popen") as popen, \
                patch.object(ani_py.Playback, "_wait_ipc", return_value=False):
            popen.return_value.poll.return_value = None
            pb.play(ani_py.Stream("720p", "https://cdn/x.m3u8"), title="t",
                    subtitle=None, referer="http://x/", mal_id=None, episode="1")
        stale.close.assert_called_once()
        self.assertIsNone(pb._session)


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
    def test_mpv_subtitle_switch_uses_ipc(self, mock_which, mock_ipc, mock_active):
        pb = ani_py.Playback(args(player="mpv"))
        pb.ipc_path = ani_py.Path("/tmp/test.sock")
        track = ani_py.SubtitleTrack("https://cdn.example/de.vtt", "de", "German")
        self.assertTrue(pb.set_subtitle(track))
        mock_ipc.assert_called_once_with(
            ["sub-add", track.url, "select", "German", "de"]
        )

    @patch.object(ani_py.Playback, "active", return_value=True)
    @patch.object(ani_py.Playback, "_ipc")
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_mpv_subtitle_off_uses_sid_no(self, mock_which, mock_ipc, mock_active):
        pb = ani_py.Playback(args(player="mpv"))
        pb.ipc_path = ani_py.Path("/tmp/test.sock")
        self.assertTrue(pb.set_subtitle(None))
        mock_ipc.assert_called_once_with(["set_property", "sid", "no"])

    @patch.object(ani_py.Playback, "active", return_value=True)
    @patch.object(ani_py.Playback, "_ipc")
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_resume_unpauses_active_mpv(self, mock_which, mock_ipc, mock_active):
        pb = ani_py.Playback(args(player="mpv"))
        pb.ipc_path = ani_py.Path("/tmp/test.sock")
        self.assertTrue(pb.resume())
        mock_ipc.assert_called_once_with(["set_property", "pause", False])

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

    @patch.object(ani_py.Playback, "_ipc_supported", return_value=True)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_auto_next_support_requires_mpv_ipc(self, mock_which, mock_supported):
        pb = ani_py.Playback(args(player="mpv"))
        self.assertTrue(pb.auto_next_supported())

    @patch.object(ani_py.Playback, "_ipc_supported", return_value=True)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_wait_for_completion_reports_only_natural_eof(self, mock_which, mock_supported):
        pb = ani_py.Playback(args(player="mpv"))
        pb.ipc_path = ani_py.Path("/tmp/test-auto-next.sock")
        pb.proc = Mock()
        pb.proc.poll.return_value = None
        with patch.object(pb, "_ipc", side_effect=[False, True]) as ipc:
            self.assertEqual(pb.wait_for_completion(poll_interval=0), "eof")
        self.assertEqual(ipc.call_count, 2)
        ipc.assert_called_with(["get_property", "eof-reached"], timeout=0.6)

    @patch.object(ani_py.Playback, "_ipc_supported", return_value=True)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_wait_for_completion_does_not_treat_player_exit_as_eof(self, mock_which, mock_supported):
        pb = ani_py.Playback(args(player="mpv"))
        pb.ipc_path = ani_py.Path("/tmp/test-auto-next.sock")
        pb.proc = Mock()
        pb.proc.poll.return_value = 0
        self.assertEqual(pb.wait_for_completion(poll_interval=0), "closed")
        self.assertIsNone(pb.proc)
        self.assertIsNone(pb.ipc_path)

    @patch("ani_py.which_first", return_value="/usr/bin/vlc")
    def test_auto_next_support_rejects_non_mpv(self, mock_which):
        pb = ani_py.Playback(args(player="vlc"))
        self.assertFalse(pb.auto_next_supported())

    @patch.object(ani_py.Playback, "_ipc_supported", return_value=True)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_wait_for_completion_retries_transient_ipc_errors(self, mock_which, mock_supported):
        """mpv answers "property unavailable" for a few ms right after launch."""
        pb = ani_py.Playback(args(player="mpv"))
        pb.ipc_path = ani_py.Path("/tmp/test-auto-next.sock")
        pb.proc = Mock()
        pb.proc.poll.return_value = None
        blip = RuntimeError("property unavailable")
        with patch.object(pb, "_ipc", side_effect=[blip, blip, False, True]) as ipc:
            self.assertEqual(pb.wait_for_completion(poll_interval=0), "eof")
        self.assertEqual(ipc.call_count, 4)

    @patch.object(ani_py.Playback, "_ipc_supported", return_value=True)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_wait_for_completion_gives_up_as_ipc_lost_not_eof(self, mock_which, mock_supported):
        pb = ani_py.Playback(args(player="mpv"))
        pb.ipc_path = ani_py.Path("/tmp/test-auto-next.sock")
        pb.proc = Mock()
        pb.proc.poll.return_value = None
        with patch.object(pb, "_ipc", side_effect=RuntimeError("no reply from mpv")) as ipc:
            self.assertEqual(pb.wait_for_completion(poll_interval=0), "ipc-lost")
        self.assertEqual(ipc.call_count, ani_py.IPC_GRACE_POLLS + 1)

    @patch.object(ani_py.Playback, "_ipc_supported", return_value=True)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_reset_resumed_position_rewinds_a_saved_end_position(self, mock_which, mock_supported):
        pb = ani_py.Playback(args(player="mpv"))
        pb.ipc_path = ani_py.Path("/tmp/test-auto-next.sock")
        replies = [["get_property", "time-pos"], ["get_property", "duration"]]
        with patch.object(pb, "_ipc", side_effect=[1440.0, 1440.0, True]) as ipc:
            self.assertTrue(pb._reset_resumed_position())
        self.assertEqual(replies[0][1], "time-pos")
        ipc.assert_any_call(["set_property", "time-pos", 0.0], timeout=0.6)

    @patch.object(ani_py.Playback, "_ipc_supported", return_value=True)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_reset_resumed_position_leaves_a_real_resume_alone(self, mock_which, mock_supported):
        pb = ani_py.Playback(args(player="mpv"))
        pb.ipc_path = ani_py.Path("/tmp/test-auto-next.sock")
        with patch.object(pb, "_ipc", side_effect=[600.0, 1440.0]) as ipc:
            self.assertFalse(pb._reset_resumed_position())
        self.assertNotIn(
            ["set_property", "time-pos", 0.0],
            [call.args[0] for call in ipc.call_args_list],
        )

    @patch.object(ani_py.Playback, "_ipc_supported", return_value=True)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_reset_resumed_position_ignores_unknown_duration(self, mock_which, mock_supported):
        """Live streams report no duration; never rewind those."""
        pb = ani_py.Playback(args(player="mpv"))
        pb.ipc_path = ani_py.Path("/tmp/test-auto-next.sock")
        with patch.object(pb, "_ipc", side_effect=[600.0, None]):
            self.assertFalse(pb._reset_resumed_position())

    @patch.object(ani_py.Playback, "_ipc_supported", return_value=True)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_clear_external_subtitles_drops_only_external_subs(self, mock_which, mock_supported):
        pb = ani_py.Playback(args(player="mpv"))
        pb.ipc_path = ani_py.Path("/tmp/test-auto-next.sock")
        track_list = [
            {"id": 1, "type": "sub", "external": False},
            {"id": 2, "type": "sub", "external": True},
            {"id": 3, "type": "audio", "external": True},
            {"id": 4, "type": "sub", "external": True},
        ]
        with patch.object(pb, "_ipc", return_value=track_list) as ipc:
            pb._clear_external_subtitles()
        removed = [call.args[0] for call in ipc.call_args_list if call.args[0][0] == "sub-remove"]
        self.assertEqual(removed, [["sub-remove", 4], ["sub-remove", 2]])

    @patch.object(ani_py.Playback, "_ipc_supported", return_value=True)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_skip_args_are_cached_per_episode(self, mock_which, mock_supported):
        pb = ani_py.Playback(args(player="mpv", skip=True))
        with patch.object(pb, "_fetch_skip_args", return_value=["-fs", "--x"]) as fetch:
            self.assertEqual(pb._skip_args("1", "2"), ["-fs", "--x"])
            self.assertEqual(pb._skip_args("1", "2"), ["-fs", "--x"])
            self.assertEqual(fetch.call_count, 1)

    @patch.object(ani_py.Playback, "active", return_value=True)
    @patch.object(ani_py.Playback, "_ipc_supported", return_value=True)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_replace_stays_in_place_when_skip_produced_no_flags(self, mock_which, mock_supported, mock_active):
        """A missing/failing ani-skip has nothing to apply, so do not restart."""
        pb = ani_py.Playback(args(player="mpv", skip=True))
        pb.ipc_path = ani_py.Path("/tmp/test.sock")
        with patch.object(pb, "_skip_args", return_value=[]), \
                patch.object(pb, "play") as play, \
                patch.object(pb, "_wait_path", return_value=True), \
                patch.object(pb, "_clear_external_subtitles"), \
                patch.object(pb, "_reset_resumed_position", return_value=False), \
                patch.object(pb, "_ipc") as ipc:
            rc = pb.replace(ani_py.Stream("1080p", "https://cdn/2.m3u8"), **KW)
        self.assertEqual(rc, 0)
        play.assert_not_called()
        self.assertIn(["loadfile", "https://cdn/2.m3u8", "replace"], [c.args[0] for c in ipc.call_args_list])

    @patch.object(ani_py.Playback, "active", return_value=True)
    @patch.object(ani_py.Playback, "_ipc_supported", return_value=True)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_replace_applies_skip_state_without_restarting(self, mock_which, mock_supported, mock_active):
        pb = ani_py.Playback(args(player="mpv", skip=True))
        pb.ipc_path = ani_py.Path("/tmp/test.sock")
        with patch.object(pb, "_skip_args", return_value=["--chapters-file=/tmp/ep2.txt", "--script-opts=skip-op_start=1"]), \
                patch.object(pb, "_write_skip_state"), \
                patch.object(pb, "play", return_value=0) as play, \
                patch.object(pb, "_wait_path", return_value=True), \
                patch.object(pb, "_clear_external_subtitles"), \
                patch.object(pb, "_reset_resumed_position", return_value=False), \
                patch.object(pb, "_ipc") as ipc:
            rc = pb.replace(ani_py.Stream("1080p", "https://cdn/2.m3u8"), **KW)
        play.assert_not_called()
        self.assertEqual(rc, 0)
        commands = [call.args[0] for call in ipc.call_args_list]
        self.assertIn(
            ["loadfile", "https://cdn/2.m3u8", "replace", -1, "chapters-file=/tmp/ep2.txt"],
            commands,
        )

    def test_parse_skip_flags_extracts_chapters_and_intervals(self):
        data = ani_py.parse_skip_flags([
            "--chapters-file=/tmp/ep1.txt",
            "--script-opts=skip-op_start=83.5,skip-op_end=92,skip-ed_start=1200,skip-ed_end=1280,skip-offset=1",
        ])
        assert data is not None
        self.assertEqual(data.get("chapters_file"), "/tmp/ep1.txt")
        self.assertEqual(data.get("op_start"), 83.5)
        self.assertEqual(data.get("op_end"), 92.0)
        self.assertEqual(data.get("ed_start"), 1200.0)
        self.assertEqual(data.get("ed_end"), 1280.0)
        self.assertEqual(data.get("offset"), 1.0)

    def test_parse_skip_flags_is_empty_for_empty_input(self):
        self.assertIsNone(ani_py.parse_skip_flags([]))

    def test_window_flags_require_matching_state(self):
        pb = ani_py.Playback(args(player="mpv"))
        self.assertEqual(pb._window_flags(), [])
        pb._window_state = {
            "fullscreen": True,
            "geometry": "300x200",
            "autofit": "50%x0",
            "junk": "<script>",
        }
        self.assertEqual(
            pb._window_flags(),
            ["--fullscreen", "--geometry=300x200", "--autofit=50%x0"],
        )

    @patch.object(ani_py.Playback, "active", return_value=True)
    @patch.object(ani_py.Playback, "_ipc_supported", return_value=True)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_replace_clears_previous_episode_subtitles(self, mock_which, mock_supported, mock_active):
        pb = ani_py.Playback(args(player="mpv"))
        pb.ipc_path = ani_py.Path("/tmp/test.sock")
        with patch.object(pb, "play"), \
                patch.object(pb, "_wait_path", return_value=True), \
                patch.object(pb, "_reset_resumed_position", return_value=False), \
                patch.object(pb, "_ipc") as ipc, \
                patch.object(pb, "_clear_external_subtitles") as clear:
            pb.replace(ani_py.Stream("1080p", "https://cdn/2.m3u8"), **KW)
        clear.assert_called_once_with()
        commands = [call.args[0] for call in ipc.call_args_list]
        self.assertLess(
            commands.index(["loadfile", "https://cdn/2.m3u8", "replace"]),
            len(commands) - 1,
        )
        self.assertIn(["sub-add", "https://cdn.example/sub.vtt", "select"], commands)

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
