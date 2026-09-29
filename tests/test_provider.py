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
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        return value


def _b64(text):
    return base64.b64encode(text.encode()).decode()


def _player_page(master_url):
    raw = json.dumps({"src": master_url}).encode()
    blob = base64.b64encode(
        bytes(b ^ ani_py.XOR_KEY[i % len(ani_py.XOR_KEY)] for i, b in enumerate(raw))
    ).decode()
    return f'<script>window.__P="{blob}"</script>'


def _server_item(mode, name, embed_url):
    return f'<a class="server-item" data-type="{mode}" data-server-name="{name}" data-hash="{_b64(embed_url)}"></a>'


SERVERS_URL = f"{ani_py.BASE_URL}/api/theme/episode/servers?episodeId=111"
TLS_ERROR = ani_py.HttpError("curl: (60) SSL: no alternative certificate subject name matches target hostname")


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

        tracks = ani_py.HianimeProvider._subtitle_tracks({
            "subtitles": [
                {"src": "https://sub-en.vtt", "default": True, "label": "English", "lang": "eng"},
                {"src": "https://sub-de.vtt", "label": "German", "lang": "deu"},
            ]
        })
        self.assertEqual([(t.language, t.label) for t in tracks], [("en", "English"), ("de", "German")])

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
            "subtitles": [
                {
                    "src": "https://cdn.example/en.vtt",
                    "default": True,
                    "label": "English",
                    "language": "eng",
                },
                {
                    "src": "https://cdn.example/de.vtt",
                    "label": "German",
                    "language": "deu",
                },
            ],
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
        self.assertEqual(
            [(track.language, track.label) for track in bundle.subtitles],
            [("en", "English"), ("de", "German")],
        )
        self.assertEqual(bundle.streams[0].quality, "720p")
        self.assertEqual(bundle.streams[0].url, "https://cdn.example/720/index.m3u8")

    def test_resolve_falls_back_to_next_server_when_hls_host_fails(self):
        zoko = "https://zoko.example/mal/1/a"
        other = "https://other.example/mal/1/b"
        responses = {
            SERVERS_URL: (
                _server_item("sub", "ZokoAnime", zoko)
                + _server_item("sub", "OtherServer", other)
                # A dub-only server must never be tried for a sub request.
                + _server_item("dub", "DubServer", "https://dub.example/mal/1/c")
            ),
            zoko: _player_page("https://broken-hls.example/master.m3u8"),
            "https://broken-hls.example/master.m3u8": TLS_ERROR,
            other: _player_page("https://good-hls.example/master.m3u8"),
            "https://good-hls.example/master.m3u8":
                "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720\n720/index.m3u8\n",
        }
        http = FakeHttp(responses)
        bundle = ani_py.HianimeProvider(http).resolve("frieren-999", ani_py.Episode("111", "1"), "sub")
        self.assertEqual(bundle.referer, "https://other.example/")
        self.assertEqual(bundle.streams[0].url, "https://good-hls.example/720/index.m3u8")
        self.assertNotIn("https://dub.example/mal/1/c", [url for url, _ in http.calls])

    def test_resolve_prefers_zokoanime_regardless_of_page_order(self):
        zoko = "https://zoko.example/mal/1/a"
        other = "https://other.example/mal/1/b"
        responses = {
            SERVERS_URL: _server_item("sub", "OtherServer", other) + _server_item("sub", "ZokoAnime", zoko),
            zoko: _player_page("https://zoko-hls.example/master.m3u8"),
            "https://zoko-hls.example/master.m3u8": "#EXTM3U\n",
        }
        http = FakeHttp(responses)
        bundle = ani_py.HianimeProvider(http).resolve("frieren-999", ani_py.Episode("111", "1"), "sub")
        self.assertEqual(bundle.referer, "https://zoko.example/")
        self.assertNotIn(other, [url for url, _ in http.calls])

    def test_resolve_skips_server_whose_payload_cannot_be_read(self):
        zoko = "https://zoko.example/mal/1/a"
        other = "https://other.example/mal/1/b"
        responses = {
            SERVERS_URL: _server_item("sub", "ZokoAnime", zoko) + _server_item("sub", "OtherServer", other),
            zoko: "<html>markup changed</html>",
            other: _player_page("https://good-hls.example/master.m3u8"),
            "https://good-hls.example/master.m3u8": "#EXTM3U\n",
        }
        bundle = ani_py.HianimeProvider(FakeHttp(responses)).resolve(
            "frieren-999", ani_py.Episode("111", "1"), "sub"
        )
        self.assertEqual(bundle.referer, "https://other.example/")

    def test_resolve_reports_first_failure_when_every_server_fails(self):
        zoko = "https://zoko.example/mal/1/a"
        other = "https://other.example/mal/1/b"
        responses = {
            SERVERS_URL: _server_item("sub", "ZokoAnime", zoko) + _server_item("sub", "OtherServer", other),
            zoko: _player_page("https://broken-hls.example/master.m3u8"),
            "https://broken-hls.example/master.m3u8": TLS_ERROR,
            other: ani_py.HttpError("HTTP 500"),
        }
        with self.assertRaises(ani_py.ProviderUnavailable) as ctx:
            ani_py.HianimeProvider(FakeHttp(responses)).resolve(
                "frieren-999", ani_py.Episode("111", "1"), "sub"
            )
        message = str(ctx.exception)
        self.assertIn("HiAnime HLS host failed", message)
        self.assertIn("OtherServer", message)

    def test_resolve_single_server_failure_keeps_original_error(self):
        zoko = "https://zoko.example/mal/1/a"
        responses = {
            SERVERS_URL: _server_item("sub", "ZokoAnime", zoko),
            zoko: _player_page("https://broken-hls.example/master.m3u8"),
            "https://broken-hls.example/master.m3u8": TLS_ERROR,
        }
        with self.assertRaises(ani_py.ProviderUnavailable) as ctx:
            ani_py.HianimeProvider(FakeHttp(responses)).resolve(
                "frieren-999", ani_py.Episode("111", "1"), "sub"
            )
        self.assertNotIn("other servers", str(ctx.exception))

    def test_resolve_without_any_server_for_mode_raises_stream_not_found(self):
        responses = {SERVERS_URL: _server_item("dub", "ZokoAnime", "https://zoko.example/mal/1/a")}
        with self.assertRaises(ani_py.StreamNotFound):
            ani_py.HianimeProvider(FakeHttp(responses)).resolve(
                "frieren-999", ani_py.Episode("111", "1"), "sub"
            )

    def test_episode_scrape(self):
        page = '''<a class="ep-item" data-number="1" data-id="111" href="/watch/frieren-999?ep=1"></a>
<a class="ep-item" data-number="2" data-id="112" href="/watch/frieren-999?ep=2"></a>'''
        provider = ani_py.HianimeProvider(FakeHttp({f'{ani_py.BASE_URL}/api/theme/episode/list/999': page}))
        episodes = provider.episodes('frieren-999')
        self.assertEqual([(e.episode_id, e.number) for e in episodes], [('111', '1'), ('112', '2')])


if __name__ == '__main__':
    unittest.main()
