#!/usr/bin/env sh
set -eu

REPO="${ANI_PY_REPO:-Onehand-Coding/ani-py}"
REF="${ANI_PY_REF:-}"
WITH_DEPS=0
PREFIX_OVERRIDE=""

usage() {
  cat <<'EOF'
Usage: install.sh [--deps] [--prefix DIR]

Installs the standalone ani-py script.
  --deps        install core/recommended CLI dependencies when supported
  --prefix DIR  install under DIR/bin instead of the platform default
  -h, --help    show this help

Defaults:
  Linux/Unix    ~/.local/bin/ani-py
  Termux        $PREFIX/bin/ani-py
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --deps) WITH_DEPS=1 ;;
    --prefix)
      shift
      [ "$#" -gt 0 ] || { echo "error: --prefix requires a directory" >&2; exit 2; }
      PREFIX_OVERRIDE=$1
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

is_termux=0
if [ -n "${TERMUX_VERSION:-}" ] || { [ -n "${PREFIX:-}" ] && [ -d "${PREFIX:-}/etc/termux" ]; }; then
  is_termux=1
fi

if [ -n "$PREFIX_OVERRIDE" ]; then
  install_prefix=$PREFIX_OVERRIDE
elif [ "$is_termux" -eq 1 ]; then
  install_prefix=${PREFIX:-"$HOME/.local"}
else
  install_prefix="$HOME/.local"
fi

bindir="$install_prefix/bin"
target="$bindir/ani-py"
if [ -n "$REF" ]; then
  url="https://raw.githubusercontent.com/$REPO/$REF/ani_py.py"
  checksum_url=""
  echo "warning: ANI_PY_REF bypasses release checksum verification for development installs" >&2
else
  release_base="https://github.com/$REPO/releases/latest/download"
  url="$release_base/ani-py"
  checksum_url="$release_base/SHA256SUMS"
fi

need_root() {
  case "$1" in
    /usr|/usr/*|/opt|/opt/*) return 0 ;;
    *) return 1 ;;
  esac
}

run_pkg() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "error: dependency installation needs root/sudo on this system" >&2
    exit 1
  fi
}

install_deps() {
  if [ "$is_termux" -eq 1 ]; then
    command -v pkg >/dev/null 2>&1 || {
      echo "warning: Termux detected but pkg is unavailable; skipping dependency install" >&2
      return
    }
    pkg install -y python curl fzf termux-tools termux-am
    return
  fi

  if command -v apt-get >/dev/null 2>&1; then
    run_pkg apt-get update
    run_pkg apt-get install -y python3 curl fzf mpv
  elif command -v dnf >/dev/null 2>&1; then
    run_pkg dnf install -y python3 curl fzf mpv
  elif command -v pacman >/dev/null 2>&1; then
    run_pkg pacman -S --needed --noconfirm python curl fzf mpv
  elif command -v zypper >/dev/null 2>&1; then
    run_pkg zypper --non-interactive install python3 curl fzf mpv
  elif command -v apk >/dev/null 2>&1; then
    run_pkg apk add python3 curl fzf mpv
  else
    echo "warning: unsupported package manager; install python, curl, fzf, and a player manually" >&2
  fi
}

if [ "$WITH_DEPS" -eq 1 ]; then
  install_deps
fi

if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
  echo "warning: Python is not currently on PATH; ani-py requires Python 3.10+" >&2
fi

tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/ani-py-install.XXXXXX")
tmp="$tmp_dir/ani-py"
checksums="$tmp_dir/SHA256SUMS"
trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM

download() {
  source_url=$1
  destination=$2
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$source_url" -o "$destination"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$destination" "$source_url"
  else
    echo "error: curl or wget is required to download ani-py" >&2
    exit 1
  fi
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$1"
  elif command -v python >/dev/null 2>&1; then
    python -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$1"
  else
    echo "error: sha256sum, shasum, or Python is required to verify ani-py" >&2
    exit 1
  fi
}

download "$url" "$tmp"
if [ -n "$checksum_url" ]; then
  download "$checksum_url" "$checksums"
  expected=$(awk '$2 == "ani-py" || $2 == "*ani-py" { print $1; exit }' "$checksums")
  case "$expected" in
    ""|*[!0-9A-Fa-f]*) echo "error: release checksum file has no valid ani-py entry" >&2; exit 1 ;;
  esac
  [ "${#expected}" -eq 64 ] || {
    echo "error: release checksum file has no valid ani-py entry" >&2
    exit 1
  }
  actual=$(sha256_file "$tmp")
  if [ "$actual" != "$expected" ]; then
    echo "error: ani-py release checksum verification failed" >&2
    exit 1
  fi
fi

first_line=$(head -n 1 "$tmp" 2>/dev/null || true)
case "$first_line" in
  '#!'*) ;;
  *) echo "error: downloaded file does not look like an executable script" >&2; exit 1 ;;
esac

if need_root "$install_prefix" && [ "$(id -u)" -ne 0 ]; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "error: $install_prefix requires root; use --prefix or install sudo" >&2
    exit 1
  fi
  sudo mkdir -p "$bindir"
  sudo install -m 0755 "$tmp" "$target"
else
  mkdir -p "$bindir"
  install -m 0755 "$tmp" "$target"
fi

echo "Installed ani-py to $target"
if [ "$is_termux" -eq 1 ]; then
  echo "Install VLC for Android or mpv-android separately."
elif ! command -v mpv >/dev/null 2>&1 && ! command -v vlc >/dev/null 2>&1; then
  echo "Note: install mpv or VLC before playback."
fi

case ":$PATH:" in
  *":$bindir:"*) ;;
  *) echo "Add $bindir to PATH to run: ani-py" ;;
esac
