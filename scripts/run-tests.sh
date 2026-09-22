#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[1/4] Compiling Python files..."
python3 -m py_compile ani_py.py ani-py

echo "[2/4] Running unit tests..."
python3 -m unittest discover -s tests -v

echo "[3/4] Smoke-checking CLI help/version..."
./ani-py --help >/dev/null
./ani-py --version

echo "[4/4] Building standalone artifact..."
./scripts/build-standalone.sh >/dev/null

echo "All checks passed."
