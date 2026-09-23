#!/usr/bin/env bash
set -u

if [[ -z "${TERMUX_VERSION:-}" && -z "${ANDROID_ROOT:-}" ]]; then
  echo "[warn] Termux/Android environment variables were not detected."
fi

find_tool() {
  command -v "$1" 2>/dev/null || { [[ -x "/system/bin/$1" ]] && printf '/system/bin/%s\n' "$1"; }
}

AM="$(find_tool am || true)"
PM="$(find_tool pm || true)"

echo "ani-py Termux check"
echo "  TERMUX_VERSION: ${TERMUX_VERSION:-not set}"
echo "  am: ${AM:-not found}"
echo "  pm: ${PM:-not found}"

if [[ -z "$AM" || -z "$PM" ]]; then
  echo "[fail] Android activity/package manager tools are unavailable."
  exit 1
fi

check_pkg() {
  local package="$1" label="$2"
  if "$PM" path "$package" 2>/dev/null | grep -q '^package:'; then
    echo "  [ok] $label ($package)"
    return 0
  fi
  echo "  [--] $label not installed ($package)"
  return 1
}

mpv_ok=0
vlc_ok=0
check_pkg is.xyz.mpv "mpv-android" && mpv_ok=1
check_pkg org.videolan.vlc "VLC for Android" && vlc_ok=1

if [[ $mpv_ok -eq 0 && $vlc_ok -eq 0 ]]; then
  echo "[fail] Install mpv-android or VLC for Android before live playback testing."
  exit 1
fi

echo "[ok] Termux player prerequisites look usable."
