import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestInstaller(unittest.TestCase):
    def _fixture_env(self, root: Path, *, checksum: str | None = None) -> tuple[dict[str, str], bytes]:
        fake_bin = root / "bin"
        fake_bin.mkdir()
        payload = b"#!/usr/bin/env python3\nprint('ani-py fixture')\n"
        payload_path = root / "payload"
        payload_path.write_bytes(payload)
        digest = checksum or hashlib.sha256(payload).hexdigest()
        sums_path = root / "SHA256SUMS"
        sums_path.write_text(f"{digest}  ani-py\n", encoding="utf-8")
        fake_curl = fake_bin / "curl"
        fake_curl.write_text(
            """#!/bin/sh
set -eu
out=""
url=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o)
      shift
      out=$1
      ;;
    http://*|https://*)
      url=$1
      ;;
  esac
  shift
done
case "$url" in
  */ani-py) cp "$FAKE_ANI_PY" "$out" ;;
  */SHA256SUMS) cp "$FAKE_SHA256SUMS" "$out" ;;
  *) echo "unexpected URL: $url" >&2; exit 22 ;;
esac
""",
            encoding="utf-8",
        )
        fake_curl.chmod(0o755)
        tmpdir = root / "tmp"
        tmpdir.mkdir()
        home = root / "home"
        home.mkdir()
        env = os.environ.copy()
        env.update({
            "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
            "FAKE_ANI_PY": str(payload_path),
            "FAKE_SHA256SUMS": str(sums_path),
            "TMPDIR": str(tmpdir),
            "HOME": str(home),
        })
        env.pop("ANI_PY_REF", None)
        env.pop("TERMUX_VERSION", None)
        env.pop("PREFIX", None)
        return env, payload

    def test_release_install_verifies_checksum_and_cleans_tempdir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env, payload = self._fixture_env(root)
            prefix = root / "prefix"
            proc = subprocess.run(
                ["sh", str(ROOT / "install.sh"), "--prefix", str(prefix)],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            target = prefix / "bin" / "ani-py"
            self.assertEqual(target.read_bytes(), payload)
            self.assertTrue(target.stat().st_mode & 0o111)
            self.assertEqual(list((root / "tmp").iterdir()), [])

    def test_checksum_mismatch_refuses_install(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env, _ = self._fixture_env(root, checksum="0" * 64)
            prefix = root / "prefix"
            proc = subprocess.run(
                ["sh", str(ROOT / "install.sh"), "--prefix", str(prefix)],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("checksum verification failed", proc.stderr)
            self.assertFalse((prefix / "bin" / "ani-py").exists())

    def test_termux_version_alone_selects_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env, payload = self._fixture_env(root)
            termux_prefix = root / "termux-prefix"
            env["TERMUX_VERSION"] = "0.119"
            env["PREFIX"] = str(termux_prefix)
            proc = subprocess.run(
                ["sh", str(ROOT / "install.sh")],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual((termux_prefix / "bin" / "ani-py").read_bytes(), payload)
            self.assertFalse((Path(env["HOME"]) / ".local" / "bin" / "ani-py").exists())


if __name__ == "__main__":
    unittest.main()
