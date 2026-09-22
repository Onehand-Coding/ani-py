import argparse
import io
import subprocess
import unittest
from unittest.mock import patch

import ani_py


class TestHttpClient(unittest.TestCase):
    @patch("ani_py.run_capture")
    @patch("ani_py.which_first", return_value="/usr/bin/curl")
    def test_get_builds_curl_request_and_parses_status(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="hello\n__ANI_PY_HTTP__200", stderr=""
        )
        client = ani_py.HttpClient()
        body = client.get("https://example.test/a", referer="https://ref.example/")
        self.assertEqual(body, "hello")
        cmd = mock_run.call_args.args[0]
        self.assertEqual(cmd[0], "/usr/bin/curl")
        self.assertIn("-L", cmd)
        self.assertIn("-A", cmd)
        self.assertIn(ani_py.USER_AGENT, cmd)
        self.assertIn("-e", cmd)
        self.assertIn("https://ref.example/", cmd)
        self.assertEqual(cmd[-1], "https://example.test/a")

    @patch("ani_py.run_capture")
    @patch("ani_py.which_first", return_value="/usr/bin/curl")
    def test_non_2xx_is_reported(self, mock_which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="missing\n__ANI_PY_HTTP__404", stderr=""
        )
        client = ani_py.HttpClient()
        with self.assertRaises(ani_py.HttpError):
            client.get("https://example.test/missing")


if __name__ == "__main__":
    unittest.main()
