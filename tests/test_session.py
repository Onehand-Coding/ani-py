import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import ani_py


def playback_args(**overrides):
    base = dict(
        download=False,
        player="mpv",
        player_flag=[],
        skip=False,
        no_detach=False,
        exit_after_play=False,
        ipc_socket=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class TestDetachedSessionStore(unittest.TestCase):
    def test_round_trip(self):
        session = ani_py.DetachedSession(
            socket="/tmp/ani-py-1.sock",
            player="/usr/bin/mpv",
            provider="hianime",
            provider_id="frieren-1",
            title="Frieren",
            episode="4",
            quality="1080p",
            mode="sub",
            source_provider="hianime",
            subtitle_preference="label:German",
        )
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "os.environ", {"ANI_PY_HIST_DIR": td}, clear=False
        ):
            store = ani_py.DetachedSessionStore()
            store.save(session)
            self.assertEqual(store.load(), session)
            self.assertTrue(store.path.exists())
            store.clear()
            self.assertIsNone(store.load())

    @patch.object(ani_py.Playback, "active", return_value=True)
    @patch("ani_py.is_android_environment", return_value=False)
    @patch("ani_py.which_first", return_value="/usr/bin/mpv")
    def test_playback_adopts_existing_mpv_socket(self, mock_which, mock_android, mock_active):
        pb = ani_py.Playback(playback_args())
        session = ani_py.DetachedSession(
            "/tmp/ani-py-old.sock", "/usr/bin/mpv", "hianime", "frieren-1",
            "Frieren", "4", "1080p", "sub", "hianime", "auto",
        )
        self.assertTrue(pb.adopt_detached_session(session))
        self.assertEqual(pb.ipc_path, Path("/tmp/ani-py-old.sock"))
        self.assertEqual(pb.player, "/usr/bin/mpv")


class TestDetachedSessionApp(unittest.TestCase):
    def test_detach_metadata_is_saved(self):
        app = object.__new__(ani_py.App)
        app.args = argparse.Namespace(mode="sub")
        app.playback = Mock()
        app.playback._is_android.return_value = False
        app.playback._is_mpv.return_value = True
        app.playback.ipc_path = Path("/tmp/ani-py.sock")
        app.playback.player = "/usr/bin/mpv"
        app.playback.active.return_value = True
        app.session_store = Mock()
        app.last_stream = ani_py.Stream("1080p", "https://video")
        app.last_provider = "hianime"
        app.subtitle_preference = "de"
        anime = ani_py.Anime("frieren-1", "Frieren", "hianime")
        episode = ani_py.Episode("ep4", "4")

        self.assertTrue(app._save_detached_session(anime, episode, "best"))
        saved = app.session_store.save.call_args.args[0]
        self.assertEqual(saved.socket, "/tmp/ani-py.sock")
        self.assertEqual(saved.episode, "4")
        self.assertEqual(saved.quality, "1080p")
        self.assertEqual(saved.subtitle_preference, "de")

    def test_resume_restores_controller_context(self):
        app = object.__new__(ani_py.App)
        app.args = argparse.Namespace(mode="dub")
        app.playback = Mock()
        app.playback.adopt_detached_session.return_value = True
        app.playback.current_path.return_value = "https://video"
        app.subtitle_preference = "auto"
        app._episodes_with_fallback = Mock()
        anime = ani_py.Anime("frieren-1", "Frieren", "hianime")
        episodes = [ani_py.Episode("ep3", "3"), ani_py.Episode("ep4", "4")]
        app._episodes_with_fallback.return_value = (anime, episodes)
        app._interactive_loop = Mock()
        app.session_store = Mock()
        session = ani_py.DetachedSession(
            "/tmp/ani-py.sock", "/usr/bin/mpv", "hianime", "frieren-1",
            "Frieren", "4", "720p", "sub", "hianime", "label:German",
        )

        self.assertEqual(app._resume_detached_session(session), 0)
        self.assertEqual(app.args.mode, "sub")
        self.assertEqual(app.subtitle_preference, "label:German")
        self.assertEqual(app.last_stream, ani_py.Stream("720p", "https://video"))
        app._interactive_loop.assert_called_once_with(anime, episodes, episodes[1], "720p")


if __name__ == "__main__":
    unittest.main()
