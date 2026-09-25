#!/usr/bin/env sh
set -eu

PREFIX_OVERRIDE=""

usage() {
  cat <<'EOF'
Usage: uninstall.sh [--prefix DIR]

Removes the ani-py executable installed by install.sh.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
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
if [ -n "${TERMUX_VERSION:-}" ] || [ -n "${PREFIX:-}" ] && [ -d "${PREFIX:-}/etc/termux" ]; then
  is_termux=1
fi

if [ -n "$PREFIX_OVERRIDE" ]; then
  install_prefix=$PREFIX_OVERRIDE
elif [ "$is_termux" -eq 1 ]; then
  install_prefix=${PREFIX:-"$HOME/.local"}
else
  install_prefix="$HOME/.local"
fi

target="$install_prefix/bin/ani-py"

case "$target" in
  /usr/*|/opt/*)
    if [ "$(id -u)" -eq 0 ]; then
      rm -f "$target"
    elif command -v sudo >/dev/null 2>&1; then
      sudo rm -f "$target"
    else
      echo "error: removing $target requires root/sudo" >&2
      exit 1
    fi
    ;;
  *) rm -f "$target" ;;
esac

echo "Removed $target"
