#!/usr/bin/env bash
set -euo pipefail

KUHI_URL="${ANI_PY_KUHI_URL:-https://anime-scraper-v2.vercel.app}"
PROBE="${KUHI_URL%/}/anime/extract/20?e=1&type=sub"

echo "ani-py live provider check"
echo "Kuhi: $PROBE"

payload="$(curl -fsSL --max-time 30 -A 'Mozilla/5.0' "$PROBE")" || {
  echo "[fail] Kuhi request failed" >&2
  exit 1
}

printf '%s' "$payload" | python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception as exc:
    raise SystemExit(f"[fail] Kuhi returned invalid JSON: {exc}")

if not isinstance(data, dict):
    raise SystemExit("[fail] Kuhi returned a non-object response")
if data.get("success") is False:
    message = data.get("message", "request failed")
    raise SystemExit(f"[fail] Kuhi reported failure: {message}")

payload = data.get("results") if isinstance(data.get("results"), dict) else data
streams = payload.get("streams") if isinstance(payload, dict) else None
if not isinstance(streams, list):
    raise SystemExit("[fail] Kuhi response has no streams list")

direct = []
for item in streams:
    if not isinstance(item, dict):
        continue
    url = item.get("url") or item.get("file") or item.get("src")
    kind = str(item.get("type") or "").lower()
    if isinstance(url, str) and url.startswith(("http://", "https://")) and kind != "embed":
        direct.append(item)

if not direct:
    raise SystemExit("[fail] Kuhi returned no direct playable HTTP(S) stream")

provider = payload.get("provider", "unknown provider")
print(f"[ok] Kuhi resolved {len(direct)} direct stream(s) via {provider}")
for item in direct[:3]:
    kind = item.get("type", "stream")
    quality = item.get("quality", "auto")
    url = item.get("url") or item.get("file") or item.get("src") or ""
    print(f"     {kind:>6}  {quality:>6}  {url[:100]}")
'
