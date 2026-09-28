#!/usr/bin/env bash
# Live provider reachability check.
#
# Unit tests use mocked HTTP only, so they cannot tell you whether a
# third-party deployment still exists. This drives the real provider classes
# against the network so the headers, endpoints, and parsing under test are the
# ones the app actually uses.
#
# The default automatic provider failing is fatal: that is a broken release.
# An opt-in provider failing is reported but does not fail the check.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PROBE="${ANI_PY_LIVE_PROBE:-naruto}"

echo "ani-py live provider check (probe: $PROBE)"

python3 - "$PROBE" <<'PY'
import sys

import ani_py

probe = sys.argv[1]
http = ani_py.HttpClient()

# Mirrors the provider list in App.__init__; keep the two in step.
providers = [
    ani_py.HianimeProvider(http),
    ani_py.AniLightProvider(http),
]
default_names = {p.name for p in providers if not p.experimental}

failures = []
for provider in providers:
    label = provider.display_name
    required = provider.name in default_names
    try:
        if not provider.available():
            raise ani_py.ProviderUnavailable("preflight reported unavailable")
        results = provider.search(probe)
    except ani_py.AniPyError as exc:
        print(f"[{'FAIL' if required else 'warn'}] {label}: {exc}")
        failures.append((label, required))
        continue
    except Exception as exc:  # network/HTTP layer, unexpected payloads
        print(f"[{'FAIL' if required else 'warn'}] {label}: {type(exc).__name__}: {exc}")
        failures.append((label, required))
        continue

    if not results:
        print(f"[{'FAIL' if required else 'warn'}] {label}: preflight passed but search returned nothing")
        failures.append((label, required))
        continue

    print(f"[ok] {label} returned {len(results)} result(s) for {probe!r}")
    for anime in results[:3]:
        print(f"        {anime.title[:70]}")

if failures:
    blocking = [name for name, required in failures if required]
    if blocking:
        raise SystemExit(f"[fail] required provider(s) unreachable: {', '.join(blocking)}")
    print("[warn] optional provider(s) unreachable; the default chain is unaffected")
PY

echo "All default providers are reachable."
