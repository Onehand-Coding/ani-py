import argparse
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import ani_py


def args(**overrides):
    base = dict(
        download=False,
        player='mpv',
        player_flag=[],
        skip=False,
        no_detach=False,
        exit_after_play=False,
        ipc_socket=None,
        menu=None,
        menu_flags='',
        mode='sub',
        quality='best',
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class TestPlayback(unittest.TestCase):
    def test_private_ipc_socket_is_not_global_mpvsocket(self):
        with patch('ani_py.which_first', return_value='/usr/bin/mpv'):
            pb = ani_py.Playback(args())
            path = pb._make_ipc_path()
            self.assertTrue(str(path).endswith('.sock'))
            self.assertNotEqual(str(path), '/tmp/mpvsocket')
            self.assertIn('ani-py', path.name)

    def test_explicit_ipc_socket_override(self):
        with patch('ani_py.which_first', return_value='/usr/bin/mpv'):
            pb = ani_py.Playback(args(ipc_socket='~/custom-ani.sock'))
            path = pb._make_ipc_path()
            self.assertEqual(path, Path('~/custom-ani.sock').expanduser())

    def _mpv_cmd(self, **overrides: Any) -> list:
        kwargs: dict[str, Any] = dict(
            title='T E1', subtitle=None, referer='https://krussdomi.com/',
            mal_id=None, episode='1',
        )
        kwargs.update(overrides)
        with patch('ani_py.which_first', return_value='/usr/bin/mpv'):
            pb = ani_py.Playback(args())
            return pb._mpv_command(ani_py.Stream('720p', 'https://cdn.example/x.m3u8'), **kwargs)

    def test_mpv_command_sends_extra_headers(self):
        cmd = self._mpv_cmd(extra_headers={'Origin': 'https://krussdomi.com'})
        self.assertIn('--http-header-fields=Origin: https://krussdomi.com', cmd)

    def test_mpv_command_omits_header_flag_without_extra(self):
        cmd = self._mpv_cmd()
        self.assertFalse(any(a.startswith('--http-header-fields') for a in cmd))

    def test_replace_uses_fresh_process_when_extra_headers(self):
        with patch('ani_py.which_first', return_value='/usr/bin/mpv'):
            pb = ani_py.Playback(args())
            with patch.object(pb, 'active', return_value=True), \
                 patch.object(pb, 'stop') as stop, \
                 patch.object(pb, 'play', return_value=0) as play:
                rc = pb.replace(
                    ani_py.Stream('720p', 'https://cdn.example/x.m3u8'),
                    title='T E1', subtitle=None, referer='https://krussdomi.com/',
                    mal_id=None, episode='1',
                    extra_headers={'Origin': 'https://krussdomi.com'},
                )
                self.assertEqual(rc, 0)
                stop.assert_called_once_with()
                self.assertEqual(
                    play.call_args.kwargs['extra_headers'],
                    {'Origin': 'https://krussdomi.com'},
                )


if __name__ == '__main__':
    unittest.main()
