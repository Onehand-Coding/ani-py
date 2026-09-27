import unittest

import ani_py


class TestCoreHelpers(unittest.TestCase):
    def setUp(self):
        self.episodes = [
            ani_py.Episode("101", "1"),
            ani_py.Episode("102", "2"),
            ani_py.Episode("103", "3"),
            ani_py.Episode("104", "4"),
            ani_py.Episode("105", "5.5"),
        ]

    def test_split_flags(self):
        self.assertEqual(ani_py.split_flags('--profile=fast --msg "hello world"'), ['--profile=fast', '--msg', 'hello world'])
        self.assertEqual(ani_py.split_flags('   '), [])

    def test_choose_quality_best(self):
        streams = [ani_py.Stream('480p', 'a'), ani_py.Stream('720p', 'b'), ani_py.Stream('1080p', 'c')]
        chosen = ani_py.choose_quality(streams, 'best')
        self.assertEqual(chosen.quality, '1080p')

    def test_choose_quality_worst(self):
        streams = [ani_py.Stream('480p', 'a'), ani_py.Stream('720p', 'b'), ani_py.Stream('1080p', 'c')]
        chosen = ani_py.choose_quality(streams, 'worst')
        self.assertEqual(chosen.quality, '480p')

    def test_choose_quality_numeric(self):
        streams = [ani_py.Stream('480p', 'a'), ani_py.Stream('720p', 'b'), ani_py.Stream('1080p', 'c')]
        chosen = ani_py.choose_quality(streams, '720')
        self.assertEqual(chosen.quality, '720p')

    def test_choose_subtitle_track_by_language_label_and_off(self):
        bundle = ani_py.StreamBundle(
            streams=[ani_py.Stream("720p", "video")],
            subtitle="https://subs/en.vtt",
            referer="https://embed/",
            mal_id="1",
            subtitles=[
                ani_py.SubtitleTrack("https://subs/en.vtt", "en", "English", True),
                ani_py.SubtitleTrack("https://subs/de.vtt", "de", "German"),
                ani_py.SubtitleTrack("https://subs/signs.vtt", "en", "Signs & Songs"),
            ],
        )
        self.assertEqual(ani_py.choose_subtitle_track(bundle, "de").url, "https://subs/de.vtt")
        self.assertEqual(
            ani_py.choose_subtitle_track(bundle, "label:Signs & Songs").url,
            "https://subs/signs.vtt",
        )
        self.assertIsNone(ani_py.choose_subtitle_track(bundle, "off"))
        self.assertIsNone(ani_py.choose_subtitle_track(bundle, "fr"))
        self.assertEqual(ani_py.choose_subtitle_track(bundle, "auto").url, "https://subs/en.vtt")

    def test_parse_episode_spec_single(self):
        result = ani_py.parse_episode_spec('3', self.episodes)
        self.assertEqual([e.number for e in result], ['3'])

    def test_parse_episode_spec_range(self):
        result = ani_py.parse_episode_spec('2-4', self.episodes)
        self.assertEqual([e.number for e in result], ['2', '3', '4'])

    def test_parse_episode_spec_reverse_range(self):
        result = ani_py.parse_episode_spec('4-2', self.episodes)
        self.assertEqual([e.number for e in result], ['4', '3', '2'])

    def test_parse_episode_spec_zero_and_minus_one(self):
        self.assertEqual([e.number for e in ani_py.parse_episode_spec('0', self.episodes)], ['1'])
        self.assertEqual([e.number for e in ani_py.parse_episode_spec('-1', self.episodes)], ['5.5'])

    def test_parse_episode_spec_decimal_range(self):
        result = ani_py.parse_episode_spec('4-5.5', self.episodes)
        self.assertEqual([e.number for e in result], ['4', '5.5'])

    def test_format_rows(self):
        anime_rows, anime_map = ani_py.format_anime_rows([ani_py.Anime('frieren-1', 'Frieren')])
        episode_rows, episode_map = ani_py.format_episode_rows([ani_py.Episode('ep1', '1')])
        self.assertIn('Frieren', anime_rows[0])
        self.assertIs(anime_map[anime_rows[0]].__class__, ani_py.Anime)
        self.assertEqual(episode_rows, ['Episode 1'])
        self.assertEqual(episode_map['Episode 1'].episode_id, 'ep1')


if __name__ == '__main__':
    unittest.main()
