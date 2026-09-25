import base64
import json
import unittest

import ani_py


class FakeHttp:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, *, referer=None, timeout=12):
        self.calls.append((url, referer))
        return self.responses[url]


class TestProviderInternals(unittest.TestCase):
    def test_decode_embed_hash(self):
        encoded = base64.b64encode(b'https://example.org/embed/mal/123/abc').decode()
        self.assertEqual(
            ani_py.HianimeProvider._decode_embed_hash(encoded),
            'https://example.org/embed/mal/123/abc',
        )

    def test_deobfuscate_payload(self):
        payload = {'src': 'https://cdn.example/master.m3u8', 'subtitles': [{'src': 'https://sub.vtt', 'default': True}]}
        raw = json.dumps(payload).encode()
        blob = base64.b64encode(bytes(b ^ ani_py.XOR_KEY[i % len(ani_py.XOR_KEY)] for i, b in enumerate(raw))).decode()
        decoded = ani_py.HianimeProvider._deobfuscate(blob)
        self.assertEqual(decoded['src'], payload['src'])
        self.assertEqual(decoded['subtitles'][0]['src'], 'https://sub.vtt')

    def test_pick_source_and_subtitle(self):
        payload = {
            'nested': {
                'src': 'https://cdn.example/master.m3u8',
                'subtitles': [
                    {'src': 'https://sub-1.vtt', 'default': False},
                    {'src': 'https://sub-2.vtt', 'default': True},
                ],
            }
        }
        self.assertEqual(ani_py.HianimeProvider._pick_source_url(payload), 'https://cdn.example/master.m3u8')
        self.assertEqual(ani_py.HianimeProvider._pick_subtitle(payload), 'https://sub-2.vtt')

        info = ani_py.HianimeProvider._pick_subtitle_info({
            "subtitles": [
                {"src": "https://sub-es.vtt", "default": True, "label": "Spanish", "lang": "spa"}
            ]
        })
        self.assertEqual(info, ("https://sub-es.vtt", "es", "Spanish"))

    def test_parse_master_playlist(self):
        master = '''#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
low/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720
mid/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080
high/index.m3u8
'''
        streams = ani_py.HianimeProvider._parse_master(master, 'https://cdn.example/master.m3u8')
        self.assertEqual([s.quality for s in streams], ['1080p', '720p', '360p'])
        self.assertEqual(streams[0].url, 'https://cdn.example/high/index.m3u8')

    def test_search_scrape(self):
        html = '''<div class="film-detail"><h3 class="film-name"><a href="/watch/frieren-beyond-journeys-end-1" title="Frieren: Beyond Journey&#039;s End"></a></h3></div>
<div class="film-detail"><h3 class="film-name"><a href="/watch/dandadan-2" title="Dandadan"></a></h3></div>
<div id="main-sidebar"><div class="film-detail"><h3 class="film-name"><a href="/watch/dup" title="Ignore sidebar"></a></h3></div></div>'''
        provider = ani_py.HianimeProvider(FakeHttp({f'{ani_py.BASE_URL}/search?keyword=frieren': html}))
        found = provider.search('frieren')
        self.assertEqual([a.slug for a in found], ['frieren-beyond-journeys-end-1', 'dandadan-2'])
        self.assertEqual(found[0].title, "Frieren: Beyond Journey's End")


    def test_full_resolve_extracts_mal_id_streams_and_subtitle(self):
        embed_url = "https://embed.example/mal/52299/episode/abc"
        encoded = base64.b64encode(embed_url.encode()).decode()
        payload = {
            "src": "https://cdn.example/master.m3u8",
            "subtitles": [{
                "src": "https://cdn.example/en.vtt",
                "default": True,
                "label": "English",
                "language": "eng",
            }],
        }
        raw = json.dumps(payload).encode()
        blob = base64.b64encode(
            bytes(b ^ ani_py.XOR_KEY[i % len(ani_py.XOR_KEY)] for i, b in enumerate(raw))
        ).decode()
        responses = {
            f"{ani_py.BASE_URL}/api/theme/episode/servers?episodeId=111":
                f'<a class="server-item" data-type="sub" data-server-name="ZokoAnime" data-hash="{encoded}"></a>',
            embed_url: f'<script>window.__P="{blob}"</script>',
            "https://cdn.example/master.m3u8":
                "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720\n720/index.m3u8\n",
        }
        provider = ani_py.HianimeProvider(FakeHttp(responses))
        bundle = provider.resolve("frieren-999", ani_py.Episode("111", "1"), "sub")
        self.assertEqual(bundle.mal_id, "52299")
        self.assertEqual(bundle.referer, "https://embed.example/")
        self.assertEqual(bundle.subtitle, "https://cdn.example/en.vtt")
        self.assertEqual(bundle.subtitle_language, "en")
        self.assertEqual(bundle.subtitle_label, "English")
        self.assertEqual(bundle.streams[0].quality, "720p")
        self.assertEqual(bundle.streams[0].url, "https://cdn.example/720/index.m3u8")

    def test_episode_scrape(self):
        page = '''<a class="ep-item" data-number="1" data-id="111" href="/watch/frieren-999?ep=1"></a>
<a class="ep-item" data-number="2" data-id="112" href="/watch/frieren-999?ep=2"></a>'''
        provider = ani_py.HianimeProvider(FakeHttp({f'{ani_py.BASE_URL}/api/theme/episode/list/999': page}))
        episodes = provider.episodes('frieren-999')
        self.assertEqual([(e.episode_id, e.number) for e in episodes], [('111', '1'), ('112', '2')])


if __name__ == '__main__':
    unittest.main()
