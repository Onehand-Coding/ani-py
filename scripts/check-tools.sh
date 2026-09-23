#!/usr/bin/env bash
set -u

have() { command -v "$1" >/dev/null 2>&1; }
show() {
  local name="$1" kind="$2"
  if have "$name"; then
    printf '  [ok] %-10s %s\n' "$name" "$(command -v "$name")"
  else
    printf '  [%s] %-10s not found\n' "$kind" "$name"
  fi
}

echo "ani-py external tool check"
echo
show python3 required
show curl required
show mpv recommended
show fzf recommended
show rofi optional
show dmenu optional
show vlc optional
show yt-dlp optional
show ffmpeg optional
show ani-skip optional

echo
if have ani-skip; then
  help="$(ani-skip --help 2>&1 || true)"
  if printf '%s\n' "$help" | grep -Eq -- '(^|[[:space:]])-q([,[:space:]]|$)|--query'; then
    echo "  [ok] ani-skip supports -q/--query"
  else
    echo "  [warn] ani-skip was found, but -q/--query was not detected"
  fi
fi

echo
if ! have python3 || ! have curl; then
  echo "Required tools are missing."
  exit 1
fi

echo "Required tools are present."

if [[ -n "${TERMUX_VERSION:-}" || -n "${ANDROID_ROOT:-}" ]]; then
  echo
  echo "Termux/Android detected. Run ./scripts/check-termux.sh for Android player checks."
fi
