import unittest

import ani_py


class FakeAniLightHttp:
    def __init__(self):
        self.json_responses = {}
        self.errors = {}
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append(("get_json", url, kwargs))
        if url in self.errors:
            raise self.errors[url]
        return self.json_responses[url]


class TestAniLightProvider(unittest.TestCase):
    def test_search_uses_anilist_slug_identity_and_caches_mal_metadata(self):
        http = FakeAniLightHttp()
        provider = ani_py.AniLightProvider(http)
        url = f"{ani_py.ANILIGHT_API_URL}/search?q=frieren"
        http.json_responses[url] = [
            {
                "id": 1234,
                "slug": "sousou-no-frieren",
                "anilistId": 154587,
                "idMal": 52991,
                "title": {
                    "english": "Frieren: Beyond Journey's End",
                    "romaji": "Sousou no Frieren",
                },
            }
        ]

        results = provider.search("frieren")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].provider, "anilight")
        self.assertEqual(results[0].provider_id, "154587:sousou-no-frieren")
        self.assertEqual(results[0].title, "Frieren: Beyond Journey's End")
        self.assertEqual(provider._mal_id(results[0]), "52991")
        self.assertEqual(http.calls[0][2]["referer"], ani_py.ANILIGHT_BASE_URL + "/")
        self.assertIn("Origin", http.calls[0][2]["headers"])

    def test_episodes_parse_and_sort_and_cache_watch_document(self):
        http = FakeAniLightHttp()
        provider = ani_py.AniLightProvider(http)
        anime = ani_py.Anime("154587:sousou-no-frieren", "Frieren", "anilight")
        url = f"{ani_py.ANILIGHT_API_URL}/watch/sousou-no-frieren"
        http.json_responses[url] = {
            "id": 9876,
            "episodes": [
                {
                    "number": 2,
                    "embed_url": {
                        "sub": "https://megaplay.buzz/stream/s-2/202/sub",
                        "dub": "https://megaplay.buzz/stream/s-2/202/dub",
                    },
                },
                {
                    "number": 1,
                    "embed_url": {
                        "sub": "https://megaplay.buzz/stream/s-2/201/sub",
                    },
                },
                {
                    "number": 1.5,
                    "embed_url": {
                        "sub": "https://megaplay.buzz/stream/s-2/215/sub",
                    },
                },
            ],
        }

        episodes = provider.episodes(anime)

        self.assertEqual([e.number for e in episodes], ["1", "1.5", "2"])
        self.assertEqual([e.episode_id for e in episodes], ["1", "1.5", "2"])
        self.assertEqual(provider._numeric_id(anime), "9876")
        provider.episodes(anime)
        watch_calls = [c for c in http.calls if c[1] == url]
        self.assertEqual(len(watch_calls), 1, "watch document should be cached")

    def test_resolve_uses_anilight_numeric_id_and_portable_ryu_proxy(self):
        http = FakeAniLightHttp()
        provider = ani_py.AniLightProvider(http)
        anime = ani_py.Anime("154587:sousou-no-frieren", "Frieren", "anilight")
        watch_url = f"{ani_py.ANILIGHT_API_URL}/watch/sousou-no-frieren"
        source_url = (
            f"{ani_py.ANILIGHT_API_URL}/sources?"
            "id=9876&epNum=1&type=sub&providerId=ryu"
        )
        http.json_responses[watch_url] = {
            "id": 9876,
            "episodes": [{
                "number": 1,
                "embed_url": {
                    "sub": "https://megaplay.buzz/stream/s-2/12345/sub",
                    "dub": "https://megaplay.buzz/stream/s-2/12345/dub",
                },
            }],
        }
        http.json_responses[source_url] = {
            "sources": [
                {"url": "https://animegg.example/video-720.mp4", "quality": "720"},
                {"url": "https://animegg.example/video-1080.mp4", "quality": "1080p"},
            ],
            "tracks": [],
        }
        provider._info_cache[anime.provider_id] = {
            "id": 9876,
            "slug": "sousou-no-frieren",
            "anilistId": 154587,
            "idMal": 52991,
        }

        bundle = provider.resolve(anime, ani_py.Episode("1", "1"), "sub")

        self.assertEqual(bundle.provider, "anilight")
        self.assertEqual(bundle.referer, ani_py.ANILIGHT_BASE_URL + "/")
        self.assertEqual(bundle.mal_id, "52991")
        self.assertIsNone(bundle.subtitle)
        self.assertEqual([s.quality for s in bundle.streams], ["1080p", "720p"])
        self.assertTrue(
            bundle.streams[0].url.startswith(
                ani_py.ANILIGHT_API_URL + "/proxy/ryu?url="
            )
        )
        self.assertIn("video-1080.mp4", bundle.streams[0].url)

    def test_resolve_uses_watch_id_not_anilist_id_for_sources(self):
        http = FakeAniLightHttp()
        provider = ani_py.AniLightProvider(http)
        anime = ani_py.Anime("20:naruto", "Naruto", "anilight")
        http.json_responses[f"{ani_py.ANILIGHT_API_URL}/watch/naruto"] = {
            "id": 4321,
            "episodes": [{
                "number": 1,
                "embed_url": {
                    "sub": "https://megaplay.buzz/stream/s-2/99/sub",
                },
            }],
        }
        source_url = (
            f"{ani_py.ANILIGHT_API_URL}/sources?"
            "id=4321&epNum=1&type=sub&providerId=ryu"
        )
        http.json_responses[source_url] = {
            "sources": [{"url": "https://animegg.example/naruto.mp4", "quality": "720p"}]
        }

        provider.resolve(anime, ani_py.Episode("1", "1"), "sub")
        source_calls = [c for c in http.calls if "/sources?" in c[1]]
        self.assertEqual(source_calls[0][1], source_url)

    def test_dub_requires_dub_episode_marker(self):
        http = FakeAniLightHttp()
        provider = ani_py.AniLightProvider(http)
        anime = ani_py.Anime("20:naruto", "Naruto", "anilight")
        http.json_responses[f"{ani_py.ANILIGHT_API_URL}/watch/naruto"] = {
            "id": 4321,
            "episodes": [{
                "number": 1,
                "embed_url": {
                    "sub": "https://megaplay.buzz/stream/s-2/99/sub",
                },
            }],
        }

        with self.assertRaises(ani_py.StreamNotFound):
            provider.resolve(anime, ani_py.Episode("1", "1"), "dub")

    def test_missing_portable_source_fails_cleanly(self):
        http = FakeAniLightHttp()
        provider = ani_py.AniLightProvider(http)
        anime = ani_py.Anime("20:naruto", "Naruto", "anilight")
        http.json_responses[f"{ani_py.ANILIGHT_API_URL}/watch/naruto"] = {
            "id": 4321,
            "episodes": [{
                "number": 1,
                "embed_url": {
                    "sub": "https://megaplay.buzz/stream/s-2/99/sub",
                },
            }],
        }
        http.json_responses[
            f"{ani_py.ANILIGHT_API_URL}/sources?"
            "id=4321&epNum=1&type=sub&providerId=ryu"
        ] = {"sources": []}

        with self.assertRaises(ani_py.StreamNotFound):
            provider.resolve(anime, ani_py.Episode("1", "1"), "sub")

    def test_mal_id_can_be_loaded_from_anime_document(self):
        http = FakeAniLightHttp()
        provider = ani_py.AniLightProvider(http)
        anime = ani_py.Anime("154587:sousou-no-frieren", "Frieren", "anilight")
        info_url = f"{ani_py.ANILIGHT_API_URL}/anime/sousou-no-frieren"
        http.json_responses[info_url] = {"id": 9876, "idMal": 52991}

        self.assertEqual(provider._mal_id(anime), "52991")

    def test_preflight_is_memoized(self):
        http = FakeAniLightHttp()
        provider = ani_py.AniLightProvider(http)
        url = f"{ani_py.ANILIGHT_API_URL}/search?q=naruto"
        http.json_responses[url] = [{
            "id": 4321,
            "slug": "naruto",
            "anilistId": 20,
            "title": {"english": "Naruto"},
        }]

        self.assertTrue(provider.available())
        self.assertTrue(provider.available())
        calls = [c for c in http.calls if c[1] == url]
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
