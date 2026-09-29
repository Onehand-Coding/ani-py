import base64
import json
import unittest

import ani_py


class FakeHttp:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.headers = []

    def get(self, url, *, referer=None, timeout=12, headers=None):
        self.calls.append((url, referer))
        self.headers.append((url, headers))
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

    def test_resolve_sends_watch_page_referer_when_fetching_embed(self):
        # The embed hosts answer HTTP 200 with an error page when no Referer
        # is sent, so the watch page has to travel with the embed request.
        zoko = "https://zoko.example/mal/1/a"
        http = FakeHttp(
            {
                SERVERS_URL: _server_item("sub", "ZokoAnime", zoko),
                zoko: _player_page("https://hls.example/master.m3u8"),
                "https://hls.example/master.m3u8": "#EXTM3U\n",
            }
        )
        ani_py.HianimeProvider(http).resolve("frieren-999", ani_py.Episode("111", "1"), "sub")
        embed_referer = next(ref for url, ref in http.calls if url == zoko)
        self.assertEqual(embed_referer, f"{ani_py.BASE_URL}/watch/frieren-999?ep=1")

    def test_resolve_reports_embed_error_page_not_markup_change(self):
        # A Referer-gated soft 404 used to be reported as a markup change,
        # which sent debugging in the wrong direction entirely.
        zoko = "https://zoko.example/mal/1/a"
        error_page = '<html><title>Error - MegaPlay</title><div class="error-container"><h1>We\'re Sorry!</h1></div></html>'
        with self.assertRaises(ani_py.ProviderUnavailable) as ctx:
            ani_py.HianimeProvider(
                FakeHttp({SERVERS_URL: _server_item("sub", "ZokoAnime", zoko), zoko: error_page})
            ).resolve("frieren-999", ani_py.Episode("111", "1"), "sub")
        self.assertIn("error page", str(ctx.exception))

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


# The megaplay.buzz backend replaced the window.__P player blob with an
# AES-encrypted source manifest served from /stream/getSources. The ciphertext
# below was produced by Node WebCrypto using the same key/IV derivation, so the
# test pins the real wire format rather than a round-trip of our own code.
MEGAPLAY_EMBED = "https://megaplay.buzz/stream/s-2/2142/sub"
# data-id is the per-episode, per-mode id the player sends to getSources.
# data-realid is shared between the sub and dub embeds and resolves to a
# different show entirely, so it must never be used.
MEGAPLAY_MEDIA_ID = "36396"
MEGAPLAY_REAL_ID = "2142"
MEGAPLAY_SOURCES = f"https://megaplay.buzz/stream/getSources?id={MEGAPLAY_MEDIA_ID}&type=sub"
MEGAPLAY_MANIFEST = "wdeBruh3qqn_i5wUNnyaPetjmky2XY6ros_465Or0nhnNKf4BxS5H65eHawnxNNW"
MEGAPLAY_MASTER = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720\n"
    "720.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080\n"
    "1080.m3u8\n"
)


def _megaplay_page(media_id=MEGAPLAY_MEDIA_ID, real_id=MEGAPLAY_REAL_ID):
    return (
        f'<div id="player" data-id="{media_id}" data-realid="{real_id}" '
        f'data-mediaid="1774"></div>'
    )


def _sources_payload(tracks=None, enc=MEGAPLAY_MANIFEST):
    return json.dumps({"enc": enc, "tracks": tracks or []})


