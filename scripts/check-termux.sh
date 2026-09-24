#!/usr/bin/env bash
set -u

# Live-device helper for Termux/Android playback checks.
# Mirrors the app's launch order: normal `am` dispatch first, `termux-open`
# chooser second, existing rish/Shizuku only as an optional fallback.
# Player apps are NOT probed via `pm path`: package-manager queries from an
# ordinary Termux UID are unreliable, and intent dispatch does not need them.

if [[ -z "${TERMUX_VERSION:-}" && -z "${ANDROID_ROOT:-}" ]]; then
  echo "[warn] Termux/Android environment variables were not detected."
fi

find_tool() {
  command -v "$1" 2>/dev/null || { [[ -x "/system/bin/$1" ]] && printf '/system/bin/%s\n' "$1"; }
}

AM="$(find_tool am || true)"
OPEN="$(find_tool termux-open || true)"

echo "ani-py Termux check"
echo "  TERMUX_VERSION: ${TERMUX_VERSION:-not set}"
echo "  am: ${AM:-not found}"
echo "  termux-open: ${OPEN:-not found}"

if [[ -z "$AM" ]]; then
  echo "[fail] Android activity manager is unavailable; install termux-tools/termux-am."
  exit 1
fi

if [[ -x "$HOME/rish" ]]; then
  echo "  [ok] rish optional Shizuku fallback available ($HOME/rish)"
elif command -v rish >/dev/null 2>&1; then
  echo "  [ok] rish optional Shizuku fallback available ($(command -v rish))"
else
  echo "  [--] rish not found (not required)"
fi

echo "Install VLC and/or mpv-android from the Play Store / F-Droid;"
echo "ani-py dispatches VIEW intents without package-manager preflight."
echo "[ok] Termux dispatch prerequisites look usable."
