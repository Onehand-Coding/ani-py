#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p dist
cp ani_py.py dist/ani-py
chmod +x dist/ani-py
python3 -m py_compile dist/ani-py
rm -rf dist/__pycache__
./dist/ani-py --version
printf 'Built %s\n' "$ROOT/dist/ani-py"