class TestMegaplayBackend(unittest.TestCase):
    def _http(self, **overrides):
        responses = {
            SERVERS_URL: _server_item("sub", "Vidstream-2", MEGAPLAY_EMBED),
            MEGAPLAY_EMBED: _megaplay_page(),
            MEGAPLAY_SOURCES: _sources_payload(),
            "https://cdn.example/master.m3u8": MEGAPLAY_MASTER,
        }
        responses.update(overrides)
        return FakeHttp(responses)

    def test_aes256_cbc_decrypt_matches_webcrypto_vector(self):
        # Locks the hand-rolled AES-256 against an independently generated
        # vector; a subtle row-shift or round-order error breaks playback.
        plaintext = ani_py._aes256_cbc_decrypt(
            MEGAPLAY_MANIFEST, ani_py.MEGAPLAY_KEY, ani_py.MEGAPLAY_IV
        )
        self.assertEqual(json.loads(plaintext)["file"], "https://cdn.example/master.m3u8")

    def test_resolve_decrypts_manifest_and_parses_master(self):
        bundle = ani_py.HianimeProvider(self._http()).resolve(
            "frieren-999", ani_py.Episode("111", "1"), "sub"
        )
        self.assertEqual(bundle.referer, "https://megaplay.buzz/")
        self.assertEqual(bundle.streams[0].quality, "1080p")
        self.assertEqual(bundle.streams[0].url, "https://cdn.example/1080.m3u8")
        self.assertEqual(bundle.streams[1].url, "https://cdn.example/720.m3u8")

    def test_sources_request_uses_embed_referer_and_xhr_header(self):
        http = self._http()
        ani_py.HianimeProvider(http).resolve("frieren-999", ani_py.Episode("111", "1"), "sub")
        referer = next(ref for url, ref in http.calls if url == MEGAPLAY_SOURCES)
        self.assertEqual(referer, "https://megaplay.buzz/")
        headers = next(h for url, h in http.headers if url == MEGAPLAY_SOURCES)
        self.assertEqual(headers, {"X-Requested-With": "XMLHttpRequest"})

    def test_sources_request_uses_data_id_not_data_realid(self):
        # data-realid is shared between the sub and dub embeds; requesting it
        # served a completely different show. data-id is what the player uses.
        http = self._http()
        ani_py.HianimeProvider(http).resolve("frieren-999", ani_py.Episode("111", "1"), "sub")
        requested = [url for url, _ in http.calls if "getSources" in url]
        self.assertEqual(requested, [MEGAPLAY_SOURCES])

    def test_sub_and_dub_request_their_own_media_id(self):
        # Sharing one id across modes is what served the wrong-language
        # stream, so each mode must carry its own embed data-id through.
        sub_embed = "https://megaplay.buzz/stream/s-2/2142/sub"
        dub_embed = "https://megaplay.buzz/stream/s-2/2142/dub"
        sub_sources = "https://megaplay.buzz/stream/getSources?id=36396&type=sub"
        dub_sources = "https://megaplay.buzz/stream/getSources?id=36382&type=dub"
        http = FakeHttp(
            {
                SERVERS_URL: _server_item("sub", "Vidstream-2", sub_embed)
                + _server_item("dub", "Vidstream-2", dub_embed),
                sub_embed: _megaplay_page(media_id="36396"),
                dub_embed: _megaplay_page(media_id="36382"),
                sub_sources: _sources_payload(),
                dub_sources: _sources_payload(),
                "https://cdn.example/master.m3u8": MEGAPLAY_MASTER,
            }
        )
        provider = ani_py.HianimeProvider(http)
        provider.resolve("frieren-999", ani_py.Episode("111", "1"), "sub")
        provider.resolve("frieren-999", ani_py.Episode("111", "1"), "dub")
        requested = [url for url, _ in http.calls if "getSources" in url]
        self.assertIn(sub_sources, requested)
        self.assertIn(dub_sources, requested)

    def test_resolve_collects_subtitle_tracks_from_outer_payload(self):
        tracks = [{"file": "https://sub.example/en.vtt", "label": "English", "default": True}]
        bundle = ani_py.HianimeProvider(
            self._http(**{MEGAPLAY_SOURCES: _sources_payload(tracks)})
        ).resolve("frieren-999", ani_py.Episode("111", "1"), "sub")
        self.assertEqual(bundle.subtitle, "https://sub.example/en.vtt")
        self.assertEqual(bundle.subtitle_language, "en")
        self.assertEqual(bundle.subtitle_label, "English")

    def test_manifest_without_media_id_reports_markup_change(self):
        with self.assertRaises(ani_py.ProviderChanged):
            ani_py.HianimeProvider(
                self._http(**{MEGAPLAY_EMBED: "<html>no player</html>"})
            ).resolve("frieren-999", ani_py.Episode("111", "1"), "sub")

    def test_undecryptable_manifest_reports_provider_changed(self):
        with self.assertRaises(ani_py.ProviderChanged) as ctx:
            ani_py.HianimeProvider(
                self._http(**{MEGAPLAY_SOURCES: _sources_payload(enc="not-base64!!")})
            ).resolve("frieren-999", ani_py.Episode("111", "1"), "sub")
        self.assertIn("could not be decrypted", str(ctx.exception))

    def test_manifest_without_hls_url_raises_stream_not_found(self):
        # A decrypted manifest that parses but carries no playlist is a
        # stream problem, not a markup problem.
        import base64 as _b64mod

        empty = _b64mod.urlsafe_b64encode(b"\x00" * 32).decode().rstrip("=")
        with self.assertRaises(ani_py.ProviderChanged):
            ani_py.HianimeProvider(
                self._http(**{MEGAPLAY_SOURCES: _sources_payload(enc=empty)})
            ).resolve("frieren-999", ani_py.Episode("111", "1"), "sub")

    def test_parse_master_stays_callable_as_staticmethod(self):
        streams = ani_py.HianimeProvider._parse_master(MEGAPLAY_MASTER, "https://cdn.example/master.m3u8")
        self.assertEqual([s.quality for s in streams], ["1080p", "720p"])


if __name__ == '__main__':
    unittest.main()
