import argparse
import unittest
from pathlib import Path
from unittest.mock import patch

import ani_py


def args(**overrides):
    base = dict(
        download=False,
        player='mpv',
        vlc=False,
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


if __name__ == '__main__':
    unittest.main()
