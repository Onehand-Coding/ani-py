import unittest

import ani_py


class FakeHttp:
    def __init__(self):
        self.calls = []

    def post_json(self, url, payload, **kwargs):
        self.calls.append(("post_json", url, payload, kwargs))
        if url.endswith("/api/fsearch"):
            return {
                "result": [
                    {
                        "slug": "naruto",
                        "title_en": "Naruto",
                        "title": "NARUTO",
                    }
                ]
            }
        raise AssertionError(url)

    def get_json(self, url, **kwargs):
        self.calls.append(("get_json", url, kwargs))
        if url.endswith("/api/show/naruto"):
            return {"type": "tv", "locales": ["ja-JP", "en-US"]}
        if "/api/show/naruto/episodes?ep=1&lang=ja-JP" in url:
            return {
                "result": [
                    {"episode_number": 1, "slug": "aaa"},
                    {"episode_number": 2, "slug": "bbb"},
                ],
                "pages": [
                    {"eps": [1, 2]},
                    {"eps": [3, 4]},
                ],
            }
        if "/api/show/naruto/episodes?ep=3&lang=ja-JP" in url:
            return {
                "result": [
                    {"episode_number": 3, "slug": "ccc"},
                    {"episode_number": 4, "slug": "ddd"},
                ]
            }
        if url.endswith("/api/show/naruto/episode/ep-1-aaa"):
            return {"servers": [{"src": "https://krussdomi.com/player?id=abc123"}]}
        raise AssertionError(url)

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        if url == "https://hls.krussdomi.com/manifest/abc123/master.m3u8":
            return """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=1280x720
720/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2200000,RESOLUTION=1920x1080
1080/index.m3u8
"""
        raise AssertionError(url)


class TestKaaProvider(unittest.TestCase):
    def setUp(self):
        self.http = FakeHttp()
        self.provider = ani_py.KaaProvider(self.http)

    def test_search(self):
        results = self.provider.search("naruto")
        self.assertEqual(results, [ani_py.Anime("naruto", "Naruto", "kaa")])

    def test_episode_pagination(self):
        anime = ani_py.Anime("naruto", "Naruto", "kaa")
        episodes = self.provider.episodes(anime)
        self.assertEqual([ep.number for ep in episodes], ["1", "2", "3", "4"])
        self.assertEqual(episodes[0].episode_id, "ep-1-aaa")

    def test_resolve_hls_and_qualities(self):
        anime = ani_py.Anime("naruto", "Naruto", "kaa")
        episode = self.provider.episodes(anime)[0]
        bundle = self.provider.resolve(anime, episode, "sub")
        self.assertEqual(bundle.provider, "kaa")
        self.assertEqual(bundle.referer, "https://krussdomi.com/")
        self.assertEqual([s.quality for s in bundle.streams], ["1080p", "720p"])
        master = next(c for c in self.http.calls if c[0] == "get")
        self.assertEqual(master[2]["referer"], "https://krussdomi.com/")

    def test_dub_allowed_when_english_locale_exists(self):
        anime = ani_py.Anime("naruto", "Naruto", "kaa")
        episode = self.provider.episodes(anime)[0]
        self.assertEqual(self.provider.resolve(anime, episode, "dub").provider, "kaa")

    def test_resolve_carries_origin_header_for_segments(self):
        anime = ani_py.Anime("naruto", "Naruto", "kaa")
        episode = self.provider.episodes(anime)[0]
        bundle = self.provider.resolve(anime, episode, "sub")
        # Segment host 403s without Origin; mpv must send it.
        self.assertEqual(bundle.extra_headers, {"Origin": "https://krussdomi.com"})


if __name__ == "__main__":
    unittest.main()
