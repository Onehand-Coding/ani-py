import unittest

import ani_py


SEARCH_HTML = """
<div class="ani items">
  <div class="item"><a class="name" href="/watch/naruto">Naruto</a></div>
</div>
"""

SHOW_HTML = '<div id="watch-main" data-id="20"></div>'

EPISODE_HTML = """
<ul class="ep-range">
<li><a data-slug="1" data-num="1" data-mal="20" data-timestamp="12345"
       data-ids="server-key" data-sub="1" data-dub="1">Episode 1</a></li>
<li><a data-slug="2" data-num="2" data-mal="20" data-timestamp="12346"
       data-ids="server-key-2" data-sub="1" data-dub="0">Episode 2</a></li>
</ul>
"""

SERVER_HTML = """
<div class="type" data-type="sub">
  <ul><li data-link-id="sub-link">Vidstream</li></ul>
</div>
<div class="type" data-type="dub">
  <ul><li data-link-id="dub-link">Vidstream Dub</li></ul>
</div>
"""


class FakeHttp:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        if "/filter?keyword=naruto" in url:
            return SEARCH_HTML
        if url.endswith("/watch/naruto"):
            return SHOW_HTML
        if url == "https://embed.example/sub":
            return 'file: "https://cdn.example/sub/master.m3u8"'
        if url == "https://embed.example/dub":
            return 'const src = "https://cdn.example/dub/master.m3u8"'
        if url in {
            "https://cdn.example/sub/master.m3u8",
            "https://cdn.example/dub/master.m3u8",
        }:
            return """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=1280x720
720/index.m3u8
"""
        raise AssertionError(url)

    def get_json(self, url, **kwargs):
        self.calls.append(("json", url, kwargs))
        if url.endswith("/ajax/episode/list/20"):
            return {"result": EPISODE_HTML}
        if "/ajax/server/list?servers=server-key" in url:
            return {"result": SERVER_HTML}
        if "/ajax/server?get=sub-link" in url:
            return {"result": {"url": "https://embed.example/sub", "skip_data": {"intro": [0, 90]}}}
        if "/ajax/server?get=dub-link" in url:
            return {"result": {"url": "https://embed.example/dub"}}
        if "mapper.mewcdn.online" in url:
            return {"status": 200}
        raise AssertionError(url)


class TestAniKotoProvider(unittest.TestCase):
    def setUp(self):
        self.http = FakeHttp()
        self.provider = ani_py.AniKotoProvider(self.http)

    def test_search(self):
        self.assertEqual(
            self.provider.search("naruto"),
            [ani_py.Anime("naruto", "Naruto", "anikoto")],
        )

    def test_episode_metadata_and_mal_id(self):
        anime = ani_py.Anime("naruto", "Naruto", "anikoto")
        episodes = self.provider.episodes(anime)
        self.assertEqual([ep.number for ep in episodes], ["1", "2"])
        bundle = self.provider.resolve(anime, episodes[0], "sub")
        self.assertEqual(bundle.mal_id, "20")
        self.assertEqual(bundle.provider, "anikoto")

    def test_resolve_sub_and_dub(self):
        anime = ani_py.Anime("naruto", "Naruto", "anikoto")
        episode = self.provider.episodes(anime)[0]
        sub = self.provider.resolve(anime, episode, "sub")
        dub = self.provider.resolve(anime, episode, "dub")
        self.assertEqual(sub.streams[0].url, "https://cdn.example/sub/720/index.m3u8")
        self.assertEqual(dub.streams[0].url, "https://cdn.example/dub/720/index.m3u8")

    def test_missing_dub_fails_cleanly(self):
        anime = ani_py.Anime("naruto", "Naruto", "anikoto")
        episode = self.provider.episodes(anime)[1]
        with self.assertRaises(ani_py.StreamNotFound):
            self.provider.resolve(anime, episode, "dub")

    def test_mapper_download_can_be_used_as_direct_fallback(self):
        anime = ani_py.Anime("naruto", "Naruto", "anikoto")
        self.provider._episode_cache["naruto"] = {
            "1": {
                "episode_id": "1",
                "server_ids": "",
                "mal_id": "20",
                "timestamp": "12345",
                "sub": True,
                "dub": False,
            }
        }

        original_get_json = self.http.get_json

        def mapper_only(url, **kwargs):
            if "mapper.mewcdn.online" in url:
                return {
                    "kiwi": {
                        "sub": {
                            "download": {
                                "720p": "https://cdn.example/direct-720.mp4"
                            }
                        }
                    }
                }
            return original_get_json(url, **kwargs)

        self.http.get_json = mapper_only
        bundle = self.provider.resolve(anime, ani_py.Episode("1", "1"), "sub")
        self.assertEqual(bundle.streams, [ani_py.Stream("720p", "https://cdn.example/direct-720.mp4")])


if __name__ == "__main__":
    unittest.main()
