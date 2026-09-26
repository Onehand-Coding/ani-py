import unittest

import ani_py


class FakeAniLightHttp:
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


class TestAniLightProvider(unittest.TestCase):
    def test_search_uses_anilist_slug_identity_and_caches_mal_metadata(self):
        http = FakeAniLightHttp()
        provider = ani_py.AniLightProvider(http)
        url = f"{ani_py.ANILIGHT_API_URL}/search?q=frieren"
        http.json_responses[url] = [
            {
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

    def test_episodes_parse_and_sort_numeric_episode_numbers(self):
        http = FakeAniLightHttp()
        provider = ani_py.AniLightProvider(http)
        anime = ani_py.Anime("154587:sousou-no-frieren", "Frieren", "anilight")
        url = f"{ani_py.ANILIGHT_API_URL}/watch/sousou-no-frieren"
        http.json_responses[url] = {
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
            ]
        }

        episodes = provider.episodes(anime)

        self.assertEqual([e.number for e in episodes], ["1", "1.5", "2"])
        self.assertEqual([e.episode_id for e in episodes], ["1", "1.5", "2"])
        provider.episodes(anime)
        watch_calls = [c for c in http.calls if c[1] == url]
        self.assertEqual(len(watch_calls), 1, "episode document should be cached")

    def test_resolve_extracts_hls_subtitle_mal_and_skip_markers(self):
        http = FakeAniLightHttp()
        provider = ani_py.AniLightProvider(http)
        anime = ani_py.Anime("154587:sousou-no-frieren", "Frieren", "anilight")
        watch_url = f"{ani_py.ANILIGHT_API_URL}/watch/sousou-no-frieren"
        variant_url = f"{ani_py.MEGAPLAY_BASE_URL}/api/12345"
        source_url = f"{ani_py.MEGAPLAY_BASE_URL}/stream/getSourcesNew?id=7001"
        master_url = "https://video.nekostream.site/frieren/master.m3u8"

        http.json_responses[watch_url] = {
            "episodes": [{
                "number": 1,
                "embed_url": {
                    "sub": "https://megaplay.buzz/stream/s-2/12345/sub",
                    "dub": "https://megaplay.buzz/stream/s-2/12345/dub",
                },
            }]
        }
        http.json_responses[variant_url] = {
            "success": 1,
            "data": [
                {"type": "sub", "episode_id": 7001, "embed_id": "abc"},
                {"type": "dub", "episode_id": 7002, "embed_id": "def"},
            ],
        }
        http.json_responses[source_url] = {
            "sources": {"file": master_url},
            "tracks": [
                {
                    "file": "https://video.nekostream.site/frieren/en.vtt",
                    "label": "English",
                    "kind": "captions",
                    "default": True,
                }
            ],
            "intro": {"start": 90, "end": 180},
            "outro": {"start": 1320, "end": 1410},
        }
        http.text_responses[master_url] = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=1280x720
720/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3500000,RESOLUTION=1920x1080
1080/index.m3u8
"""
        provider._info_cache[anime.provider_id] = {
            "slug": "sousou-no-frieren",
            "anilistId": 154587,
            "idMal": 52991,
        }

        bundle = provider.resolve(anime, ani_py.Episode("1", "1"), "sub")

        self.assertEqual(bundle.provider, "anilight")
        self.assertEqual(bundle.referer, ani_py.MEGAPLAY_REFERER)
        self.assertEqual(bundle.mal_id, "52991")
        self.assertEqual(bundle.subtitle, "https://video.nekostream.site/frieren/en.vtt")
        self.assertEqual(bundle.subtitle_language, "en")
        self.assertEqual(bundle.subtitle_label, "English")
        self.assertEqual(bundle.intro, (90.0, 180.0))
        self.assertEqual(bundle.outro, (1320.0, 1410.0))
        self.assertEqual([s.quality for s in bundle.streams], ["1080p", "720p"])
        self.assertEqual(
            bundle.streams[1].url,
            "https://video.nekostream.site/frieren/720/index.m3u8",
        )

        master_call = next(c for c in http.calls if c[0] == "get" and c[1] == master_url)
        self.assertEqual(master_call[2]["referer"], ani_py.MEGAPLAY_REFERER)

    def test_dub_requires_a_dub_embed(self):
        http = FakeAniLightHttp()
        provider = ani_py.AniLightProvider(http)
        anime = ani_py.Anime("20:naruto", "Naruto", "anilight")
        http.json_responses[f"{ani_py.ANILIGHT_API_URL}/watch/naruto"] = {
            "episodes": [{
                "number": 1,
                "embed_url": {"sub": "https://megaplay.buzz/stream/s-2/99/sub"},
            }]
        }

        with self.assertRaises(ani_py.StreamNotFound):
            provider.resolve(anime, ani_py.Episode("1", "1"), "dub")

    def test_sub_can_fall_back_to_hardsub_variant(self):
        http = FakeAniLightHttp()
        provider = ani_py.AniLightProvider(http)
        anime = ani_py.Anime("20:naruto", "Naruto", "anilight")
        http.json_responses[f"{ani_py.ANILIGHT_API_URL}/watch/naruto"] = {
            "episodes": [{
                "number": 1,
                "embed_url": {"sub": "https://megaplay.buzz/stream/s-2/99/sub"},
            }]
        }
        http.json_responses[f"{ani_py.MEGAPLAY_BASE_URL}/api/99"] = {
            "data": [{"type": "hsub", "episode_id": 500}]
        }
        http.json_responses[
            f"{ani_py.MEGAPLAY_BASE_URL}/stream/getSourcesNew?id=500"
        ] = {"sources": {"file": "https://cdn.example/video.m3u8"}}
        http.text_responses["https://cdn.example/video.m3u8"] = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n"

        bundle = provider.resolve(anime, ani_py.Episode("1", "1"), "sub")
        self.assertEqual(bundle.streams, [ani_py.Stream("auto", "https://cdn.example/video.m3u8")])

    def test_mal_id_can_be_loaded_from_anime_document(self):
        http = FakeAniLightHttp()
        provider = ani_py.AniLightProvider(http)
        anime = ani_py.Anime("154587:sousou-no-frieren", "Frieren", "anilight")
        info_url = f"{ani_py.ANILIGHT_API_URL}/anime/sousou-no-frieren"
        http.json_responses[info_url] = {"idMal": 52991}

        self.assertEqual(provider._mal_id(anime), "52991")

    def test_preflight_is_memoized(self):
        http = FakeAniLightHttp()
        provider = ani_py.AniLightProvider(http)
        url = f"{ani_py.ANILIGHT_API_URL}/search?q=naruto"
        http.json_responses[url] = [{
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
