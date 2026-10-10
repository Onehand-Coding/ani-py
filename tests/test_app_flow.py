import argparse
import io
import os
import re
import tempfile
import time
import unittest
from typing import cast
from unittest.mock import Mock, patch

import ani_py


class _Tty(io.StringIO):
    """A stderr stand-in that claims to be a terminal."""

    def isatty(self) -> bool:
        return True


def _mock(app: object, name: str) -> Mock:
    """A Mock the test installed over a real App method.

    App declares these attributes as methods, so a type checker rejects reading
    `.call_count` off them even though the tests replaced them with Mocks.
    """
    return cast(Mock, getattr(app, name))


def app_args(**overrides):
    base = dict(
        clear_history=False,
        forget=False,
        continue_watching=False,
        query=["frieren"],
        quality="best",
        download=False,
        exit_after_play=False,
        auto_next=False,
        auto_next_limit=0,
        no_detach=False,
        attach=False,
        list_providers=False,
        provider="auto",
        provider_order="hianime,anilight",
        select_nth=None,
        episode=None,
        mode="sub",
        sort=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class TestHistoryCompletion(unittest.TestCase):
    def _store(self) -> ani_py.HistoryStore:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        # The store resolves its paths at construction, so it keeps working
        # after the environment is restored.
        with patch.dict(os.environ, {"ANI_PY_HIST_DIR": holder.name}):
            return ani_py.HistoryStore()

    def test_completion_state_round_trips(self):
        store = self._store()
        anime = ani_py.Anime("geass-1", "Code Geass")
        store.update(anime, "18", completed=False)
        self.assertEqual(store.load()[0].completed, False)
        store.update(anime, "19", completed=True)
        entry = store.load()[0]
        self.assertEqual((entry.episode, entry.completed), ("19", True))

    def test_legacy_rows_read_as_completed(self):
        # Rows written before the flag existed carry no completion info; they
        # must keep advancing, not replay a finished episode.
        store = self._store()
        store.path.write_text("18\thianime\tgeass-1\tCode Geass\n", encoding="utf-8")
        self.assertTrue(store.load()[0].completed)

    def test_tabs_in_a_title_survive_the_state_token(self):
        store = self._store()
        store.path.write_text("7\thianime\tgeass-1\tA\tB\tstate=0\n", encoding="utf-8")
        entry = store.load()[0]
        self.assertEqual(entry.title, "A\tB")
        self.assertFalse(entry.completed)

    def test_an_older_three_field_row_still_loads(self):
        store = self._store()
        store.path.write_text("7\tgeass-1\tCode Geass\n", encoding="utf-8")
        entry = store.load()[0]
        self.assertEqual((entry.provider, entry.provider_id, entry.title),
                         ("hianime", "geass-1", "Code Geass"))
        self.assertTrue(entry.completed)

    def test_continue_resumes_an_unfinished_episode(self):
        app = object.__new__(ani_py.App)
        app.args = app_args(auto_next=True)
        anime = ani_py.Anime("geass", "Code Geass")
        episodes = [ani_py.Episode(str(100 + i), str(i)) for i in range(1, 26)]
        app._episodes_with_fallback = Mock(return_value=(anime, episodes))
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            _, _, selected = app._pick_episodes(anime, "18", continue_completed=False)
        self.assertEqual([e.number for e in selected], ["18"])

    def test_continue_advances_after_a_finished_episode(self):
        app = object.__new__(ani_py.App)
        app.args = app_args(auto_next=True)
        anime = ani_py.Anime("geass", "Code Geass")
        episodes = [ani_py.Episode(str(100 + i), str(i)) for i in range(1, 26)]
        app._episodes_with_fallback = Mock(return_value=(anime, episodes))
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            _, _, selected = app._pick_episodes(anime, "18", continue_completed=True)
        self.assertEqual([e.number for e in selected], ["19"])

    def test_continue_defaults_to_the_previous_advancing_behaviour(self):
        app = object.__new__(ani_py.App)
        app.args = app_args(auto_next=True)
        anime = ani_py.Anime("geass", "Code Geass")
        episodes = [ani_py.Episode(str(100 + i), str(i)) for i in range(1, 26)]
        app._episodes_with_fallback = Mock(return_value=(anime, episodes))
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            _, _, selected = app._pick_episodes(anime, "18")
        self.assertEqual([e.number for e in selected], ["19"])

    def test_continue_at_the_last_finished_episode_is_refused(self):
        app = object.__new__(ani_py.App)
        app.args = app_args(auto_next=True)
        anime = ani_py.Anime("geass", "Code Geass")
        episodes = [ani_py.Episode(str(100 + i), str(i)) for i in range(1, 5)]
        app._episodes_with_fallback = Mock(return_value=(anime, episodes))
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                app._pick_episodes(anime, "4", continue_completed=True)

    def test_continue_can_resume_the_final_episode_when_unfinished(self):
        # Resuming must not be blocked by end-of-list the way advancing is.
        app = object.__new__(ani_py.App)
        app.args = app_args(auto_next=True)
        anime = ani_py.Anime("geass", "Code Geass")
        episodes = [ani_py.Episode(str(100 + i), str(i)) for i in range(1, 5)]
        app._episodes_with_fallback = Mock(return_value=(anime, episodes))
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            _, _, selected = app._pick_episodes(anime, "4", continue_completed=False)
        self.assertEqual([e.number for e in selected], ["4"])


class TestInteractiveCompletionRecording(unittest.TestCase):
    """Leaving an episode must record what mpv actually saw.

    Recording at the top of the menu loop is too early: the episode has not
    finished yet, so an episode watched to its end and then closed was recorded
    as unfinished and `--continue` replayed it.
    """

    def _app(self, reached_eof):
        app = object.__new__(ani_py.App)
        app.args = app_args()
        playback = Mock()
        playback.reached_eof.return_value = reached_eof
        app.playback = playback
        app._play_episode = Mock(return_value=0)
        app._clear_detached_session = Mock()
        app.last_stream = ani_py.Stream("1080p", "https://video")
        app.last_provider = "hianime"
        app.last_subtitle = None
        app.subtitle_preference = "auto"
        app.providers = Mock()
        history = Mock()
        app.history = history
        return app, history, playback

    def _episodes(self):
        return [ani_py.Episode("101", "1"), ani_py.Episode("102", "2")]

    def test_closing_after_finishing_records_it_watched(self):
        app, history, _ = self._app(reached_eof=True)
        app.menu = Mock()
        app.menu.choose.return_value = []  # user dismissed the menu
        anime = ani_py.Anime("geass", "Code Geass")
        app._interactive_loop(anime, self._episodes(), self._episodes()[0], "1080")
        history.update.assert_called_once_with(anime, "1", completed=True)

    def test_closing_mid_episode_records_it_unfinished(self):
        app, history, _ = self._app(reached_eof=False)
        app.menu = Mock()
        app.menu.choose.return_value = []
        anime = ani_py.Anime("geass", "Code Geass")
        app._interactive_loop(anime, self._episodes(), self._episodes()[0], "1080")
        history.update.assert_called_once_with(anime, "1", completed=False)

    def test_stop_and_quit_records_before_stopping_the_player(self):
        # Once mpv is stopped its end-of-file state is unknowable, so the
        # record has to happen first or every episode looks unfinished.
        app, history, playback = self._app(reached_eof=True)
        app.menu = Mock()
        app.menu.choose.return_value = ["Stop & quit"]
        order = []
        history.update.side_effect = lambda *a, **k: order.append("record")
        playback.stop.side_effect = lambda *a, **k: order.append("stop")
        anime = ani_py.Anime("geass", "Code Geass")
        app._interactive_loop(anime, self._episodes(), self._episodes()[0], "1080")
        self.assertEqual(order, ["record", "stop"])


    def test_unreachable_player_does_not_overwrite_a_recorded_finish(self):
        # mpv already exited (its window was closed), so reached_eof() cannot
        # answer. Unknown must not be written down as "did not finish", or the
        # exit path would undo what the watcher recorded while mpv was alive.
        app, history, _ = self._app(reached_eof=None)
        app.menu = Mock()
        app.menu.choose.return_value = []
        anime = ani_py.Anime("geass", "Code Geass")
        app._interactive_loop(anime, self._episodes(), self._episodes()[0], "1080")
        history.update.assert_not_called()

    def test_watcher_records_the_finish_while_mpv_is_still_alive(self):
        app, history, _ = self._app(reached_eof=True)
        anime = ani_py.Anime("geass", "Code Geass")
        app._watch_completion(anime, self._episodes()[0])
        deadline = time.monotonic() + 5
        while not history.update.called and time.monotonic() < deadline:
            time.sleep(0.02)
        self.addCleanup(app._stop_completion_watch)
        history.update.assert_called_once_with(anime, "1", completed=True)

    def test_watcher_does_not_record_while_still_playing(self):
        app, history, _ = self._app(reached_eof=False)
        anime = ani_py.Anime("geass", "Code Geass")
        app._watch_completion(anime, self._episodes()[0])
        self.addCleanup(app._stop_completion_watch)
        time.sleep(0.3)
        history.update.assert_not_called()


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
        self.assertEqual(_mock(app, "_play_episode").call_count, 3)
        for call in _mock(app, "_play_episode").call_args_list:
            self.assertTrue(call.kwargs["foreground"])
            self.assertFalse(call.kwargs["keep_open"])
        _mock(app, "_interactive_loop").assert_not_called()

    def test_single_episode_keeps_interactive_controller(self):
        app = self.make_app(lambda eps: [eps[0]])
        rc = app.run()
        self.assertEqual(rc, 0)
        call = _mock(app, "_play_episode").call_args
        self.assertFalse(call.kwargs["foreground"])
        self.assertTrue(call.kwargs["keep_open"])
        _mock(app, "_interactive_loop").assert_called_once()

    def test_download_range_stays_synchronous_without_controller(self):
        app = self.make_app(lambda eps: eps[:2], download=True)
        rc = app.run()
        self.assertEqual(rc, 0)
        self.assertEqual(_mock(app, "_play_episode").call_count, 2)
        _mock(app, "_interactive_loop").assert_not_called()

    def test_auto_next_single_episode_expands_to_remaining_episode_list(self):
        episodes = [ani_py.Episode(str(100 + i), str(i)) for i in range(1, 5)]
        queue = ani_py.App._auto_next_queue(episodes, [episodes[1]])
        self.assertEqual([episode.number for episode in queue], ["2", "3", "4"])

    def test_auto_next_explicit_multi_selection_is_respected_exactly(self):
        episodes = [ani_py.Episode(str(100 + i), str(i)) for i in range(1, 5)]
        selected = [episodes[3], episodes[1]]
        queue = ani_py.App._auto_next_queue(episodes, selected)
        self.assertEqual(queue, selected)

    def test_auto_next_advances_only_after_natural_eof(self):
        app = object.__new__(ani_py.App)
        app.args = app_args(auto_next=True)
        anime = ani_py.Anime("frieren-999", "Frieren")
        episodes = [ani_py.Episode("101", "1"), ani_py.Episode("102", "2")]
        app.playback = Mock()
        app.playback.auto_next_supported.return_value = True
        app.playback.wait_for_completion.side_effect = ["eof", "eof"]
        app._play_episode = Mock(return_value=0)
        app.history = Mock()

        with patch("ani_py.sys.stderr", new=io.StringIO()):
            self.assertEqual(app._run_auto_next(anime, episodes, [episodes[0]], "best"), 0)

        self.assertEqual(_mock(app, "_play_episode").call_count, 2)
        first, second = _mock(app, "_play_episode").call_args_list
        self.assertFalse(first.kwargs["replace"])
        self.assertTrue(first.kwargs["keep_open"])
        self.assertTrue(second.kwargs["replace"])
        self.assertTrue(second.kwargs["keep_open"])
        app.playback.resume.assert_called_once_with()
        app.playback.stop.assert_called_once_with()

    def test_auto_next_stops_queue_when_playback_closes_before_eof(self):
        app = object.__new__(ani_py.App)
        app.args = app_args(auto_next=True)
        anime = ani_py.Anime("frieren-999", "Frieren")
        episodes = [ani_py.Episode("101", "1"), ani_py.Episode("102", "2")]
        app.playback = Mock()
        app.playback.auto_next_supported.return_value = True
        app.playback.wait_for_completion.return_value = "closed"
        app._play_episode = Mock(return_value=0)

        with patch("ani_py.sys.stderr", new=io.StringIO()):
            self.assertEqual(app._run_auto_next(anime, episodes, [episodes[0]], "best"), 0)

        _mock(app, "_play_episode").assert_called_once()
        app.playback.stop.assert_called_once_with()

    def test_auto_next_rejects_player_without_reliable_completion_signal(self):
        app = object.__new__(ani_py.App)
        app.args = app_args(auto_next=True)
        app.playback = Mock()
        app.playback.auto_next_supported.return_value = False
        anime = ani_py.Anime("frieren-999", "Frieren")
        episodes = [ani_py.Episode("101", "1")]
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                app._run_auto_next(anime, episodes, episodes, "best")

    def test_continue_history_advances_to_next_episode(self):
        app = object.__new__(ani_py.App)
        app.args = argparse.Namespace(episode=None, provider="auto", select_nth=None)
        app.history = Mock()
        app.history.load.return_value = [ani_py.HistoryEntry("1", "hianime", "frieren-999", "Frieren")]
        app.menu = Mock()
        # Take the row the code built rather than hardcoding its wording.
        app.menu.choose.side_effect = lambda rows, *a, **k: [rows[0]]
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            anime, last, completed = app._from_history()
        self.assertEqual(anime.slug, "frieren-999")
        self.assertEqual(last, "1")
        self.assertTrue(completed)

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

    def test_from_history_default_keeps_file_order(self):
        app = object.__new__(ani_py.App)
        app.args = argparse.Namespace(episode=None, provider="auto", select_nth=None, sort=None)
        app.history = Mock()
        app.history.load.return_value = [
            ani_py.HistoryEntry("1", "hianime", "frieren-999", "Frieren"),
            ani_py.HistoryEntry("4", "hianime", "one-piece-1", "One Piece"),
        ]
        app.menu = Mock()
        app.menu.choose.side_effect = lambda rows, *a, **k: [rows[0]]
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            anime, last, completed = app._from_history()
        self.assertEqual(anime.slug, "frieren-999")
        self.assertEqual(last, "1")

    def test_from_history_sort_recent_shows_newest_first(self):
        app = object.__new__(ani_py.App)
        app.args = argparse.Namespace(episode=None, provider="auto", select_nth=None, sort="recent")
        app.history = Mock()
        app.history.load.return_value = [
            ani_py.HistoryEntry("1", "hianime", "frieren-999", "Frieren"),
            ani_py.HistoryEntry("4", "hianime", "one-piece-1", "One Piece"),
        ]
        app.menu = Mock()
        app.menu.choose.side_effect = lambda rows, *a, **k: [rows[0]]
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            anime, last, completed = app._from_history()
        self.assertEqual(anime.slug, "one-piece-1")
        self.assertEqual(last, "4")

    def test_from_history_sort_alpha_orders_by_title(self):
        app = object.__new__(ani_py.App)
        app.args = argparse.Namespace(episode=None, provider="auto", select_nth=None, sort="alpha")
        app.history = Mock()
        app.history.load.return_value = [
            ani_py.HistoryEntry("1", "hianime", "frieren-999", "Frieren"),
            ani_py.HistoryEntry("2", "hianime", "bleach-1", "Bleach"),
            ani_py.HistoryEntry("4", "hianime", "one-piece-1", "One Piece"),
        ]
        app.menu = Mock()
        app.menu.choose.side_effect = lambda rows, *a, **k: [rows[0]]
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            anime, last, completed = app._from_history()
        self.assertEqual(anime.slug, "bleach-1")
        self.assertEqual(last, "2")

    def test_from_history_tolerates_fzf_ansi_stripping(self):
        import re
        app = object.__new__(ani_py.App)
        app.args = argparse.Namespace(episode=None, provider="auto", select_nth=None, sort=None)
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
                anime, last, completed = app._from_history()
        # File order: rows[0] is Frieren (stored first).
        self.assertEqual(anime.slug, "frieren-999")
        self.assertEqual(last, "1")
        self.assertTrue(completed)

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

    def test_search_another_anime_ignores_startup_episode_and_select_nth(self):
        app = object.__new__(ani_py.App)
        app.args = app_args(select_nth=2, episode="4")
        app.providers = Mock()
        found = [
            ani_py.Anime("one", "One"),
            ani_py.Anime("two", "Two"),
        ]
        app.providers.search.return_value = found
        app.providers.get.return_value.display_name = "HiAnime"
        episodes = [
            ani_py.Episode("101", "1"),
            ani_py.Episode("102", "2"),
        ]
        app._episodes_with_fallback = Mock(return_value=(found[0], episodes))
        app.menu = Mock()
        app.menu.choose.side_effect = [
            ["  1  One"],
            ["Episode 2"],
        ]
        app.menu.prompt_text.return_value = "one"

        with patch("ani_py.sys.stderr", new=io.StringIO()):
            with patch("ani_py.sys.stdout", new=io.StringIO()):
                selected = app._search_another_anime()

        self.assertEqual(selected, (found[0], episodes, episodes[1]))
        app.providers.search.assert_called_once_with("one", "auto")
        self.assertEqual(app.menu.choose.call_count, 2)

    def test_interactive_search_replaces_playback_and_continues_session(self):
        app = object.__new__(ani_py.App)
        app.args = app_args()
        current_anime = ani_py.Anime("frieren-999", "Frieren")
        current_episodes = [
            ani_py.Episode("101", "1"),
            ani_py.Episode("102", "2"),
        ]
        next_anime = ani_py.Anime("one-piece-1", "One Piece")
        next_episodes = [
            ani_py.Episode("201", "1"),
            ani_py.Episode("202", "2"),
        ]
        app.playback = Mock()
        app.playback.active.return_value = True
        app.providers = Mock()
        app.providers.get.return_value.display_name = "HiAnime"
        app.menu = Mock()
        app.menu.choose.side_effect = [
            ["Search another anime"],
            ["Stop & quit"],
        ]
        app._search_another_anime = Mock(
            return_value=(next_anime, next_episodes, next_episodes[1])
        )
        app._play_episode = Mock(return_value=0)
        app.last_stream = None
        app.last_provider = None
        app.history = Mock()

        app._interactive_loop(
            current_anime,
            current_episodes,
            current_episodes[0],
            "best",
        )

        app._search_another_anime.assert_called_once_with()
        _mock(app, "_play_episode").assert_called_once_with(
            next_anime,
            next_episodes[1],
            "best",
            replace=True,
        )
        app.playback.stop.assert_called_once_with()
        second_menu_header = app.menu.choose.call_args_list[1].kwargs["header"]
        self.assertIn("One Piece", second_menu_header)
        self.assertIn("Episode 2", second_menu_header)

    def test_change_subtitle_switches_live_mpv_without_reloading_video(self):
        app = object.__new__(ani_py.App)
        app.args = app_args()
        anime = ani_py.Anime("frieren-999", "Frieren")
        episodes = [ani_py.Episode("101", "1")]
        english = ani_py.SubtitleTrack("https://subs/en.vtt", "en", "English", True)
        german = ani_py.SubtitleTrack("https://subs/de.vtt", "de", "German")
        bundle = ani_py.StreamBundle(
            streams=[ani_py.Stream("1080p", "https://video")],
            subtitle=english.url,
            referer="https://embed/",
            mal_id="52991",
            provider="hianime",
            subtitles=[english, german],
        )
        app.playback = Mock()
        app.playback.active.return_value = True
        app.playback.set_subtitle.return_value = True
        app.providers = Mock()
        app.providers.get.return_value.display_name = "HiAnime"
        app.menu = Mock()
        app.menu.choose.side_effect = [
            ["Change subtitle"],
            ["German [de]"],
            ["Stop & quit"],
        ]
        app._bundle = Mock(return_value=bundle)
        app._play_episode = Mock(return_value=0)
        app.last_stream = ani_py.Stream("1080p", "https://video")
        app.last_provider = "hianime"
        app.last_subtitle = english
        app.subtitle_preference = "auto"
        app.history = Mock()

        app._interactive_loop(anime, episodes, episodes[0], "1080")

        app.playback.set_subtitle.assert_called_once_with(german)
        _mock(app, "_play_episode").assert_not_called()
        self.assertEqual(app.subtitle_preference, "label:German")

    def test_auto_next_rejects_attach_before_session_handling(self):
        app = object.__new__(ani_py.App)
        app.args = app_args(auto_next=True, attach=True)
        app.history = Mock()
        app.playback = Mock()
        app.playback.auto_next_supported.return_value = True
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                app.run()

    def test_auto_next_rejects_unsupported_player_before_any_search(self):
        app = object.__new__(ani_py.App)
        app.args = app_args(auto_next=True)
        app.history = Mock()
        app.playback = Mock()
        app.playback.auto_next_supported.return_value = False
        app.providers = Mock()
        app._search_anime = Mock()
        app._pick_episodes = Mock()
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                app.run()
        _mock(app, "_search_anime").assert_not_called()
        _mock(app, "_pick_episodes").assert_not_called()

    def test_auto_next_queue_is_capped_and_reports_the_trim(self):
        episodes = [ani_py.Episode(str(100 + i), str(i)) for i in range(1, 21)]
        with patch("ani_py.sys.stderr", new=io.StringIO()) as stderr:
            queue = ani_py.App._auto_next_queue(episodes, [episodes[0]], 3)
        self.assertEqual([episode.number for episode in queue], ["1", "2", "3"])
        self.assertIn("17 later episode(s) will not be queued", stderr.getvalue())

    def test_auto_next_queue_respects_explicit_selection_before_the_cap(self):
        episodes = [ani_py.Episode(str(100 + i), str(i)) for i in range(1, 6)]
        selected = [episodes[1], episodes[0]]
        queue = ani_py.App._auto_next_queue(episodes, selected, 12)
        self.assertEqual(queue, selected)

    def test_auto_next_queue_never_caps_an_explicit_selection(self):
        # The cap exists only to stop one selection expanding into a whole
        # season. A range the user picked is their decision and is honoured,
        # even when it is longer than the default limit.
        episodes = [ani_py.Episode(str(100 + i), str(i)) for i in range(1, 21)]
        selected = episodes[:5]
        queue = ani_py.App._auto_next_queue(episodes, selected, 2)
        self.assertEqual(queue, selected)

    def test_continue_yields_one_episode_so_auto_next_expands_and_caps(self):
        # --continue resolves to the next unwatched episode as a *single*
        # selection, so --auto-next expands from it and the limit applies --
        # unlike an explicitly typed range, which is never trimmed.
        app = object.__new__(ani_py.App)
        app.args = app_args(auto_next=True)
        anime = ani_py.Anime("geass", "Code Geass: Lelouch of the Rebellion R2")
        episodes = [ani_py.Episode(str(100 + i), str(i)) for i in range(1, 26)]
        app._episodes_with_fallback = Mock(return_value=(anime, episodes))
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            _, loaded, selected = app._pick_episodes(anime, "18")
        self.assertEqual([e.number for e in selected], ["19"])
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            queue = ani_py.App._auto_next_queue(loaded, selected, 3)
        self.assertEqual([e.number for e in queue], ["19", "20", "21"])

    def test_continue_at_the_last_episode_is_refused_clearly(self):
        app = object.__new__(ani_py.App)
        app.args = app_args(auto_next=True)
        anime = ani_py.Anime("geass", "Code Geass")
        episodes = [ani_py.Episode(str(100 + i), str(i)) for i in range(1, 5)]
        app._episodes_with_fallback = Mock(return_value=(anime, episodes))
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                app._pick_episodes(anime, "4")

    def test_auto_next_status_line_shows_position_and_next_episode(self):
        app = object.__new__(ani_py.App)
        app.playback = Mock()
        app.playback.progress.return_value = (95.0, 240.0)
        anime = ani_py.Anime("frieren-999", "Frieren")
        buf = _Tty()
        with patch("ani_py.sys.stderr", new=buf):
            now = ani_py.NowPlaying()
            app._draw_status(now, anime, ani_py.Episode("103", "3"), 3, 12,
                             ani_py.Episode("104", "4"))
        out = buf.getvalue()
        self.assertIn("Frieren", out)
        self.assertIn("Ep 3 (3/12)", out)
        self.assertIn("01:35/04:00", out)
        self.assertIn("Ep 4", out)

    def test_auto_next_status_separates_episode_number_from_queue_position(self):
        # Starting mid-season, "Ep 19/8" read as "19 out of 8". The episode's
        # own number must never share a ratio with the queue length.
        app = object.__new__(ani_py.App)
        app.playback = Mock()
        app.playback.progress.return_value = None
        anime = ani_py.Anime("geass", "Code Geass: Lelouch of the Rebellion R2")
        buf = _Tty()
        with patch("ani_py.sys.stderr", new=buf):
            now = ani_py.NowPlaying()
            app._draw_status(now, anime, ani_py.Episode("19", "19"), 1, 8, None)
        out = re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())
        self.assertIn("Ep 19 (1/8)", out)
        self.assertNotIn("19/8", out)

    def test_auto_next_status_is_silent_when_not_a_terminal(self):
        buf = io.StringIO()
        with patch("ani_py.sys.stderr", new=buf):
            now = ani_py.NowPlaying()
            now.title("x")
            now.update("y")
            now.clear()
        self.assertEqual(buf.getvalue(), "")

    def test_now_playing_redraws_one_line_and_restores_it(self):
        buf = _Tty()
        with patch("ani_py.sys.stderr", new=buf):
            now = ani_py.NowPlaying()
            now.title("Frieren Ep 3/12")
            now.update("first", interval=0)
            now.update("second", interval=0)
            now.clear()
        out = buf.getvalue()
        self.assertIn("\033]2;Frieren Ep 3/12\007", out)
        self.assertIn("\r\033[Kfirst", out)
        self.assertIn("\r\033[Ksecond", out)
        self.assertTrue(out.endswith("\r\033[K"))
        # Redraws reuse one line instead of appending, so scrollback stays clean.
        self.assertNotIn("\n", out)

    def test_auto_next_reports_ipc_loss_instead_of_a_silent_stop(self):
        app = object.__new__(ani_py.App)
        app.args = app_args(auto_next=True)
        anime = ani_py.Anime("frieren-999", "Frieren")
        episodes = [ani_py.Episode("101", "1"), ani_py.Episode("102", "2")]
        app.playback = Mock()
        app.playback.auto_next_supported.return_value = True
        app.playback.wait_for_completion.return_value = "ipc-lost"
        app._play_episode = Mock(return_value=0)

        with patch("ani_py.sys.stderr", new=io.StringIO()) as stderr:
            self.assertEqual(app._run_auto_next(anime, episodes, [episodes[0]], "best"), 0)

        _mock(app, "_play_episode").assert_called_once()
        app.playback.stop.assert_called_once_with()
        self.assertIn("Lost contact with mpv", stderr.getvalue())

    def test_auto_next_applies_configured_queue_limit(self):
        app = object.__new__(ani_py.App)
        app.args = app_args(auto_next=True, auto_next_limit=1)
        anime = ani_py.Anime("frieren-999", "Frieren")
        episodes = [ani_py.Episode(str(101 + i), str(i + 1)) for i in range(4)]
        app.playback = Mock()
        app.playback.auto_next_supported.return_value = True
        app.playback.wait_for_completion.return_value = "eof"
        app._play_episode = Mock(return_value=0)
        app.history = Mock()

        with patch("ani_py.sys.stderr", new=io.StringIO()):
            self.assertEqual(app._run_auto_next(anime, episodes, episodes[:1], "best"), 0)

        self.assertEqual(_mock(app, "_play_episode").call_count, 1)

    def test_clear_history_short_circuits(self):
        app = object.__new__(ani_py.App)
        app.args = app_args(clear_history=True)
        app.history = Mock()
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            self.assertEqual(app.run(), 0)
        app.history.clear.assert_called_once()

    def _forget_app(self, entries, picks, answer="y", sort=None):
        app = object.__new__(ani_py.App)
        app.args = app_args(forget=True, query=[], sort=sort)
        app.history = Mock()
        app.history.load.return_value = entries
        app.menu = Mock()
        app.menu.choose.side_effect = lambda rows, prompt, **kw: [rows[i] for i in picks]
        app.menu.prompt_text.return_value = answer
        return app

    def test_forget_removes_only_picked_entries(self):
        entries = [
            ani_py.HistoryEntry("1", "hianime", "frieren-999", "Frieren", True),
            ani_py.HistoryEntry("4", "hianime", "one-piece-100", "One Piece", False),
        ]
        # File order: rows[1] is One Piece (stored last).
        app = self._forget_app(entries, picks=[1])
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            self.assertEqual(app.run(), 0)
        _mock(app.menu, "choose").assert_called_once()
        self.assertTrue(_mock(app.menu, "choose").call_args.kwargs["multi"])
        _mock(app.history, "remove").assert_called_once_with([entries[1]])

    def test_forget_sort_recent_shows_newest_first(self):
        entries = [
            ani_py.HistoryEntry("1", "hianime", "frieren-999", "Frieren", True),
            ani_py.HistoryEntry("4", "hianime", "one-piece-100", "One Piece", False),
        ]
        # --sort recent: rows[0] is One Piece (stored last).
        app = self._forget_app(entries, picks=[0], sort="recent")
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            self.assertEqual(app.run(), 0)
        _mock(app.history, "remove").assert_called_once_with([entries[1]])

    def test_forget_cancelled_at_prompt_writes_nothing(self):
        entries = [
            ani_py.HistoryEntry("1", "hianime", "frieren-999", "Frieren", True),
        ]
        app = self._forget_app(entries, picks=[0], answer="n")
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            self.assertEqual(app.run(), 0)
        _mock(app.history, "remove").assert_not_called()

    def test_forget_with_nothing_picked_writes_nothing(self):
        entries = [
            ani_py.HistoryEntry("1", "hianime", "frieren-999", "Frieren", True),
        ]
        app = self._forget_app(entries, picks=[])
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            self.assertEqual(app.run(), 0)
        _mock(app.menu, "prompt_text").assert_not_called()
        _mock(app.history, "remove").assert_not_called()

    def test_forget_never_reaches_search(self):
        entries = [
            ani_py.HistoryEntry("1", "hianime", "frieren-999", "Frieren", True),
        ]
        app = self._forget_app(entries, picks=[0])
        app._search_anime = Mock()
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            self.assertEqual(app.run(), 0)
        _mock(app, "_search_anime").assert_not_called()

    def test_forget_empty_history_fails(self):
        app = self._forget_app([], picks=[])
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                app.run()
        self.assertEqual(ctx.exception.code, 1)
        _mock(app.history, "remove").assert_not_called()


if __name__ == "__main__":
    unittest.main()
