import unittest
from typing import Any

import ani_py


SEARCH_HTML = """
<a class="nv-anime-thumb" href="/watch/naruto">
  <div class="nv-anime-title">Naruto</div>
</a>
"""

SERIES_HTML = """
<article class="nv-info-episode-item">
  <a class="nv-info-episode-main" href="/watch/naruto/ep-1"><span>Episode 1</span></a>
  <span>SUB</span><span>DUB</span>
</article>
<article class="nv-info-episode-item">
  <a class="nv-info-episode-main" href="/watch/naruto/ep-2"><span>Episode 2</span></a>
  <span>SUB</span>
</article>
"""

WATCH_HTML = """
<div class="nv-server-grid" data-id="sub">
  <button data-video="https://embed.example/sub-1"></button>
</div>
<div class="nv-server-grid" data-id="dub">
  <button data-video="https://embed.example/dub-1"></button>
</div>
"""


class FakeHttp:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "/browser?keyword=naruto" in url:
            return SEARCH_HTML
        if url.endswith("/watch/naruto"):
            return SERIES_HTML
        if url.endswith("/watch/naruto/ep-1"):
            return WATCH_HTML
        if url == "https://embed.example/sub-1":
            return 'const src = "https://cdn.example/sub/master.m3u8";'
        if url == "https://embed.example/dub-1":
            return 'file: "https://cdn.example/dub/master.m3u8"'
        if url in {
            "https://cdn.example/sub/master.m3u8",
            "https://cdn.example/dub/master.m3u8",
        }:
            return """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=1280x720
720.m3u8
"""
        raise AssertionError(url)


class TestAniNekoProvider(unittest.TestCase):
    def setUp(self):
        self.http: Any = FakeHttp()
        self.provider = ani_py.AniNekoProvider(self.http)

    def test_search(self):
        self.assertEqual(
            self.provider.search("naruto"),
            [ani_py.Anime("naruto", "Naruto", "anineko")],
        )

    def test_episodes_and_audio_flags(self):
        anime = ani_py.Anime("naruto", "Naruto", "anineko")
        episodes = self.provider.episodes(anime)
        self.assertEqual([ep.number for ep in episodes], ["1", "2"])
        with self.assertRaises(ani_py.StreamNotFound):
            self.provider.resolve(anime, episodes[1], "dub")

    def test_resolve_sub_hls(self):
        anime = ani_py.Anime("naruto", "Naruto", "anineko")
        episode = self.provider.episodes(anime)[0]
        bundle = self.provider.resolve(anime, episode, "sub")
        self.assertEqual(bundle.provider, "anineko")
        self.assertEqual(bundle.referer, "https://embed.example/")
        self.assertEqual(bundle.streams[0].quality, "720p")
        self.assertEqual(bundle.streams[0].url, "https://cdn.example/sub/720.m3u8")

    def test_resolve_dub_hls(self):
        anime = ani_py.Anime("naruto", "Naruto", "anineko")
        episode = self.provider.episodes(anime)[0]
        bundle = self.provider.resolve(anime, episode, "dub")
        self.assertEqual(bundle.streams[0].url, "https://cdn.example/dub/720.m3u8")

    def test_preflight_marks_dead_site_unavailable_and_memoizes(self):
        calls = []

        class DeadHttp:
            def get(self, url, **kwargs) -> str:
                calls.append(url)
                raise ani_py.HttpError("timeout")

        http: Any = DeadHttp()
        provider = ani_py.AniNekoProvider(http)
        self.assertFalse(provider.available())
        self.assertFalse(provider.available())
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
