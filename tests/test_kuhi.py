import unittest

import ani_py


class FakeKuhiHttp:
    def __init__(self):
        self.json_responses = {}
        self.text_responses = {}
        self.errors = {}
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append(("get_json", url, kwargs))
        if url in self.errors:
            raise self.errors[url]
        return self.json_responses[url]

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        if url in self.errors:
            raise self.errors[url]
        return self.text_responses[url]


class TestKuhiProvider(unittest.TestCase):
    def test_search_parses_anilist_results(self):
        http = FakeKuhiHttp()
        provider = ani_py.KuhiProvider(http)
        url = f"{ani_py.KUHI_BASE_URL}/anime/search?query=naruto&page=1&per_page=40"
        http.json_responses[url] = {
            "page": 1,
            "results": [
                {
                    "id": 20,
                    "title": {
                        "english": "Naruto",
                        "romaji": "Naruto",
                        "native": "NARUTO -ナルト-",
                    },
                },
                {
                    "id": 1735,
                    "title": {"english": "Naruto: Shippuden"},
                },
            ],
        }
        results = provider.search("naruto")
        self.assertEqual([x.provider for x in results], ["kuhi", "kuhi"])
        self.assertEqual([x.provider_id for x in results], ["20", "1735"])
        self.assertEqual(results[0].title, "Naruto")

    def test_search_accepts_wrapped_results(self):
        http = FakeKuhiHttp()
        provider = ani_py.KuhiProvider(http)
        url = f"{ani_py.KUHI_BASE_URL}/anime/search?query=frieren&page=1&per_page=40"
        http.json_responses[url] = {
            "success": True,
            "results": {
                "results": [
                    {"anilistId": 154587, "title": {"romaji": "Sousou no Frieren"}}
                ]
            },
        }
        results = provider.search("frieren")
        self.assertEqual([(x.provider_id, x.title) for x in results], [("154587", "Sousou no Frieren")])

    def test_episodes_merge_provider_sub_and_dub_lists(self):
        http = FakeKuhiHttp()
        provider = ani_py.KuhiProvider(http)
        url = f"{ani_py.KUHI_BASE_URL}/anime/episodes/20"
        http.json_responses[url] = {
            "anilistId": 20,
            "providers": {
                "anineko": {
                    "episodes": {
                        "sub": [{"number": 1}, {"number": 2}, {"number": 3.5}],
                        "dub": [{"number": 1}, {"number": 2}],
                    }
                },
                "kaa": {
                    "episodes": {
                        "sub": [{"number": 2}, {"number": 4}],
                        "dub": [{"number": 4}],
                    }
                },
            },
        }
        episodes = provider.episodes(ani_py.Anime("20", "Naruto", "kuhi"))
        self.assertEqual([e.number for e in episodes], ["1", "2", "3.5", "4"])
        self.assertEqual([e.episode_id for e in episodes], ["1", "2", "3.5", "4"])

    def test_resolve_returns_direct_streams_subtitle_referer_and_mal(self):
        http = FakeKuhiHttp()
        provider = ani_py.KuhiProvider(http)
        extract = f"{ani_py.KUHI_BASE_URL}/anime/extract/20?e=1&type=sub"
        info = f"{ani_py.KUHI_BASE_URL}/anime/info/20"
        http.json_responses[extract] = {
            "anilistId": 20,
            "provider": "anineko",
            "streams": [
                {
                    "type": "hls",
                    "url": "https://cdn.example/720.m3u8",
                    "quality": "720p",
                    "referer": "https://player.example/",
                },
                {
                    "type": "hls",
                    "url": "https://cdn.example/1080.m3u8",
                    "resolution": {"width": 1920, "height": 1080},
                    "headers": {"Referer": "https://player.example/"},
                },
                {
                    "type": "embed",
                    "url": "https://embed.example/e/abc",
                },
            ],
            "subtitles": [
                {"url": "https://cdn.example/en.vtt", "label": "English", "default": True},
            ],
        }
        http.json_responses[info] = {"idMal": 20}

        bundle = provider.resolve(ani_py.Anime("20", "Naruto", "kuhi"), ani_py.Episode("1", "1"), "sub")
        self.assertEqual(bundle.provider, "kuhi")
        self.assertEqual(bundle.mal_id, "20")
        self.assertEqual(bundle.referer, "https://player.example/")
        self.assertEqual(bundle.subtitle, "https://cdn.example/en.vtt")
        self.assertEqual([s.quality for s in bundle.streams], ["1080p", "720p"])
        self.assertEqual([s.url for s in bundle.streams], [
            "https://cdn.example/1080.m3u8",
            "https://cdn.example/720.m3u8",
        ])

    def test_resolve_expands_unlabelled_master_playlist(self):
        http = FakeKuhiHttp()
        provider = ani_py.KuhiProvider(http)
        extract = f"{ani_py.KUHI_BASE_URL}/anime/extract/20?e=1&type=sub"
        info = f"{ani_py.KUHI_BASE_URL}/anime/info/20"
        master_url = "https://cdn.example/master.m3u8"
        http.json_responses[extract] = {
            "streams": [
                {"type": "hls", "url": master_url, "referer": "https://player.example/"},
            ],
            "subtitles": [],
        }
        http.json_responses[info] = {"idMal": 20}
        http.text_responses[master_url] = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=854x480
480/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080
1080/index.m3u8
"""
        bundle = provider.resolve("20", ani_py.Episode("1", "1"), "sub")
        self.assertEqual([s.quality for s in bundle.streams], ["1080p", "480p"])
        self.assertEqual(bundle.streams[1].url, "https://cdn.example/480/index.m3u8")

    def test_resolve_rejects_embed_only_result(self):
        http = FakeKuhiHttp()
        provider = ani_py.KuhiProvider(http)
        extract = f"{ani_py.KUHI_BASE_URL}/anime/extract/20?e=1&type=sub"
        http.json_responses[extract] = {
            "streams": [{"type": "embed", "url": "https://embed.example/e/abc"}],
            "subtitles": [],
        }
        with self.assertRaises(ani_py.StreamNotFound):
            provider.resolve("20", ani_py.Episode("1", "1"), "sub")

    def test_deep_preflight_requires_direct_stream(self):
        http = FakeKuhiHttp()
        provider = ani_py.KuhiProvider(http)
        probe = f"{ani_py.KUHI_BASE_URL}/anime/extract/20?e=1&type=sub"
        http.json_responses[probe] = {
            "provider": "anineko",
            "streams": [{"type": "hls", "url": "https://cdn.example/master.m3u8"}],
        }
        self.assertTrue(provider.available())
        self.assertTrue(provider.available())
        calls = [call for call in http.calls if call[0] == "get_json" and call[1] == probe]
        self.assertEqual(len(calls), 1, "preflight should be memoized per provider instance")

    def test_deep_preflight_rejects_embed_only_response(self):
        http = FakeKuhiHttp()
        provider = ani_py.KuhiProvider(http)
        probe = f"{ani_py.KUHI_BASE_URL}/anime/extract/20?e=1&type=sub"
        http.json_responses[probe] = {
            "streams": [{"type": "embed", "url": "https://embed.example/e/abc"}],
        }
        self.assertFalse(provider.available())

    def test_deep_preflight_rejects_network_failure(self):
        http = FakeKuhiHttp()
        provider = ani_py.KuhiProvider(http)
        probe = f"{ani_py.KUHI_BASE_URL}/anime/extract/20?e=1&type=sub"
        http.errors[probe] = ani_py.HttpError("down")
        self.assertFalse(provider.available())


if __name__ == "__main__":
    unittest.main()
