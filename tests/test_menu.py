import io
import subprocess
import unittest
from unittest.mock import patch

import ani_py


class TestMenus(unittest.TestCase):
    @patch("ani_py.run_capture")
    @patch("ani_py.shutil.which", return_value="/usr/bin/fzf")
    def test_fzf_multi_menu_flags(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="Episode 1\nEpisode 3\n", stderr="")
        menu = ani_py.Menu("fzf")
        result = menu.choose(["Episode 1", "Episode 2", "Episode 3"], "Episode › ", multi=True)
        self.assertEqual(result, ["Episode 1", "Episode 3"])
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[0], "fzf")
        self.assertIn("--multi", cmd)
        self.assertIn("tab:toggle+down", cmd)
        self.assertIn("--border=rounded", cmd)

    @patch("ani_py.run_capture")
    @patch("ani_py.shutil.which", return_value="/usr/bin/rofi")
    def test_rofi_backend(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="Episode 2\n", stderr="")
        menu = ani_py.Menu("rofi")
        result = menu.choose(["Episode 1", "Episode 2"], "Episode › ")
        self.assertEqual(result, ["Episode 2"])
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[0], "rofi")
        self.assertIn("-dmenu", cmd)
        self.assertIn("-i", cmd)

    @patch("ani_py.run_capture")
    @patch("ani_py.shutil.which", return_value="/usr/bin/dmenu")
    def test_dmenu_backend(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="Episode 1\n", stderr="")
        menu = ani_py.Menu("dmenu")
        result = menu.choose(["Episode 1", "Episode 2"], "Episode › ")
        self.assertEqual(result, ["Episode 1"])
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[0], "dmenu")
        self.assertIn("-l", cmd)

    @patch("ani_py.input", return_value="1,3-4")
    @patch("ani_py.shutil.which", return_value=None)
    def test_numbered_fallback_multi_selection(self, mock_which, mock_input):
        menu = ani_py.Menu("fzf")
        with patch("ani_py.sys.stdout", new=io.StringIO()):
            result = menu.choose(["one", "two", "three", "four"], "Pick › ", multi=True)
        self.assertEqual(result, ["one", "three", "four"])

    @patch("ani_py.run_capture")
    @patch("ani_py.shutil.which", return_value="/usr/bin/fzf")
    def test_compact_fzf_menu_is_small(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="Replay\n", stderr="")
        menu = ani_py.Menu("fzf")
        menu.choose(["Replay", "Quit"], "Action › ", compact=True, header="Episode 1")
        cmd = mock_run.call_args.args[0]
        self.assertIn("--height=~12", cmd)
        self.assertIn("--info=hidden", cmd)
        self.assertIn("--header", cmd)


if __name__ == "__main__":
    unittest.main()
