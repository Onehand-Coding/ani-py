import argparse
import io
import unittest
from unittest.mock import Mock, patch

import ani_py


class FakeProvider(ani_py.Provider):
    def __init__(self, name, results=None, error=None):
        self.name = name
        self.display_name = name.title()
        self.results = results or []
        self.error = error

    def search(self, query):
        if self.error:
            raise self.error
        return self.results

    def episodes(self, anime):
        return [ani_py.Episode("1", "1")]

    def resolve(self, anime, episode, mode):
        return ani_py.StreamBundle([ani_py.Stream("720p", "u")], None, "r", None, self.name)


class TestProviderManager(unittest.TestCase):
    def test_auto_search_falls_back_after_provider_error(self):
        primary = FakeProvider("primary", error=ani_py.ProviderUnavailable("down"))
        backup = FakeProvider("backup", [ani_py.Anime("id", "Naruto", "backup")])
        manager = ani_py.ProviderManager([primary, backup], ["primary", "backup"])
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            results = manager.search("naruto", "auto")
        self.assertEqual(results[0].provider, "backup")

    def test_explicit_provider_does_not_fail_over(self):
        primary = FakeProvider("primary", error=ani_py.ProviderUnavailable("down"))
        backup = FakeProvider("backup", [ani_py.Anime("id", "Naruto", "backup")])
        manager = ani_py.ProviderManager([primary, backup], ["primary", "backup"])
        with self.assertRaises(ani_py.ProviderUnavailable):
            manager.search("naruto", "primary")

    def test_title_match_exact_is_automatic(self):
        app = object.__new__(ani_py.App)
        app.providers = Mock()
        app.menu = Mock()
        original = ani_py.Anime("a", "Frieren: Beyond Journey's End", "hianime")
        candidates = [
            ani_py.Anime("x", "Frieren: Beyond Journey's End", "animekai"),
            ani_py.Anime("y", "Frieren Season 2", "animekai"),
        ]
        chosen = app._choose_fallback_candidate(original, candidates)
        self.assertEqual(chosen.provider_id, "x")
        app.menu.choose.assert_not_called()

    def test_ambiguous_title_match_asks_user(self):
        app = object.__new__(ani_py.App)
        app.providers = Mock()
        app.providers.get.return_value.display_name = "AnimeKai"
        app.menu = Mock()
        original = ani_py.Anime("a", "One Piece", "hianime")
        candidates = [
            ani_py.Anime("x", "One Piece Movie", "animekai"),
            ani_py.Anime("y", "One Piece Fan Letter", "animekai"),
        ]
        # Return the first rendered row, proving we don't silently choose it.
        app.menu.choose.side_effect = lambda rows, *args, **kwargs: [rows[0]]
        chosen = app._choose_fallback_candidate(original, candidates)
        self.assertEqual(chosen.provider_id, "x")
        app.menu.choose.assert_called_once()


class ResolveProvider(ani_py.Provider):
    def __init__(self, name, title, fail_resolve=False):
        self.name = name
        self.display_name = name.title()
        self.title = title
        self.fail_resolve = fail_resolve
        self.capabilities = ani_py.ProviderCapabilities(sub=True, dub=True)

    def search(self, query):
        return [ani_py.Anime(self.name + "-id", self.title, self.name)]

    def episodes(self, anime):
        return [ani_py.Episode(self.name + "-ep1", "1")]

    def resolve(self, anime, episode, mode):
        if self.fail_resolve:
            raise ani_py.StreamNotFound("primary stream down")
        return ani_py.StreamBundle(
            [ani_py.Stream("720p", "https://backup/720.m3u8")],
            None,
            "https://backup/",
            None,
            self.name,
        )


class TestAppProviderFailover(unittest.TestCase):
    def test_stream_resolution_fails_over_to_matching_backup_episode(self):
        primary = ResolveProvider("hianime", "Frieren", fail_resolve=True)
        backup = ResolveProvider("animekai", "Frieren", fail_resolve=False)
        app = object.__new__(ani_py.App)
        app.args = argparse.Namespace(provider="auto", mode="sub")
        app.providers = ani_py.ProviderManager([primary, backup], ["hianime", "animekai"])
        app.menu = Mock()
        app.bundle_cache = {}
        app.episode_cache = {}
        app.fallback_map = {}
        anime = ani_py.Anime("hianime-id", "Frieren", "hianime")
        episode = ani_py.Episode("hianime-ep1", "1")
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            bundle = app._bundle(anime, episode)
        self.assertEqual(bundle.provider, "animekai")
        self.assertEqual(bundle.streams[0].quality, "720p")
        self.assertEqual(app.fallback_map[("hianime", "hianime-id")].provider, "animekai")


class TestExperimentalPreflight(unittest.TestCase):
    def _animekai(self, http):
        provider = ani_py.AnimeKaiProvider.__new__(ani_py.AnimeKaiProvider)
        provider.http = http
        provider.base = "https://example.test"
        provider._info_cache = {}
        provider._available = None
        return provider

    def test_preflight_passes_on_valid_fixture(self):
        http = Mock()
        http.get_json.return_value = {"result": {"html": '<a class="aitem" href="/watch/x">t</a>'}}
        provider = self._animekai(http)
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            self.assertTrue(provider.available())
            self.assertTrue(provider.available())
        http.get_json.assert_called_once()

    def test_preflight_fails_on_challenge_page(self):
        http = Mock()
        http.get_json.side_effect = ani_py.HttpError("Expected JSON from https://example.test")
        provider = self._animekai(http)
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            self.assertFalse(provider.available())

    def test_preflight_fails_on_schema_mismatch(self):
        http = Mock()
        http.get_json.return_value = {"unexpected": True}
        provider = self._animekai(http)
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            self.assertFalse(provider.available())

    def test_no_base_means_unavailable(self):
        provider = self._animekai(Mock())
        provider.base = ""
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            self.assertFalse(provider.available())

    def test_auto_search_skips_unavailable_provider(self):
        primary = FakeProvider("primary", [ani_py.Anime("id", "Naruto", "primary")])
        backup = FakeProvider("backup", [ani_py.Anime("id", "Naruto", "backup")])
        backup.available = lambda: False
        manager = ani_py.ProviderManager([primary, backup], ["primary", "backup"])
        with patch("ani_py.sys.stderr", new=io.StringIO()):
            results = manager.search("naruto", "auto")
        self.assertEqual(results[0].provider, "primary")

    def test_fallback_names_excludes_unavailable_provider(self):
        primary = FakeProvider("primary")
        backup = FakeProvider("backup")
        backup.available = lambda: False
        manager = ani_py.ProviderManager([primary, backup], ["primary", "backup"])
        self.assertEqual(manager.fallback_names("primary"), [])


if __name__ == "__main__":
    unittest.main()
