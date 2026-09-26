import argparse
import io
import unittest
from unittest.mock import Mock, patch

import ani_py


def app_args(**overrides):
    base = dict(
        clear_history=False,
        continue_watching=False,
        query=["frieren"],
        quality="best",
        download=False,
        exit_after_play=False,
        list_providers=False,
        provider="auto",
        provider_order="hianime,kuhi",
        select_nth=None,
        episode=None,
        mode="sub",
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class TestAppFlow(unittest.TestCase):
    def make_app(self, selected, **arg_overrides):
        app = object.__new__(ani_py.App)
        app.args = app_args(**arg_overrides)
        anime = ani_py.Anime("frieren-999", "Frieren")
        episodes = [ani_py.Episode(str(100 + i), str(i)) for i in range(1, 5)]
        app._search_anime = Mock(return_value=anime)
        app._pick_episodes = Mock(return_value=(anime, episodes, selected(episodes)))
        app._play_episode = Mock(return_value=0)
        app._interactive_loop = Mock()
        app.history = Mock()
        return app

    def test_range_is_sequential_foreground_queue(self):
        app = self.make_app(lambda eps: eps[:3])
        rc = app.run()
        self.assertEqual(rc, 0)
        self.assertEqual(app._play_episode.call_count, 3)
        for call in app._play_episode.call_args_list:
            self.assertTrue(call.kwargs["foreground"])
            self.assertFalse(call.kwargs["keep_open"])
        app._interactive_loop.assert_not_called()

    def test_single_episode_keeps_interactive_controller(self):
        app = self.make_app(lambda eps: [eps[0]])
        rc = app.run()
        self.assertEqual(rc, 0)
        call = app._play_episode.call_args
        self.assertFalse(call.kwargs["foreground"])
        self.assertTrue(call.kwargs["keep_open"])
        app._interactive_loop.assert_called_once()

    def test_download_range_stays_synchronous_without_controller(self):
        app = self.make_app(lambda eps: eps[:2], download=True)
        rc = app.run()
        self.assertEqual(rc, 0)
        self.assertEqual(app._play_episode.call_count, 2)
        app._interactive_loop.assert_not_called()

    def test_continue_history_advances_to_next_episode(self):
        app = object.__new__(ani_py.App)
        app.args = argparse.Namespace(episode=None, provider="auto", select_nth=None)
        app.history = Mock()
        app.history.load.return_value = [ani_py.HistoryEntry("1", "hianime", "frieren-999", "Frieren")]
        row = "Frieren  •  last watched 1  • hianime"
        app.menu = Mock()
        app.menu.choose.return_value = [row]
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            anime, last = app._from_history()
        self.assertEqual(anime.slug, "frieren-999")
        self.assertEqual(last, "1")

        app.providers = Mock()
        app.episode_cache = {}
        app.fallback_map = {}
        app.providers.episodes.return_value = [
            ani_py.Episode("101", "1"),
            ani_py.Episode("102", "2"),
        ]
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            used_anime, episodes, selected = app._pick_episodes(anime, last)
        self.assertEqual(selected[0].number, "2")

    def test_from_history_tolerates_fzf_ansi_stripping(self):
        import re
        app = object.__new__(ani_py.App)
        app.args = argparse.Namespace(episode=None, provider="auto", select_nth=None)
        app.history = Mock()
        app.history.load.return_value = [
            ani_py.HistoryEntry("1", "hianime", "frieren-999", "Frieren"),
            ani_py.HistoryEntry("2", "hianime", "one-piece-1", "One Piece"),
        ]
        app.menu = Mock()

        def fake_choose(rows, *args, **kwargs):
            # fzf --ansi strips ANSI codes from its output, so the
            # returned line is the plain (unstyled) row.
            return [re.sub(r"\x1b\[[0-9;]*m", "", rows[0])]

        app.menu.choose.side_effect = fake_choose
        with patch("ani_py.color_enabled", return_value=True):
            with patch("ani_py.sys.stderr", new=io.StringIO()):
                anime, last = app._from_history()
        self.assertEqual(anime.slug, "frieren-999")
        self.assertEqual(last, "1")

    def test_select_nth_bypasses_menu(self):
        app = object.__new__(ani_py.App)
        app.args = argparse.Namespace(select_nth=2, provider="auto")
        app.providers = Mock()
        app.providers.search.return_value = [
            ani_py.Anime("one", "One"),
            ani_py.Anime("two", "Two"),
        ]
        app.providers.get.return_value.display_name = "HiAnime"
        app.menu = Mock()
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            chosen = app._search_anime("test")
        self.assertEqual(chosen.slug, "two")
        app.menu.choose.assert_not_called()

    def test_clear_history_short_circuits(self):
        app = object.__new__(ani_py.App)
        app.args = app_args(clear_history=True)
        app.history = Mock()
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            self.assertEqual(app.run(), 0)
        app.history.clear.assert_called_once()


if __name__ == "__main__":
    unittest.main()
