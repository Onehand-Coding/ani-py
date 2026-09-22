import unittest
from unittest.mock import patch

import ani_py


class FakeKaiHttp:
    def __init__(self):
        self.json_responses = {}
        self.text_responses = {}
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append(("get_json", url, kwargs))
        return self.json_responses[url]

    def post_json(self, url, payload, **kwargs):
        self.calls.append(("post_json", url, payload, kwargs))
        return self.json_responses[(url, payload["text"])]

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return self.text_responses[url]


class TestAnimeKaiProvider(unittest.TestCase):
    BASE = "https://animekai.test"

    def make_provider(self, http):
        with patch.dict("os.environ", {"ANI_PY_ANIMEKAI_URL": self.BASE}):
            return ani_py.AnimeKaiProvider(http)

    def test_no_trusted_default_requires_explicit_mirror(self):
        http = FakeKaiHttp()
        with patch.dict("os.environ", {}, clear=True):
            provider = ani_py.AnimeKaiProvider(http)
        self.assertFalse(provider.available())
        with self.assertRaises(ani_py.ProviderUnavailable):
            provider.search("naruto")

    def test_search_parses_animekai_results(self):
        http = FakeKaiHttp()
        url = f"{self.BASE}/ajax/anime/search?keyword=naruto"
        http.json_responses[url] = {
            "result": {
                "html": '''
<a class="aitem" href="/watch/naruto-9r5k">
  <div class="poster"><img src="x"></div>
  <h6 class="title" data-jp="NARUTO">Naruto</h6>
</a>
<a class="aitem" href="/watch/naruto-shippuden-mv9v">
  <h6 class="title">Naruto Shippuden</h6>
</a>
'''
            }
        }
        provider = self.make_provider(http)
        results = provider.search("naruto")
        self.assertEqual([a.provider for a in results], ["animekai", "animekai"])
        self.assertEqual([a.provider_id for a in results], ["naruto-9r5k", "naruto-shippuden-mv9v"])
        self.assertEqual(results[0].title, "Naruto")

    def test_episode_list_uses_syncdata_and_token_helper(self):
        http = FakeKaiHttp()
        provider = self.make_provider(http)
        anime = ani_py.Anime("naruto-9r5k", "Naruto", "animekai")
        watch = f"{self.BASE}/watch/naruto-9r5k"
        http.text_responses[watch] = '''
<script id="syncData">{"anime_id":"abc123"}</script>
<a href="https://myanimelist.net/anime/20/Naruto">MAL</a>
'''
        enc = f"{ani_py.ANIMEKAI_ENC_URL}?text=abc123"
        http.json_responses[enc] = {"status": 200, "result": "ENCODED"}
        eps = f"{self.BASE}/ajax/episodes/list?ani_id=abc123&_=ENCODED"
        http.json_responses[eps] = {
            "result": '''
<div class="eplist">
  <a num="1" token="tok1" langs="3"><span>Enter: Naruto Uzumaki!</span></a>
  <a num="2" token="tok2" langs="3"><span>My Name is Konohamaru!</span></a>
</div>
'''
        }
        episodes = provider.episodes(anime)
        self.assertEqual([(e.episode_id, e.number) for e in episodes], [("tok1", "1"), ("tok2", "2")])
        self.assertEqual(provider._info_cache["naruto-9r5k"], ("abc123", "20"))

    def test_server_group_selection_keeps_sub_and_dub_separate(self):
        fragment = '''
<div class="server-items" data-id="sub">
  <span class="server" data-lid="sub1" data-sid="1">Server 1</span>
</div>
<div class="server-items" data-id="softsub">
  <span class="server" data-lid="soft1" data-sid="2">Server 2</span>
</div>
<div class="server-items" data-id="dub">
  <span class="server" data-lid="dub1" data-sid="3">Server 3</span>
</div>
'''
        groups = ani_py.AnimeKaiProvider._server_groups(fragment)
        self.assertEqual([s["link_id"] for s in ani_py.AnimeKaiProvider._mode_servers(groups, "sub")], ["sub1", "soft1"])
        self.assertEqual([s["link_id"] for s in ani_py.AnimeKaiProvider._mode_servers(groups, "dub")], ["dub1"])

    def test_full_resolve_returns_qualities_subtitle_and_mal_id(self):
        http = FakeKaiHttp()
        provider = self.make_provider(http)
        anime = ani_py.Anime("naruto-9r5k", "Naruto", "animekai")
        provider._info_cache[anime.provider_id] = ("abc123", "20")

        with patch.object(provider, "_encode", side_effect=lambda value: "ENC-" + value), \
             patch.object(provider, "_decrypt_kai", return_value={"url": "https://embed.example/e/vid123"}), \
             patch.object(provider, "_decrypt_mega", return_value={
                 "sources": [
                     {"file": "https://cdn.example/360.m3u8", "label": "360p"},
                     {"file": "https://cdn.example/720.m3u8", "label": "720p"},
                     {"file": "https://cdn.example/1080.m3u8", "label": "1080p"},
                 ],
                 "tracks": [
                     {"file": "https://cdn.example/en.vtt", "kind": "captions", "label": "English", "default": True}
                 ],
             }):
            links = f"{self.BASE}/ajax/links/list?token=tok1&_=ENC-tok1"
            http.json_responses[links] = {
                "result": '<div class="server-items" data-id="sub"><span class="server" data-lid="lid1" data-sid="1">Server 1</span></div>'
            }
            view = f"{self.BASE}/ajax/links/view?id=lid1&_=ENC-lid1"
            http.json_responses[view] = {"result": "encrypted-link"}
            http.text_responses["https://embed.example/e/vid123"] = "ok"
            http.json_responses["https://embed.example/media/vid123"] = {"result": "encrypted-media"}

            bundle = provider.resolve(anime, ani_py.Episode("tok1", "1"), "sub")

        self.assertEqual(bundle.provider, "animekai")
        self.assertEqual(bundle.mal_id, "20")
        self.assertEqual(bundle.subtitle, "https://cdn.example/en.vtt")
        self.assertEqual([s.quality for s in bundle.streams], ["1080p", "720p", "360p"])
        self.assertEqual(bundle.referer, "https://embed.example/e/vid123")


if __name__ == "__main__":
    unittest.main()
