#!/usr/bin/env python3
"""
ani-py - standalone anime CLI

Single-file, standard-library-only Python application.
External tools are used where they make sense (curl/curl-impersonate,
fzf/rofi/dmenu, mpv/vlc/iina, yt-dlp/ffmpeg, ani-skip).

This is an independent Python implementation inspired by the workflow of
terminal anime launchers. Provider markup can change; scraping code is kept
isolated behind provider adapters for easier repair and failover.
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import hashlib
import html
import http.server
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Optional, Sequence
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import quote, quote_plus, urlencode, urljoin, urlsplit

APP_NAME = "ani-py"
# Calendar version (CalVer): the date the most recent user-visible change landed.
# Monotonic by construction and comparable across automated release snapshots.
# Bump it in the same commit as the change - see "Versioning" in CONTRIBUTING.md.
VERSION = "2026.10.4"
BASE_URL = "https://hianime.at"
ANILIGHT_BASE_URL = "https://anilight.live"
ANILIGHT_API_URL = "https://api.anilight.live/api"
# Runtime updates are published as versioned GitHub Release assets after the
# main-branch test workflow passes. The moving "latest" pointer selects a release,
# while SHA256SUMS verifies that the downloaded standalone script belongs to it.
RELEASE_BASE_URL = "https://github.com/Onehand-Coding/ani-py/releases/latest/download"
UPDATE_URL = f"{RELEASE_BASE_URL}/ani-py"
UPDATE_CHECKSUM_URL = f"{RELEASE_BASE_URL}/SHA256SUMS"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
XOR_KEY = b"otaku-embed-v1"


# ---------- terminal / style ----------

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"


def color_enabled() -> bool:
    return sys.stderr.isatty() and os.getenv("NO_COLOR") is None


def sty(text: str, *codes: str) -> str:
    if not color_enabled():
        return text
    return "".join(codes) + text + C.RESET


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(value: str) -> str:
    """Remove ANSI color codes (fzf --ansi strips them from its output)."""
    return _ANSI_RE.sub("", value)


def term_width(default: int = 88) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except OSError:
        return default


def clear_screen() -> None:
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")


def banner(subtitle: Optional[str] = None) -> None:
    width = min(term_width(), 96)
    title = f" {APP_NAME} "
    left = max(1, (width - len(title)) // 2)
    right = max(1, width - left - len(title))
    print(sty("━" * left, C.DIM, C.CYAN) + sty(title, C.BOLD, C.CYAN) + sty("━" * right, C.DIM, C.CYAN))
    if subtitle:
        print(sty(subtitle, C.DIM))


def status(message: str) -> None:
    print(f"{sty('●', C.CYAN)} {message}", file=sys.stderr)


def ok(message: str) -> None:
    print(f"{sty('✓', C.GREEN)} {message}", file=sys.stderr)


def warn(message: str) -> None:
    print(f"{sty('!', C.YELLOW)} {message}", file=sys.stderr)


def fail(message: str, code: int = 1) -> "None":
    print(f"{sty('error', C.BOLD, C.RED)}  {message}", file=sys.stderr)
    raise SystemExit(code)


# ---------- models / provider contracts ----------

@dataclasses.dataclass(frozen=True)
class Anime:
    provider_id: str
    title: str
    provider: str = "hianime"

    @property
    def slug(self) -> str:
        """Backward-compatible alias for older callers/tests."""
        return self.provider_id


@dataclasses.dataclass(frozen=True)
class Episode:
    episode_id: str
    number: str


@dataclasses.dataclass(frozen=True)
class Stream:
    quality: str
    url: str


@dataclasses.dataclass(frozen=True)
class SubtitleTrack:
    url: str
    language: Optional[str] = None
    label: Optional[str] = None
    default: bool = False

    @property
    def name(self) -> str:
        if self.label:
            return self.label
        if self.language:
            return self.language
        return "Subtitle"


@dataclasses.dataclass
class StreamBundle:
    streams: list[Stream]
    subtitle: Optional[str]
    referer: str
    mal_id: Optional[str]
    provider: str = "unknown"
    subtitle_language: Optional[str] = None
    subtitle_label: Optional[str] = None
    subtitles: list[SubtitleTrack] = dataclasses.field(default_factory=list)

    def subtitle_tracks(self) -> list[SubtitleTrack]:
        tracks = list(self.subtitles)
        if self.subtitle and not any(track.url == self.subtitle for track in tracks):
            tracks.insert(
                0,
                SubtitleTrack(
                    url=self.subtitle,
                    language=self.subtitle_language,
                    label=self.subtitle_label,
                    default=True,
                ),
            )
        return tracks


@dataclasses.dataclass
class HistoryEntry:
    episode: str
    provider: str
    provider_id: str
    title: str

    @property
    def anime_slug(self) -> str:
        return self.provider_id


@dataclasses.dataclass
class DetachedSession:
    socket: str
    player: str
    provider: str
    provider_id: str
    title: str
    episode: str
    quality: str
    mode: str
    source_provider: str
    subtitle_preference: str = "auto"


@dataclasses.dataclass(frozen=True)
class ProviderCapabilities:
    sub: bool = True
    dub: bool = False
    subtitles: bool = False
    qualities: bool = True
    mal_id: bool = False


class AniPyError(RuntimeError):
    pass


class HttpError(AniPyError):
    pass


class ProviderError(AniPyError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class AnimeNotFound(ProviderError):
    pass


class EpisodeNotFound(ProviderError):
    pass


class StreamNotFound(ProviderError):
    pass


class ProviderChanged(ProviderError):
    pass


class Provider:
    name = "unknown"
    display_name = "Unknown"
    capabilities = ProviderCapabilities()
    experimental = False

    def available(self) -> bool:
        """Preflight gate: False means automatic paths must skip this provider."""
        return True

    def search(self, query: str) -> list[Anime]:
        raise NotImplementedError

    def episodes(self, anime: Anime | str) -> list[Episode]:
        raise NotImplementedError

    def resolve(self, anime: Anime | str, episode: Episode, mode: str) -> StreamBundle:
        raise NotImplementedError


# ---------- process helpers ----------

def which_first(candidates: Iterable[str]) -> Optional[str]:
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        p = Path(candidate).expanduser()
        if p.exists():
            return str(p)
    return None


def split_flags(value: str) -> list[str]:
    return shlex.split(value) if value.strip() else []


def run_capture(cmd: Sequence[str], *, input_text: Optional[str] = None, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


# ---------- networking ----------

class HttpClient:
    """curl-backed HTTP client with no third-party Python dependencies."""

    def __init__(self) -> None:
        override = os.getenv("ANI_PY_CURL")
        names = [override] if override else [
            "curl_firefox135",
            "curl_chrome136",
            "curl_chrome116",
            "curl_ff117",
            "curl",
        ]
        self.exe = which_first([x for x in names if x])
        if not self.exe:
            fail("curl was not found. Install curl or a curl-impersonate binary.")

    def request(
        self,
        method: str,
        url: str,
        *,
        referer: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: int = 15,
        json_body: Optional[object] = None,
        cookie_jar: Optional[str] = None,
    ) -> str:
        marker = "__ANI_PY_HTTP__"
        cmd = [
            self.exe,
            "-sS",
            "-L",
            "--max-time",
            str(timeout),
            "-A",
            USER_AGENT,
            "-w",
            f"\\n{marker}%{{http_code}}",
        ]
        if method.upper() != "GET":
            cmd += ["-X", method.upper()]
        if referer:
            cmd += ["-e", referer]
        for key, value in (headers or {}).items():
            cmd += ["-H", f"{key}: {value}"]
        if cookie_jar:
            cmd += ["-b", cookie_jar, "-c", cookie_jar]
        if json_body is not None:
            cmd += ["-H", "Content-Type: application/json", "--data-binary", json.dumps(json_body)]
        cmd.append(url)

        proc = run_capture(cmd)
        output = proc.stdout
        if marker not in output:
            detail = proc.stderr.strip() or f"curl exit {proc.returncode}"
            raise HttpError(f"Network request failed for {url}: {detail}")

        body, _, status_text = output.rpartition("\n" + marker)
        try:
            http_status = int(status_text.strip())
        except ValueError as exc:
            raise HttpError(f"Invalid HTTP response while requesting {url}") from exc

        if proc.returncode != 0:
            detail = proc.stderr.strip() or f"curl exit {proc.returncode}"
            raise HttpError(f"Network request failed for {url}: {detail}")

        if not (200 <= http_status < 300):
            challenge = " browser challenge" if "Just a moment" in body else ""
            raise HttpError(f"HTTP {http_status}{challenge} from {url}")
        return body

    def get(
        self,
        url: str,
        *,
        referer: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: int = 15,
        cookie_jar: Optional[str] = None,
    ) -> str:
        return self.request(
            "GET", url, referer=referer, headers=headers, timeout=timeout, cookie_jar=cookie_jar
        )

    def get_bytes(self, url: str, *, timeout: int = 30) -> bytes:
        """Fetch a binary payload without locale decoding or newline translation."""
        cmd = [
            self.exe,
            "-fsSL",
            "--max-time",
            str(timeout),
            "-A",
            USER_AGENT,
            url,
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", "replace").strip() or f"curl exit {proc.returncode}"
            raise HttpError(f"Network request failed for {url}: {detail}")
        return proc.stdout

    def get_json(self, url: str, **kwargs: object) -> object:
        body = self.get(url, **kwargs)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise HttpError(f"Expected JSON from {url}") from exc

    def post_json(self, url: str, payload: object, **kwargs: object) -> object:
        body = self.request("POST", url, json_body=payload, **kwargs)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise HttpError(f"Expected JSON from {url}") from exc


# ---------- provider helpers ----------

def _attrs(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    pat = re.compile(r"([\w:-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))")
    for match in pat.finditer(raw):
        out[match.group(1).lower()] = next((x for x in match.groups()[1:] if x is not None), "")
    return out


def _plain_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return html.unescape(re.sub(r"\s+", " ", value)).strip()


def _provider_id(anime: Anime | str) -> str:
    return anime.provider_id if isinstance(anime, Anime) else anime


def _normalize_title(value: str) -> str:
    value = html.unescape(value).casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


# ---------- AES-256-CBC decryption (stdlib only) ----------
#
# HiAnime's current embed backend ships the HLS URL inside a single
# AES-256-CBC blob. The key and IV are constants in the site's own player
# script, so the cipher is fully specified and only decryption is needed.
# Written out here because the standard library has no AES.


def _gmul(a: int, b: int) -> int:
    """Multiply two bytes in GF(2^8) modulo the AES polynomial."""
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        b >>= 1
        a = ((a << 1) ^ (0x1B if a & 0x80 else 0)) & 0xFF
    return result


def _build_sbox() -> list[int]:
    """Generate the AES S-box: multiplicative inverse plus affine transform."""
    sbox = [0] * 256
    p = q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        if q & 0x80:
            q ^= 0x09
        value = q
        for shift in (1, 2, 3, 4):
            value ^= ((q << shift) | (q >> (8 - shift))) & 0xFF
        sbox[p] = value ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


_AES_SBOX = _build_sbox()
_AES_INV_SBOX = [0] * 256
for _index, _value in enumerate(_AES_SBOX):
    _AES_INV_SBOX[_value] = _index


def _aes256_key_schedule(key: bytes) -> tuple[list[list[int]], int]:
    words = [list(key[4 * i:4 * i + 4]) for i in range(8)]
    rcon = 1
    for i in range(8, 4 * (14 + 1)):
        temp = list(words[i - 1])
        if i % 8 == 0:
            temp = temp[1:] + temp[:1]
            temp = [_AES_SBOX[b] for b in temp]
            temp[0] ^= rcon
            rcon = _gmul(rcon, 2)
        elif i % 8 == 4:
            temp = [_AES_SBOX[b] for b in temp]
        words.append([words[i - 8][j] ^ temp[j] for j in range(4)])
    return words, 14


def _add_round_key(state: list[int], words: list[list[int]], rnd: int) -> None:
    for i in range(16):
        state[i] ^= words[rnd * 4 + i // 4][i % 4]


def _inv_shift_rows(state: list[int]) -> list[int]:
    # Row r of the AES state is strided by 4 in this flat (r + 4c) layout, so
    # the inverse of a visual right-shift by r reads as an index shift of -r.
    out = [0] * 16
    for r in range(4):
        for c in range(4):
            out[r + 4 * c] = state[r + 4 * ((c - r) % 4)]
    return out


def _inv_mix_columns(state: list[int]) -> list[int]:
    out = [0] * 16
    for c in range(4):
        a0, a1, a2, a3 = (state[r + 4 * c] for r in range(4))
        out[0 + 4 * c] = _gmul(a0, 14) ^ _gmul(a1, 11) ^ _gmul(a2, 13) ^ _gmul(a3, 9)
        out[1 + 4 * c] = _gmul(a0, 9) ^ _gmul(a1, 14) ^ _gmul(a2, 11) ^ _gmul(a3, 13)
        out[2 + 4 * c] = _gmul(a0, 13) ^ _gmul(a1, 9) ^ _gmul(a2, 14) ^ _gmul(a3, 11)
        out[3 + 4 * c] = _gmul(a0, 11) ^ _gmul(a1, 13) ^ _gmul(a2, 9) ^ _gmul(a3, 14)
    return out


def _aes256_decrypt_block(block: bytes, words: list[list[int]], rounds: int) -> bytes:
    # Inverse cipher: the last round key comes off first, then each round
    # undoes MixColumns/ShiftRows/SubBytes in the reverse of the order the
    # forward cipher applied them.
    state = list(block)
    _add_round_key(state, words, rounds)
    state = _inv_shift_rows(state)
    state = [_AES_INV_SBOX[b] for b in state]
    for rnd in range(rounds - 1, 0, -1):
        _add_round_key(state, words, rnd)
        state = _inv_mix_columns(state)
        state = _inv_shift_rows(state)
        state = [_AES_INV_SBOX[b] for b in state]
    _add_round_key(state, words, 0)
    return bytes(state)


# Seed and IV the megaplay.buzz player uses for its source manifest.
MEGAPLAY_KEY = b"i?LMTAx0Q6,:}50U"
MEGAPLAY_IV = b"W0;27ToaUpl_P%'c"


def _aes256_cbc_decrypt(blob: str, key: bytes, iv: bytes) -> bytes:
    """Decrypt a base64url AES-256-CBC blob with the given 32-byte key and 16-byte IV."""
    if len(key) > 32:
        raise ValueError("AES-256 key must be at most 32 bytes")
    padded = key + b"\x00" * (32 - len(key))
    words, rounds = _aes256_key_schedule(padded)
    previous = (iv + b"\x00" * 16)[:16]
    raw = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
    out = bytearray()
    for start in range(0, len(raw) - 15, 16):
        block = raw[start:start + 16]
        plain = _aes256_decrypt_block(block, words, rounds)
        out += bytes(a ^ b for a, b in zip(plain, previous))
        previous = block
    if out:
        pad = out[-1]
        if 1 <= pad <= 16:
            out = out[:-pad]
    return bytes(out)


# ---------- HiAnime provider ----------

class HianimeProvider(Provider):
    name = "hianime"
    display_name = "HiAnime"
    capabilities = ProviderCapabilities(sub=True, dub=True, subtitles=True, qualities=True, mal_id=True)

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    @staticmethod
    def _clean_title(value: str) -> str:
        return html.unescape(re.sub(r"\s+", " ", value)).strip()

    def search(self, query: str) -> list[Anime]:
        try:
            page = self.http.get(f"{BASE_URL}/search?keyword={quote_plus(query)}")
        except HttpError as exc:
            raise ProviderUnavailable(f"HiAnime search failed: {exc}") from exc
        if "<title>Just a moment" in page:
            raise ProviderUnavailable("HiAnime is behind a browser challenge; curl-impersonate may help.")

        page = page.split('id="main-sidebar"', 1)[0]
        found: list[Anime] = []
        seen: set[str] = set()
        pat = re.compile(
            r'<h3\s+class="film-name"[^>]*>.*?'
            r'<a\s+href="[^"]*/([^"/?#]+)"[^>]*\btitle="([^"]+)"',
            re.IGNORECASE | re.DOTALL,
        )
        for slug, title in pat.findall(page):
            title = self._clean_title(title)
            if slug not in seen:
                found.append(Anime(provider_id=slug, title=title, provider=self.name))
                seen.add(slug)
        return found

    def episodes(self, anime: Anime | str) -> list[Episode]:
        anime_slug = _provider_id(anime)
        numeric_id = anime_slug.rsplit("-", 1)[-1]
        try:
            page = self.http.get(f"{BASE_URL}/api/theme/episode/list/{numeric_id}")
        except HttpError as exc:
            raise ProviderUnavailable(f"HiAnime episode list failed: {exc}") from exc
        page = page.replace("\\", "")
        pat = re.compile(
            r'data-number="([^"]+)"[^>]*data-id="(\d+)".*?'
            r'/watch/' + re.escape(anime_slug) + r'\?ep=',
            re.IGNORECASE | re.DOTALL,
        )
        episodes = [Episode(episode_id=eid, number=num) for num, eid in pat.findall(page)]
        if not episodes:
            raise EpisodeNotFound(f"HiAnime returned no episodes for {anime_slug}.")
        return episodes

    @staticmethod
    def _decode_embed_hash(encoded: str) -> Optional[str]:
        if not encoded:
            return None
        try:
            return base64.b64decode(encoded).decode("utf-8", "replace")
        except Exception:
            return None

    @staticmethod
    def _deobfuscate(blob: str) -> dict:
        raw = base64.b64decode(blob)
        decoded = bytes(value ^ XOR_KEY[i % len(XOR_KEY)] for i, value in enumerate(raw))
        try:
            return json.loads(decoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("provider payload could not be decoded") from exc

    @staticmethod
    def _pick_source_url(payload: object) -> Optional[str]:
        if isinstance(payload, dict):
            src = payload.get("src")
            if isinstance(src, str) and ".m3u8" in src:
                return src
            for value in payload.values():
                hit = HianimeProvider._pick_source_url(value)
                if hit:
                    return hit
        elif isinstance(payload, list):
            for value in payload:
                hit = HianimeProvider._pick_source_url(value)
                if hit:
                    return hit
        return None

    @staticmethod
    def _subtitle_language(value: object) -> Optional[str]:
        if not isinstance(value, str):
            return None
        text = value.strip().lower().replace("_", "-")
        if not text:
            return None
        aliases = {
            "english": "en",
            "eng": "en",
            "en-us": "en",
            "en-gb": "en",
            "japanese": "ja",
            "jpn": "ja",
            "spanish": "es",
            "spa": "es",
            "french": "fr",
            "fre": "fr",
            "fra": "fr",
            "german": "de",
            "ger": "de",
            "deu": "de",
            "portuguese": "pt",
            "por": "pt",
        }
        if text in aliases:
            return aliases[text]
        if re.fullmatch(r"[a-z]{2,3}(?:-[a-z]{2,4})?", text):
            return text.split("-", 1)[0]
        return None

    @staticmethod
    def _subtitle_tracks(payload: object) -> list[SubtitleTrack]:
        tracks: list[SubtitleTrack] = []
        seen: set[str] = set()

        def visit(value: object) -> None:
            if isinstance(value, dict):
                subtitles = value.get("subtitles")
                if isinstance(subtitles, list):
                    for item in subtitles:
                        if not isinstance(item, dict):
                            continue
                        src = item.get("src") or item.get("file") or item.get("url")
                        if not isinstance(src, str) or not src or src in seen:
                            continue
                        label_obj = item.get("label") or item.get("name")
                        label = label_obj.strip() if isinstance(label_obj, str) and label_obj.strip() else None
                        language = None
                        for key in ("language", "lang", "srclang"):
                            language = HianimeProvider._subtitle_language(item.get(key))
                            if language:
                                break
                        if not language:
                            language = HianimeProvider._subtitle_language(label)
                        tracks.append(
                            SubtitleTrack(
                                url=src,
                                language=language,
                                label=label,
                                default=bool(item.get("default")),
                            )
                        )
                        seen.add(src)
                for nested in value.values():
                    visit(nested)
            elif isinstance(value, list):
                for nested in value:
                    visit(nested)

        visit(payload)
        return tracks

    @staticmethod
    def _default_subtitle(tracks: Sequence[SubtitleTrack]) -> Optional[SubtitleTrack]:
        if not tracks:
            return None
        return next((track for track in tracks if track.default), None) or next(
            (track for track in tracks if track.language == "en"), None
        ) or tracks[0]

    @staticmethod
    def _pick_subtitle_info(payload: object) -> tuple[Optional[str], Optional[str], Optional[str]]:
        chosen = HianimeProvider._default_subtitle(HianimeProvider._subtitle_tracks(payload))
        if chosen is None:
            return None, None, None
        return chosen.url, chosen.language, chosen.label

    @staticmethod
    def _pick_subtitle(payload: object) -> Optional[str]:
        return HianimeProvider._pick_subtitle_info(payload)[0]

    def _resolve_megaplay(
        self, embed_page: str, referer: str, mode: str, mal_id: Optional[str]
    ) -> StreamBundle:
        """Resolve the megaplay.buzz backend, which serves an AES-encrypted
        source manifest from /stream/getSources instead of a window.__P blob."""
        # data-id is the per-episode, per-mode identifier the player sends to
        # getSources. data-realid is NOT equivalent: it is shared between the
        # sub and dub embeds, and asking for it returns a different show.
        id_match = re.search(r'data-id="(\d+)"', embed_page)
        if not id_match:
            raise ProviderChanged("HiAnime player markup changed; no media id was present.")
        media_id = id_match.group(1)
        try:
            payload = self.http.get(
                f"{referer}stream/getSources?id={media_id}&type={mode}",
                referer=referer,
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        except HttpError as exc:
            raise ProviderUnavailable(f"HiAnime source manifest failed: {exc}") from exc
        try:
            sources = json.loads(payload)
            enc = sources["enc"]
            if not isinstance(enc, str):
                raise TypeError("enc is not a string")
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderChanged(f"HiAnime source manifest is malformed: {exc}") from exc
        try:
            manifest = json.loads(_aes256_cbc_decrypt(enc, MEGAPLAY_KEY, MEGAPLAY_IV))
        except (ValueError, TypeError) as exc:
            raise ProviderChanged(f"HiAnime source manifest could not be decrypted: {exc}") from exc
        master_url = manifest.get("file") if isinstance(manifest, dict) else None
        if not isinstance(master_url, str) or ".m3u8" not in master_url:
            raise StreamNotFound("HiAnime source manifest contained no HLS playlist.")
        tracks = self._subtitle_tracks({"subtitles": sources.get("tracks")})
        default_track = self._default_subtitle(tracks)
        try:
            master = self.http.get(master_url, referer=referer)
        except HttpError as exc:
            raise ProviderUnavailable(f"HiAnime HLS host failed: {exc}") from exc
        streams = self._parse_master(master, master_url)
        if not streams:
            streams = [Stream(quality="auto", url=master_url)]
        return StreamBundle(
            streams=streams,
            subtitle=default_track.url if default_track else None,
            referer=referer,
            mal_id=mal_id,
            provider=self.name,
            subtitle_language=default_track.language if default_track else None,
            subtitle_label=default_track.label if default_track else None,
            subtitles=tracks,
        )

    @staticmethod
    def _parse_master(master: str, master_url: str) -> list[Stream]:
        lines = [line.strip() for line in master.splitlines() if line.strip()]
        streams: list[Stream] = []
        for i, line in enumerate(lines):
            if not line.startswith("#EXT-X-STREAM-INF") or i + 1 >= len(lines):
                continue
            url = lines[i + 1]
            if url.startswith("#"):
                continue
            resolution = re.search(r"RESOLUTION=\d+x(\d+)", line, re.IGNORECASE)
            bandwidth = re.search(r"BANDWIDTH=(\d+)", line, re.IGNORECASE)
            quality = resolution.group(1) + "p" if resolution else (
                f"{int(bandwidth.group(1)) // 1000}k" if bandwidth else "auto"
            )
            streams.append(Stream(quality=quality, url=urljoin(master_url, url)))
        streams.sort(key=stream_rank, reverse=True)
        return streams

    def resolve(self, anime: Anime | str, episode: Episode, mode: str) -> StreamBundle:
        anime_slug = _provider_id(anime)
        try:
            server_page = self.http.get(
                f"{BASE_URL}/api/theme/episode/servers?episodeId={quote_plus(episode.episode_id)}"
            ).replace('\\"', '"')
        except HttpError as exc:
            raise ProviderUnavailable(f"HiAnime server lookup failed: {exc}") from exc

        embeds: list[tuple[str, str]] = []
        for server_name, encoded in self._server_hashes(server_page, mode):
            decoded = self._decode_embed_hash(encoded)
            if decoded:
                embeds.append((server_name, decoded))
        if not embeds:
            raise StreamNotFound(f"HiAnime has no {mode} source for episode {episode.number}.")

        # The embed hosts gate on Referer and answer HTTP 200 with their own
        # error page when it is missing, so the watch page is sent as one.
        watch_referer = f"{BASE_URL}/watch/{anime_slug}?ep={episode.number}"

        # ZokoAnime is tried first; if its embed or HLS host is broken, fall
        # through to the other servers HiAnime lists for the same episode.
        failures: list[tuple[str, ProviderError]] = []
        for index, (server_name, embed_url) in enumerate(embeds):
            try:
                return self._resolve_embed(embed_url, mode, watch_referer)
            except ProviderError as exc:
                failures.append((server_name, exc))
                if index + 1 < len(embeds):
                    warn(f"HiAnime {server_name} server failed: {exc}; trying {embeds[index + 1][0]}.")
        first = failures[0][1]
        if len(failures) == 1:
            raise first
        others = ", ".join(name for name, _ in failures[1:])
        raise type(first)(f"{first} (other servers also failed: {others})") from first

    @staticmethod
    def _server_hashes(server_page: str, mode: str) -> list[tuple[str, str]]:
        """Return (server name, embed hash) pairs for ``mode``, ZokoAnime first."""
        found: list[tuple[str, str]] = []
        for tag in re.findall(r'<[^>]*class="[^"]*server-item[^"]*"[^>]*>', server_page, re.I):
            attrs = _attrs(tag)
            name = attrs.get("data-server-name", "")
            encoded = attrs.get("data-hash")
            if attrs.get("data-type") == mode and name and encoded and (name, encoded) not in found:
                found.append((name, encoded))
        if not any(name.lower() == "zokoanime" for name, _ in found):
            m = re.search(
                rf'data-type="{re.escape(mode)}".*?data-server-name="ZokoAnime".*?data-hash="([^"]+)"',
                server_page,
                re.I | re.S,
            )
            if m:
                found.append(("ZokoAnime", m.group(1)))
        found.sort(key=lambda item: item[0].lower() != "zokoanime")
        return found

    def _resolve_embed(self, embed_url: str, mode: str, page_referer: Optional[str] = None) -> StreamBundle:
        parts = urlsplit(embed_url)
        referer = f"{parts.scheme}://{parts.netloc}/"
        mal_match = re.search(r"/mal/(\d+)/", embed_url)
        mal_id = mal_match.group(1) if mal_match else None
        try:
            embed_page = self.http.get(embed_url, referer=page_referer or referer)
        except HttpError as exc:
            raise ProviderUnavailable(f"HiAnime embed host failed: {exc}") from exc
        if 'class="error-container"' in embed_page:
            raise ProviderUnavailable(
                f"HiAnime embed host {parts.netloc} served its error page instead of a player"
            )
        blob_match = re.search(r'window\.__P\s*=\s*"([^"]+)"', embed_page)
        if not blob_match:
            return self._resolve_megaplay(embed_page, referer, mode, mal_id)
        try:
            payload = self._deobfuscate(blob_match.group(1))
        except ValueError as exc:
            raise ProviderChanged(str(exc)) from exc

        master_url = self._pick_source_url(payload)
        if not master_url:
            raise StreamNotFound("HiAnime payload contained no HLS source.")
        subtitle_tracks = self._subtitle_tracks(payload)
        default_subtitle = self._default_subtitle(subtitle_tracks)
        subtitle = default_subtitle.url if default_subtitle else None
        subtitle_language = default_subtitle.language if default_subtitle else None
        subtitle_label = default_subtitle.label if default_subtitle else None
        try:
            master = self.http.get(master_url, referer=referer)
        except HttpError as exc:
            raise ProviderUnavailable(f"HiAnime HLS host failed: {exc}") from exc
        streams = self._parse_master(master, master_url)
        if not streams:
            streams = [Stream(quality="auto", url=master_url)]
        return StreamBundle(
            streams=streams,
            subtitle=subtitle,
            referer=referer,
            mal_id=mal_id,
            provider=self.name,
            subtitle_language=subtitle_language,
            subtitle_label=subtitle_label,
            subtitles=subtitle_tracks,
        )



# ---------- AniLight experimental provider ----------

class AniLightProvider(Provider):
    """AniLight catalog + currently playable direct-source adapter.

    AniLight exposes catalog/episode JSON through api.anilight.live. Its source
    endpoint fans out to several upstream servers. For the initial ani-py
    adapter we deliberately use the "ryu" / AnimeGG route because it returns
    progressive MP4 files and AniLight's own proxy makes those URLs portable
    across desktop and Android players without provider-specific HLS surgery.

    Other AniLight backends expose soft subtitles and broader coverage, but
    currently require changing CDN/proxy rules. Keep those out until their
    playback behavior is proven against ani-py's desktop and Android paths.
    """

    name = "anilight"
    display_name = "AniLight"
    capabilities = ProviderCapabilities(
        sub=True, dub=True, subtitles=False, qualities=True, mal_id=True
    )
    experimental = True

    SOURCE_PROVIDER = "ryu"

    def __init__(self, http: HttpClient) -> None:
        self.http = http
        self.base = os.getenv("ANI_PY_ANILIGHT_URL", ANILIGHT_BASE_URL).rstrip("/")
        self.api = os.getenv("ANI_PY_ANILIGHT_API_URL", ANILIGHT_API_URL).rstrip("/")
        self._available: Optional[bool] = None
        self._watch_cache: dict[str, dict[str, object]] = {}
        self._episode_cache: dict[str, dict[str, dict[str, object]]] = {}
        self._info_cache: dict[str, dict[str, object]] = {}

    @property
    def api_headers(self) -> dict[str, str]:
        return {
            "Origin": self.base,
            "Accept": "application/json,text/plain,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _api_json(self, path: str, *, timeout: int = 15) -> object:
        try:
            return self.http.get_json(
                self.api + path,
                headers=self.api_headers,
                referer=self.base + "/",
                timeout=timeout,
            )
        except HttpError as exc:
            raise ProviderUnavailable(f"AniLight API request failed: {exc}") from exc

    @staticmethod
    def _title(item: dict[str, object]) -> str:
        title = item.get("title")
        if isinstance(title, dict):
            for key in ("english", "romaji", "native"):
                value = title.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        if isinstance(title, str) and title.strip():
            return title.strip()
        for key in ("name", "englishName"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _parse_provider_id(provider_id: str) -> tuple[Optional[str], str]:
        raw = str(provider_id or "")
        if ":" in raw:
            anilist_id, slug = raw.split(":", 1)
            if anilist_id.isdigit() and slug:
                return anilist_id, slug
        return None, raw

    @staticmethod
    def _provider_id_for(item: dict[str, object]) -> Optional[str]:
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            return None
        ident = item.get("anilistId") or item.get("anilist_id")
        return f"{ident}:{slug}" if ident is not None else slug

    @staticmethod
    def _results(data: object) -> list[dict[str, object]]:
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for key in ("results", "data", "items"):
                value = data.get(key)
                if isinstance(value, list):
                    return [x for x in value if isinstance(x, dict)]
        return []

    def available(self) -> bool:
        """Memoized API preflight for user-configured automatic failover."""
        if self._available is not None:
            return self._available
        try:
            data = self._api_json("/search?" + urlencode({"q": "naruto"}), timeout=12)
            self._available = bool(self._results(data))
            if not self._available:
                warn("AniLight preflight returned no search results; treating it as unavailable.")
        except ProviderError as exc:
            warn(f"AniLight preflight failed ({exc}); treating it as unavailable.")
            self._available = False
        return self._available

    def search(self, query: str) -> list[Anime]:
        data = self._api_json("/search?" + urlencode({"q": query}))
        rows = self._results(data)
        found: list[Anime] = []
        seen: set[str] = set()
        for item in rows:
            provider_id = self._provider_id_for(item)
            title = self._title(item)
            if not provider_id or not title or provider_id in seen:
                continue
            self._info_cache[provider_id] = item
            found.append(Anime(provider_id=provider_id, title=title, provider=self.name))
            seen.add(provider_id)
        return found

    @staticmethod
    def _number(value: object) -> Optional[str]:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return str(int(value)) if value.is_integer() else str(value)
        if isinstance(value, str):
            value = value.strip()
            if re.fullmatch(r"\d+(?:\.\d+)?", value):
                return value[:-2] if value.endswith(".0") else value
        return None

    def _watch_doc(self, anime: Anime | str) -> dict[str, object]:
        provider_id = _provider_id(anime)
        cached = self._watch_cache.get(provider_id)
        if cached is not None:
            return cached
        _, slug = self._parse_provider_id(provider_id)
        if not slug:
            raise AnimeNotFound("AniLight anime id did not contain a slug.")
        data = self._api_json("/watch/" + quote(slug, safe="-._~"), timeout=20)
        if not isinstance(data, dict):
            raise ProviderChanged("AniLight watch response was not an object.")
        self._watch_cache[provider_id] = data
        return data

    def _episode_rows(self, anime: Anime | str) -> dict[str, dict[str, object]]:
        provider_id = _provider_id(anime)
        cached = self._episode_cache.get(provider_id)
        if cached is not None:
            return cached

        data = self._watch_doc(anime)
        raw = data.get("episodes")
        if not isinstance(raw, list):
            nested = data.get("data")
            raw = nested.get("episodes") if isinstance(nested, dict) else None
        if not isinstance(raw, list):
            raise ProviderChanged("AniLight watch response did not contain an episode list.")

        rows: dict[str, dict[str, object]] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            number = self._number(item.get("number"))
            if number:
                rows[number] = item
        self._episode_cache[provider_id] = rows
        return rows

    def episodes(self, anime: Anime | str) -> list[Episode]:
        rows = self._episode_rows(anime)
        episodes = [Episode(episode_id=number, number=number) for number in rows]
        episodes.sort(
            key=lambda e: float(e.number)
            if re.fullmatch(r"\d+(?:\.\d+)?", e.number)
            else 10**9
        )
        if not episodes:
            raise EpisodeNotFound(f"AniLight returned no episodes for {_provider_id(anime)}.")
        return episodes

    def _numeric_id(self, anime: Anime | str) -> Optional[str]:
        provider_id = _provider_id(anime)
        watch = self._watch_doc(anime)
        ident = watch.get("id")
        if ident is not None:
            return str(ident)

        cached = self._info_cache.get(provider_id)
        if not isinstance(cached, dict):
            _, slug = self._parse_provider_id(provider_id)
            if not slug:
                return None
            data = self._api_json("/anime/" + quote(slug, safe="-._~"), timeout=12)
            cached = data if isinstance(data, dict) else None
            if isinstance(cached, dict):
                self._info_cache[provider_id] = cached
        if not isinstance(cached, dict):
            return None
        ident = cached.get("id")
        return str(ident) if ident is not None else None

    def _mal_id(self, anime: Anime | str) -> Optional[str]:
        provider_id = _provider_id(anime)
        cached = self._info_cache.get(provider_id)
        if not isinstance(cached, dict):
            _, slug = self._parse_provider_id(provider_id)
            if not slug:
                return None
            try:
                data = self._api_json("/anime/" + quote(slug, safe="-._~"), timeout=12)
            except ProviderError:
                return None
            cached = data if isinstance(data, dict) else None
            if isinstance(cached, dict):
                self._info_cache[provider_id] = cached
        if not isinstance(cached, dict):
            return None
        value = cached.get("idMal") or cached.get("malId") or cached.get("mal_id")
        return str(value) if value is not None else None

    def _sources(self, anime_id: str, episode: str, mode: str) -> dict[str, object]:
        path = "/sources?" + urlencode({
            "id": anime_id,
            "epNum": episode,
            "type": mode,
            "providerId": self.SOURCE_PROVIDER,
        })
        data = self._api_json(path, timeout=20)
        if not isinstance(data, dict):
            raise ProviderChanged("AniLight source response was not an object.")
        return data

    @staticmethod
    def _quality(value: object) -> str:
        text = str(value or "auto").strip()
        if re.fullmatch(r"\d{3,4}", text):
            return text + "p"
        return text or "auto"

    def _proxy_url(self, upstream: str) -> str:
        # AniLight's stable API endpoint chooses the current worker/CDN for the
        # AnimeGG route. Do not hardcode the rotating worker hostname itself.
        return self.api + "/proxy/ryu?" + urlencode({"url": upstream})

    def resolve(self, anime: Anime | str, episode: Episode, mode: str) -> StreamBundle:
        if mode not in {"sub", "dub"}:
            raise StreamNotFound(f"AniLight does not support mode {mode!r}.")

        row = self._episode_rows(anime).get(episode.number)
        if not row:
            raise EpisodeNotFound(f"AniLight episode {episode.number} was not found.")

        embeds = row.get("embed_url")
        if isinstance(embeds, dict):
            marker = embeds.get(mode)
            if not isinstance(marker, str) or not marker:
                raise StreamNotFound(f"AniLight has no {mode} version for episode {episode.number}.")

        anime_id = self._numeric_id(anime)
        if not anime_id:
            raise ProviderChanged("AniLight watch response did not expose its numeric anime id.")

        payload = self._sources(anime_id, episode.number, mode)
        raw_sources = payload.get("sources")
        if not isinstance(raw_sources, list):
            raise ProviderChanged("AniLight source response did not contain a sources list.")

        streams: list[Stream] = []
        seen: set[str] = set()
        for item in raw_sources:
            if not isinstance(item, dict):
                continue
            upstream = item.get("url") or item.get("file")
            if not isinstance(upstream, str) or not upstream.startswith(("http://", "https://")):
                continue
            proxied = self._proxy_url(upstream)
            if proxied in seen:
                continue
            streams.append(Stream(self._quality(item.get("quality")), proxied))
            seen.add(proxied)

        if not streams:
            raise StreamNotFound(
                f"AniLight's portable source has no {mode} stream for episode {episode.number}."
            )

        streams.sort(key=stream_rank, reverse=True)
        return StreamBundle(
            streams=streams,
            subtitle=None,
            referer=self.base + "/",
            mal_id=self._mal_id(anime),
            provider=self.name,
        )


# ---------- provider manager ----------

class ProviderManager:
    def __init__(self, providers: Sequence[Provider], order: Sequence[str]) -> None:
        self.providers = {p.name: p for p in providers}
        cleaned = [name for name in order if name in self.providers]
        self.order = cleaned or list(self.providers)

    def get(self, name: str) -> Provider:
        try:
            return self.providers[name]
        except KeyError as exc:
            raise ProviderUnavailable(f"Unknown provider: {name}") from exc

    def search(self, query: str, preference: str = "auto") -> list[Anime]:
        names = self.order if preference == "auto" else [preference]
        errors: list[str] = []
        for index, name in enumerate(names):
            provider = self.get(name)
            if preference == "auto" and not provider.available():
                warn(f"Skipping {provider.display_name}: preflight unavailable.")
                continue
            try:
                results = provider.search(query)
            except ProviderError as exc:
                errors.append(f"{provider.display_name}: {exc}")
                if preference == "auto" and index + 1 < len(names):
                    warn(f"{provider.display_name} search failed; trying {self.get(names[index + 1]).display_name}.")
                continue
            if results:
                return results
            if preference == "auto" and index + 1 < len(names):
                warn(f"No results from {provider.display_name}; trying {self.get(names[index + 1]).display_name}.")
        if errors:
            raise ProviderUnavailable("; ".join(errors))
        return []

    def episodes(self, anime: Anime) -> list[Episode]:
        return self.get(anime.provider).episodes(anime)

    def resolve(self, anime: Anime, episode: Episode, mode: str) -> StreamBundle:
        return self.get(anime.provider).resolve(anime, episode, mode)

    def fallback_names(self, current: str) -> list[str]:
        return [name for name in self.order if name != current and self.get(name).available()]



# ---------- history ----------

class HistoryStore:
    def __init__(self) -> None:
        root = Path(os.getenv("ANI_PY_HIST_DIR") or os.getenv("XDG_STATE_HOME") or (Path.home() / ".local/state"))
        self.dir = root / APP_NAME
        self.path = self.dir / "history.tsv"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def load(self) -> list[HistoryEntry]:
        out: list[HistoryEntry] = []
        for raw in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = raw.split("\t")
            if len(parts) >= 4:
                episode, provider, provider_id = parts[:3]
                title = "\t".join(parts[3:])
                out.append(HistoryEntry(episode, provider, provider_id, title))
            elif len(parts) == 3:
                # v0.3 and older: episode, HiAnime slug, title
                episode, provider_id, title = parts
                out.append(HistoryEntry(episode, "hianime", provider_id, title))
        return out

    def save(self, entries: Sequence[HistoryEntry]) -> None:
        data = "".join(
            f"{e.episode}\t{e.provider}\t{e.provider_id}\t{e.title}\n" for e in entries
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.dir, delete=False) as tf:
            tf.write(data)
            temp_name = tf.name
        Path(temp_name).replace(self.path)

    def update(self, anime: Anime, episode: str) -> None:
        entries = self.load()
        replaced = False
        for item in entries:
            if item.provider == anime.provider and item.provider_id == anime.provider_id:
                item.episode = episode
                item.title = anime.title
                replaced = True
                break
        if not replaced:
            entries.append(HistoryEntry(episode, anime.provider, anime.provider_id, anime.title))
        self.save(entries)

    def clear(self) -> None:
        self.path.write_text("", encoding="utf-8")


class DetachedSessionStore:
    def __init__(self) -> None:
        root = Path(os.getenv("ANI_PY_HIST_DIR") or os.getenv("XDG_STATE_HOME") or (Path.home() / ".local/state"))
        self.dir = root / APP_NAME
        self.path = self.dir / "detached-session.json"
        self.dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> Optional[DetachedSession]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        required = {
            "socket", "player", "provider", "provider_id", "title",
            "episode", "quality", "mode", "source_provider",
        }
        if not required.issubset(raw):
            return None
        try:
            return DetachedSession(
                socket=str(raw["socket"]),
                player=str(raw["player"]),
                provider=str(raw["provider"]),
                provider_id=str(raw["provider_id"]),
                title=str(raw["title"]),
                episode=str(raw["episode"]),
                quality=str(raw["quality"]),
                mode=str(raw["mode"]),
                source_provider=str(raw["source_provider"]),
                subtitle_preference=str(raw.get("subtitle_preference") or "auto"),
            )
        except (TypeError, ValueError):
            return None

    def save(self, session: DetachedSession) -> None:
        payload = json.dumps(dataclasses.asdict(session), ensure_ascii=False, indent=2) + "\n"
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.dir, delete=False
        ) as handle:
            handle.write(payload)
            temp_name = handle.name
        temp_path = Path(temp_name)
        try:
            temp_path.chmod(0o600)
        except OSError:
            pass
        temp_path.replace(self.path)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


# ---------- menu ----------

class Menu:
    def __init__(self, program: Optional[str], extra_flags: str = "") -> None:
        requested = program or os.getenv("ANI_PY_MENU") or "fzf"
        self.program = requested if shutil.which(requested) else None
        self.extra = split_flags(extra_flags or os.getenv("ANI_PY_MENU_FLAGS", ""))

    def choose(
        self,
        items: Sequence[str],
        prompt: str,
        *,
        multi: bool = False,
        compact: bool = False,
        header: Optional[str] = None,
    ) -> list[str]:
        if not items:
            return []
        if len(items) == 1 and not compact:
            return [items[0]]
        if self.program == "fzf":
            height = "~12" if compact else "80%"
            info = "hidden" if compact else "inline-right"
            cmd = [
                "fzf", "--ansi", "--reverse", "--cycle",
                f"--height={height}", "--border=rounded", f"--info={info}",
                "--pointer=›", "--prompt", prompt,
            ]
            if header:
                cmd += ["--header", header, "--header-first"]
            if multi:
                cmd += ["--multi", "--bind", "tab:toggle+down", "--marker=✓"]
            cmd += self.extra
            proc = run_capture(cmd, input_text="\n".join(items) + "\n")
            return proc.stdout.splitlines() if proc.returncode == 0 else []
        if self.program == "rofi":
            cmd = ["rofi", "-dmenu", "-i", "-p", prompt.rstrip()] + self.extra
            if multi:
                cmd += ["-multi-select"]
            proc = run_capture(cmd, input_text="\n".join(items) + "\n")
            return proc.stdout.splitlines() if proc.returncode == 0 else []
        if self.program == "dmenu":
            lines = str(min(len(items), 8 if compact else 20))
            cmd = ["dmenu", "-l", lines, "-p", prompt.rstrip()] + self.extra
            proc = run_capture(cmd, input_text="\n".join(items) + "\n")
            return proc.stdout.splitlines() if proc.returncode == 0 else []
        if header:
            print(f"\n{header}")
        return self._numbered(items, prompt, multi=multi)

    def _numbered(self, items: Sequence[str], prompt: str, *, multi: bool) -> list[str]:
        print()
        for i, item in enumerate(items, 1):
            print(f"  {sty(str(i).rjust(3), C.CYAN)}  {item}")
        print()
        suffix = " (comma/range, q to cancel)" if multi else " (q to cancel)"
        raw = input(sty(prompt + suffix + " ", C.BOLD)).strip()
        if not raw or raw.lower() in {"q", "quit", "exit"}:
            return []
        if not multi:
            try:
                idx = int(raw)
                return [items[idx - 1]] if 1 <= idx <= len(items) else []
            except ValueError:
                # Allow exact text entry.
                return [raw] if raw in items else []

        picks: list[int] = []
        for token in re.split(r"[ ,]+", raw):
            if not token:
                continue
            if "-" in token:
                try:
                    a, b = (int(x) for x in token.split("-", 1))
                except ValueError:
                    continue
                step = 1 if a <= b else -1
                picks.extend(range(a, b + step, step))
            else:
                try:
                    picks.append(int(token))
                except ValueError:
                    continue
        return [items[i - 1] for i in picks if 1 <= i <= len(items)]


# ---------- Android / Termux intent relay ----------

def is_android_environment() -> bool:
    return bool(os.getenv("ANDROID_ROOT") or os.getenv("TERMUX_VERSION"))


def _android_user_id() -> str:
    raw = os.getenv("TERMUX__USER_ID", "0")
    return raw if raw.isdigit() else "0"


def _relay_encode(url: str) -> str:
    return base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def _relay_decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8")


_SUBTITLE_SUFFIXES = (".vtt", ".srt", ".ass", ".ssa")
_SUBTITLE_MIME = {
    ".vtt": "text/vtt; charset=utf-8",
    ".srt": "application/x-subrip; charset=utf-8",
    ".ass": "text/x-ssa; charset=utf-8",
    ".ssa": "text/x-ssa; charset=utf-8",
}


def _subtitle_suffix_for(url: str) -> str:
    """Cosmetic relay suffix for a subtitle URL; defaults to .vtt."""
    path = urlsplit(url).path.lower()
    for suffix in _SUBTITLE_SUFFIXES:
        if path.endswith(suffix):
            return suffix
    return ".vtt"


def _subtitle_mime_for_suffix(suffix: str) -> str:
    return _SUBTITLE_MIME.get(suffix.lower(), "text/vtt; charset=utf-8")


def _hls_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


_ANDROID_SUBTITLE_GROUP = "ani-py-subs"


def _subtitle_media_tag(
    subtitle_url: str,
    *,
    language: Optional[str] = None,
    label: Optional[str] = None,
) -> str:
    name = label or ("English" if language == "en" else "External")
    attrs = [
        "TYPE=SUBTITLES",
        f'GROUP-ID="{_ANDROID_SUBTITLE_GROUP}"',
        f'NAME="{_hls_attr(name)}"',
    ]
    if language:
        attrs.append(f'LANGUAGE="{_hls_attr(language)}"')
    attrs += [
        "DEFAULT=YES",
        "AUTOSELECT=YES",
        f'URI="{_hls_attr(subtitle_url)}"',
    ]
    return "#EXT-X-MEDIA:" + ",".join(attrs)


def _rewrite_hls_manifest(
    manifest: str,
    base_url: str,
    localize,
    subtitle_url: Optional[str] = None,
    subtitle_language: Optional[str] = None,
    subtitle_label: Optional[str] = None,
) -> str:
    """Route remote HLS URIs through localhost and safely attach one subtitle rendition."""
    uri_attr = re.compile(r'URI="([^"]+)"')
    out: list[str] = []
    has_stream_inf = False
    upstream_has_subtitles = bool(
        re.search(r"#EXT-X-MEDIA:[^\n\r]*TYPE=SUBTITLES", manifest, re.IGNORECASE)
        or re.search(r"SUBTITLES\s*=", manifest, re.IGNORECASE)
    )
    inject_subtitle = bool(subtitle_url) and not upstream_has_subtitles

    def proxied(value: str) -> str:
        absolute = urljoin(base_url, value)
        return localize(absolute) if urlsplit(absolute).scheme in {"http", "https"} else value

    for raw in manifest.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("#EXT-X-STREAM-INF"):
            has_stream_inf = True
            if inject_subtitle and "SUBTITLES=" not in line:
                line = f'{line},SUBTITLES="{_ANDROID_SUBTITLE_GROUP}"'
        if line.startswith("#"):
            line = uri_attr.sub(lambda m: f'URI="{proxied(m.group(1))}"', line)
        elif line.strip():
            line = proxied(line.strip())
        out.append(line)

    if inject_subtitle and has_stream_inf and subtitle_url:
        media = _subtitle_media_tag(
            subtitle_url,
            language=subtitle_language,
            label=subtitle_label,
        )
        if out and out[0] == "#EXTM3U":
            out.insert(1, media)
        else:
            out.insert(0, media)
    return "\n".join(out) + ("\n" if manifest.endswith(("\n", "\r")) else "")


def _wrap_hls_media_playlist(
    variant_url: str,
    subtitle_playlist_url: str,
    *,
    subtitle_language: Optional[str] = None,
    subtitle_label: Optional[str] = None,
) -> str:
    """Wrap a variant media playlist URL in a single-variant master playlist."""
    media = _subtitle_media_tag(
        subtitle_playlist_url,
        language=subtitle_language,
        label=subtitle_label,
    )
    # BANDWIDTH is required by EXT-X-STREAM-INF; this is a single-variant
    # wrapper, so the value is only a protocol placeholder estimate.
    return (
        "#EXTM3U\n"
        f"{media}\n"
        f'#EXT-X-STREAM-INF:BANDWIDTH=2000000,SUBTITLES="{_ANDROID_SUBTITLE_GROUP}"\n'
        f"{variant_url}\n"
    )


_EXTINF_RE = re.compile(r"#EXTINF:([0-9]+(?:\.[0-9]+)?)")


def _hls_media_duration(text: str) -> Optional[int]:
    """Sum EXTINF durations, rounded up; None when no usable entries exist."""
    total = sum(value for value in (float(item) for item in _EXTINF_RE.findall(text)) if value > 0)
    if total <= 0:
        return None
    return int(total) if float(total).is_integer() else int(total) + 1


def _subtitle_playlist(segment_url: str, duration: int) -> str:
    """Build a VOD WebVTT rendition playlist around one complete segment."""
    total = max(1, duration)
    return (
        "#EXTM3U\n"
        f"#EXT-X-TARGETDURATION:{total}\n"
        "#EXT-X-PLAYLIST-TYPE:VOD\n"
        f"#EXTINF:{total},\n"
        f"{segment_url}\n"
        "#EXT-X-ENDLIST\n"
    )


class _AndroidRelayHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self, address, handler, *, token: str, referer: str, user_agent: str,
        debug: bool = False, log_path: Optional[str] = None,
        subtitle_target: Optional[str] = None,
        subtitle_language: Optional[str] = None,
        subtitle_label: Optional[str] = None,
        allowed_targets: Optional[Iterable[str]] = None,
    ) -> None:
        super().__init__(address, handler)
        self.token = token
        self.referer = referer
        self.user_agent = user_agent
        self.debug = debug
        self.log_path = log_path
        self.subtitle_target = subtitle_target
        self.subtitle_language = subtitle_language
        self.subtitle_label = subtitle_label
        self.allowed_targets = set(allowed_targets) if allowed_targets is not None else None
        self.last_activity = time.monotonic()

    def _allow_target(self, target: str) -> None:
        if self.allowed_targets is not None:
            self.allowed_targets.add(target)

    def target_allowed(self, target: str) -> bool:
        return self.allowed_targets is None or target in self.allowed_targets

    def relay_url(self, target: str) -> str:
        self._allow_target(target)
        host, port = self.server_address[:2]
        return f"http://127.0.0.1:{port}/{self.token}/{_relay_encode(target)}"

    def subtitle_relay_url(self, target: str) -> str:
        self._allow_target(target)
        host, port = self.server_address[:2]
        suffix = _subtitle_suffix_for(target)
        return f"http://127.0.0.1:{port}/{self.token}/subtitle/{_relay_encode(target)}{suffix}"

    def subtitle_playlist_url_for(self, target: str, duration: int) -> str:
        host, port = self.server_address[:2]
        suffix = _subtitle_suffix_for(target)
        total = max(1, int(duration))
        return f"http://127.0.0.1:{port}/{self.token}/sublist/{total}/{_relay_encode(target)}{suffix}"

    def variant_url_for(self, target: str) -> str:
        # Same relay route with a flag so the handler serves the raw variant
        # media playlist instead of wrapping it in a master again.
        return self.relay_url(target) + "?variant=1"

    def _debug_log(self, message: str) -> None:
        if not self.debug or not self.log_path:
            return
        try:
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(message.rstrip() + "\n")
                handle.flush()
        except OSError:
            pass


class _AndroidRelayHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - must match BaseHTTPRequestHandler signature
        return

    @property
    def relay(self) -> _AndroidRelayHTTPServer:
        return self.server  # type: ignore[return-value]

    def do_HEAD(self) -> None:
        self._serve(send_body=False)

    def do_GET(self) -> None:
        self._serve(send_body=True)

    def _parse_relay_path(self) -> Optional[tuple[str, str, Optional[str], Optional[int]]]:
        """Return (upstream_target, kind, suffix, duration) or None when invalid."""
        prefix = f"/{self.relay.token}/"
        path = self.path.split("?", 1)[0]
        if not path.startswith(prefix):
            return None
        rest = path[len(prefix):]
        kind = "video"
        duration: Optional[int] = None
        suffix: Optional[str] = None
        if rest.startswith("subtitle/"):
            kind = "subtitle"
            rest = rest[len("subtitle/"):]
        elif rest.startswith("sublist/"):
            kind = "sublist"
            rest = rest[len("sublist/"):]
            duration_text, sep, rest = rest.partition("/")
            if not sep or not duration_text.isdigit():
                return None
            duration = int(duration_text)
        if kind in ("subtitle", "sublist"):
            for candidate in _SUBTITLE_SUFFIXES:
                if rest.lower().endswith(candidate):
                    suffix = candidate
                    rest = rest[: -len(candidate)]
                    break
            if suffix is None:
                return None
        try:
            target = _relay_decode(rest)
        except Exception:
            return None
        if urlsplit(target).scheme not in {"http", "https"}:
            return None
        if not self.relay.target_allowed(target):
            return None
        return target, kind, suffix, duration

    def _target(self) -> Optional[str]:
        parsed = self._parse_relay_path()
        return parsed[0] if parsed else None

    def _serve(self, *, send_body: bool) -> None:
        method = "GET" if send_body else "HEAD"
        parsed = self._parse_relay_path()
        if not parsed:
            self.relay._debug_log(f"[android-relay] video {method} invalid -> 403")
            self.send_error(403)
            return
        target, kind, sub_suffix, sub_duration = parsed
        if kind == "sublist":
            self.relay.last_activity = time.monotonic()
            playlist = _subtitle_playlist(
                self.relay.subtitle_relay_url(target), sub_duration or 0,
            ).encode("utf-8")
            self.relay._debug_log(
                f"[android-relay] subtitle {method} playlist -> 200 application/vnd.apple.mpegurl"
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(playlist)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            if send_body:
                self.wfile.write(playlist)
            return
        if kind == "subtitle":
            req_desc = f"subtitle {method} {sub_suffix}"
        else:
            req_desc = f"video {method} segment"
        self.relay.last_activity = time.monotonic()
        headers = {
            "User-Agent": self.relay.user_agent,
            "Accept-Encoding": "identity",
        }
        if self.relay.referer:
            headers["Referer"] = self.relay.referer
        likely_hls = ".m3u8" in urlsplit(target).path.lower()
        for name in ("Range", "If-Range", "If-None-Match", "If-Modified-Since"):
            if likely_hls and name in {"Range", "If-Range"}:
                continue
            value = self.headers.get(name)
            if value:
                headers[name] = value

        req = urllib_request.Request(target, headers=headers, method=method)
        try:
            upstream = urllib_request.urlopen(req, timeout=30)
        except urllib_error.HTTPError as exc:
            # Some CDNs reject HEAD; retry a lightweight GET for metadata.
            if not send_body and exc.code in {400, 403, 405, 501}:
                try:
                    upstream = urllib_request.urlopen(
                        urllib_request.Request(target, headers=headers, method="GET"), timeout=30
                    )
                except Exception as inner:
                    self.relay._debug_log(f"[android-relay] {req_desc} -> 502")
                    self.send_error(502, str(inner))
                    return
            else:
                self.relay._debug_log(f"[android-relay] {req_desc} -> {exc.code}")
                self.send_error(exc.code, str(exc.reason))
                return
        except Exception as exc:
            self.relay._debug_log(f"[android-relay] {req_desc} -> 502")
            self.send_error(502, str(exc))
            return

        try:
            status = getattr(upstream, "status", 200) or 200
            final_url = upstream.geturl()
            content_type = upstream.headers.get("Content-Type", "application/octet-stream")
            is_hls = (
                ".m3u8" in urlsplit(final_url).path.lower()
                or "mpegurl" in content_type.lower()
            )

            if is_hls and kind == "video":
                self.relay._debug_log(
                    f"[android-relay] video {method} hls -> {status} "
                    f"{content_type.split(';', 1)[0] or 'application/octet-stream'}"
                )

            if kind == "subtitle":
                # Subtitle bytes pass through unchanged, but the MIME type is
                # forced from the relay suffix: some CDNs serve subtitles as
                # application/octet-stream, which VLC for Android ignores.
                mime = _subtitle_mime_for_suffix(sub_suffix or ".vtt")
                raw = upstream.read()
                self.relay._debug_log(
                    f"[android-relay] subtitle {method} {sub_suffix} -> {status} {mime.split(';', 1)[0]}"
                )
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.close_connection = True
                self.end_headers()
                if send_body:
                    try:
                        self.wfile.write(raw)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                return

            if is_hls and send_body:
                raw = upstream.read()
                text = raw.decode("utf-8", "replace")
                hls_subtitle_target = (
                    self.relay.subtitle_target
                    if self.relay.subtitle_target
                    and _subtitle_suffix_for(self.relay.subtitle_target) == ".vtt"
                    else None
                )
                subtitle_url = (
                    self.relay.subtitle_relay_url(hls_subtitle_target)
                    if hls_subtitle_target
                    else None
                )
                variant_request = "variant=1" in urlsplit(self.path).query
                if "#EXT-X-STREAM-INF" in text:
                    body = _rewrite_hls_manifest(
                        text,
                        final_url,
                        self.relay.relay_url,
                        subtitle_url=subtitle_url,
                        subtitle_language=self.relay.subtitle_language,
                        subtitle_label=self.relay.subtitle_label,
                    ).encode("utf-8")
                elif subtitle_url and not variant_request:
                    duration = _hls_media_duration(text)
                    target_subtitle = self.relay.subtitle_target
                    if duration is None or target_subtitle is None:
                        body = _rewrite_hls_manifest(text, final_url, self.relay.relay_url).encode("utf-8")
                    else:
                        playlist_url = self.relay.subtitle_playlist_url_for(target_subtitle, duration)
                        body = _wrap_hls_media_playlist(
                            self.relay.variant_url_for(target),
                            playlist_url,
                            subtitle_language=self.relay.subtitle_language,
                            subtitle_label=self.relay.subtitle_label,
                        ).encode("utf-8")
                        self.relay._debug_log(
                            f"[android-relay] video {method} hls-media-wrapped -> {status} "
                            "application/vnd.apple.mpegurl"
                        )
                else:
                    body = _rewrite_hls_manifest(text, final_url, self.relay.relay_url).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type or "application/vnd.apple.mpegurl")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.close_connection = True
                self.end_headers()
                self.wfile.write(body)
                return

            if not is_hls:
                self.relay._debug_log(
                    f"[android-relay] video {method} segment -> {status} "
                    f"{content_type.split(';', 1)[0] or 'application/octet-stream'}"
                )

            self.send_response(status)
            passthrough = (
                "Content-Type", "Content-Length", "Content-Range", "Accept-Ranges",
                "ETag", "Last-Modified", "Cache-Control",
            )
            for name in passthrough:
                value = upstream.headers.get(name)
                if value:
                    self.send_header(name, value)
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            if not send_body:
                return
            while True:
                chunk = upstream.read(128 * 1024)
                if not chunk:
                    break
                self.relay.last_activity = time.monotonic()
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
        finally:
            upstream.close()


def run_android_relay(config_path: str) -> int:
    config_file = Path(config_path)
    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))
    except Exception:
        return 2
    try:
        config_file.unlink()
    except OSError:
        pass

    token = str(config.get("token") or "")
    ready = Path(str(config.get("ready") or ""))
    if not token or not str(ready):
        return 2
    referer = str(config.get("referer") or "")
    initial_target = str(config.get("initial_target") or "") or None
    subtitle_target = str(config.get("subtitle_target") or "") or None
    subtitle_language = str(config.get("subtitle_language") or "") or None
    subtitle_label = str(config.get("subtitle_label") or "") or None
    user_agent = str(config.get("user_agent") or USER_AGENT)
    idle_timeout = max(60.0, float(config.get("idle_timeout") or 3600))
    debug = bool(config.get("debug") or False)
    log_file = str(config.get("log_file") or "") or None

    server = _AndroidRelayHTTPServer(
        ("127.0.0.1", 0), _AndroidRelayHandler,
        token=token, referer=referer, user_agent=user_agent,
        debug=debug,
        log_path=log_file,
        subtitle_target=subtitle_target,
        subtitle_language=subtitle_language,
        subtitle_label=subtitle_label,
        allowed_targets=[target for target in (initial_target, subtitle_target) if target],
    )

    def request_shutdown(_signum: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, request_shutdown)
    server.timeout = 1.0
    server._debug_log("[android-relay] started")
    ready.write_text(json.dumps({"port": server.server_address[1], "token": token}), encoding="utf-8")
    try:
        while time.monotonic() - server.last_activity < idle_timeout:
            server.handle_request()
    finally:
        server._debug_log("[android-relay] stopped")
        server.server_close()
        try:
            ready.unlink()
        except OSError:
            pass
    return 0


@dataclasses.dataclass(frozen=True)
class AndroidRelayEndpoint:
    port: int
    token: str

    def url_for(self, target: str) -> str:
        return f"http://127.0.0.1:{self.port}/{self.token}/{_relay_encode(target)}"

    def subtitle_url_for(self, target: str) -> str:
        # VLC for Android keys subtitle handling off the resource name/type,
        # so the relayed subtitle URL ends in a cosmetic subtitle suffix.
        # The suffix is route-only; the upstream URL is never mutated.
        suffix = _subtitle_suffix_for(target)
        return f"http://127.0.0.1:{self.port}/{self.token}/subtitle/{_relay_encode(target)}{suffix}"


# ---------- player / downloader ----------

class Playback:
    """Launch players and own one private mpv IPC endpoint.

    The private endpoint is intentional: a user's mpv.conf may define a global
    socket such as /tmp/mpvsocket that other tools depend on.  Passing our own
    --input-ipc-server on the command line prevents ani-py from hijacking or
    colliding with that shared socket while still loading the rest of mpv.conf,
    input.conf and user scripts normally.
    """

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.player = self._detect_player()
        self.proc: Optional[subprocess.Popen] = None
        self.ipc_path: Optional[Path] = None
        self._detached = False
        self._android_launched = False
        self._android_relay_proc: Optional[subprocess.Popen] = None
        self._android_relay_dir: Optional[Path] = None

    def _detect_player(self) -> str:
        if self.args.download:
            return "download"

        # --player/-p is the single player selector on every platform.
        # Android apps are launched by VIEW intents; do not preflight packages
        # with `pm path`, which is unreliable from an ordinary Termux UID.
        requested_player = (self.args.player or "").strip()
        if is_android_environment():
            requested = requested_player or "auto"
            requested = requested.lower()
            if requested not in {"auto", "vlc", "mpv"}:
                fail("On Termux/Android, --player supports: auto, vlc, mpv.")
            return f"android_{requested}"

        if requested_player and requested_player.lower() != "auto":
            resolved = which_first([requested_player])
            if not resolved:
                fail(f"Requested player '{requested_player}' was not found.")
            return resolved

        system = platform.system()
        if system == "Darwin":
            resolved = which_first(["iina", "/Applications/IINA.app/Contents/MacOS/iina-cli", "mpv", "vlc"])
        elif system == "Windows":
            resolved = which_first(["mpv.exe", "vlc.exe"])
        else:
            resolved = which_first(["mpv", "vlc"])
        if not resolved:
            fail("No media player found. Install mpv or VLC, or pass --player.")
        return resolved

    def _is_android(self) -> bool:
        return self.player in {"android_auto", "android_vlc", "android_mpv"}

    def _is_mpv(self) -> bool:
        if self.player == "download" or self._is_android():
            return False
        return "mpv" in Path(self.player).name.lower()

    def _ipc_supported(self) -> bool:
        return os.name == "posix" and hasattr(socket, "AF_UNIX")

    def auto_next_supported(self) -> bool:
        """Whether playback exposes a reliable natural-EOF signal."""
        return self._is_mpv() and self._ipc_supported()

    def wait_for_completion(self, poll_interval: float = 0.2) -> str:
        """Wait for the current mpv item to finish.

        Returns "eof" only when mpv reports that the media reached its
        natural end. Any manual stop/close, player exit, or IPC loss returns
        "closed" so callers never guess that an interrupted episode was
        completed.
        """
        if not self.auto_next_supported() or self.ipc_path is None:
            return "unsupported"

        while True:
            proc = self.proc
            if proc is not None and proc.poll() is not None:
                self.proc = None
                self._cleanup_ipc()
                return "closed"
            try:
                reached = self._ipc(["get_property", "eof-reached"], timeout=0.6)
            except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
                proc = self.proc
                if proc is not None and proc.poll() is not None:
                    self.proc = None
                    self._cleanup_ipc()
                return "closed"
            if reached is True:
                return "eof"
            if poll_interval > 0:
                time.sleep(poll_interval)

    def _find_rish(self) -> Optional[str]:
        candidates = [
            shutil.which("rish"),
            str(Path.home() / "rish"),
            str(Path.home() / ".local/bin/rish"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
                return candidate
        return None

    @staticmethod
    def _android_launch_ok(proc: subprocess.CompletedProcess[str]) -> bool:
        combined = f"{proc.stdout}\n{proc.stderr}".lower()
        bad = (
            "unable to resolve intent",
            "error: activity not started",
            "failure calling service",
            "permission denied",
            "securityexception",
            "exception occurred",
            "am.sock",
            "connection refused",
        )
        return proc.returncode == 0 and not any(marker in combined for marker in bad)

    def _android_intent(self, player: str, url: str, title: str, subtitle: Optional[str]) -> list[str]:
        cmd = [
            "am", "start", "--user", _android_user_id(),
            "-a", "android.intent.action.VIEW",
        ]
        if player == "vlc":
            # Package targeting is more stable than a private activity class and
            # still lets Android route VIEW to VLC's exported playback entry.
            cmd += ["-t", "video/*", "-p", "org.videolan.vlc"]
        elif player == "mpv":
            # mpv-android documents package targeting + video/any for URLs whose
            # path does not carry a recognizable media extension.
            cmd += ["-t", "video/any", "-p", "is.xyz.mpv"]
        else:
            cmd += ["-t", "video/*"]
        cmd += ["-d", url, "--es", "title", title]
        # VLC accepts this simple string extra. mpv-android's official `subs`
        # extra is ParcelableArray<Uri>, which shell `am` cannot construct.
        if subtitle and player in {"vlc", "auto"}:
            cmd += ["--es", "subtitles_location", subtitle]
        return cmd

    def _android_debug(self) -> bool:
        return bool(getattr(self.args, "android_debug", False))

    def _print_android_intent_debug(
        self, intent: list[str], subtitle_url: Optional[str], subtitle_suffix: Optional[str],
    ) -> None:
        component = "org.videolan.vlc/org.videolan.vlc.gui.video.VideoPlayerActivity"
        if "-n" in intent:
            target = "VLC VideoPlayerActivity"
        elif "org.videolan.vlc" in intent:
            target = "VLC package"
        elif "is.xyz.mpv" in intent:
            target = "mpv-android package"
        else:
            target = "Android resolver"
        print("Android intent:", file=sys.stderr)
        print(f"  target: {target}", file=sys.stderr)
        if "-n" in intent and component not in intent:
            target = "Android component"
            print(f"  target: {target}", file=sys.stderr)
        has_subtitle = "subtitles_location" in intent
        print(f"  subtitle extra: {'present' if has_subtitle else 'none'}", file=sys.stderr)
        scheme = urlsplit(subtitle_url).scheme if has_subtitle and subtitle_url else "none"
        print(f"  subtitle extra scheme: {scheme or 'none'}", file=sys.stderr)
        suffix = subtitle_suffix if has_subtitle and subtitle_suffix else "none"
        print(f"  subtitle suffix: {suffix}", file=sys.stderr)

    def _print_android_result_debug(self, proc: subprocess.CompletedProcess[str]) -> None:
        combined = proc.stdout.strip() or proc.stderr.strip() or "no output"
        first_line = next((line.strip() for line in combined.splitlines() if line.strip()), "no output")
        sanitized = re.sub(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s}\]]+", "<redacted-url>", first_line)
        if len(sanitized) > 240:
            sanitized = sanitized[:237] + "..."
        print("Android intent result:", file=sys.stderr)
        print(f"  return code: {proc.returncode}", file=sys.stderr)
        print(f"  result: {sanitized}", file=sys.stderr)

    def _android_diagnostics(
        self, requested: str, relay_active: bool, subtitle: Optional[str], subtitle_suffix: Optional[str],
    ) -> None:
        # Concise pre-launch report for --android-debug. Never prints signed
        # upstream URLs, tokens, or file contents.
        player_label = {"vlc": "VLC", "mpv": "mpv-android"}.get(requested, requested)
        print("Android playback diagnostics", file=sys.stderr)
        print(f"  player: {player_label}", file=sys.stderr)
        print(f"  relay: {'active' if relay_active else 'inactive (direct URL)'}", file=sys.stderr)
        if not subtitle:
            print("  subtitle: none", file=sys.stderr)
            return
        print("  subtitle: resolved", file=sys.stderr)
        subtitle_type = (subtitle_suffix or _subtitle_suffix_for(subtitle)).lstrip('.').upper()
        print(f"  subtitle type: {subtitle_type}", file=sys.stderr)
        if relay_active:
            print("  subtitle transport: localhost relay", file=sys.stderr)
            print(f"  subtitle relay suffix: {subtitle_suffix or _subtitle_suffix_for(subtitle)}", file=sys.stderr)
            if self._android_relay_dir is not None:
                print(f"  relay log: {self._android_relay_dir / 'android-relay.log'}", file=sys.stderr)
        else:
            print("  subtitle transport: direct URL", file=sys.stderr)

    def _start_android_relay(
        self,
        referer: str,
        initial_target: str,
        subtitle: Optional[str] = None,
        subtitle_language: Optional[str] = None,
        subtitle_label: Optional[str] = None,
    ) -> Optional[AndroidRelayEndpoint]:
        # A loopback relay makes Referer-protected streams usable by Android
        # players without requiring player-specific config or Shizuku. It also
        # rewrites HLS child playlists/segments so every request keeps headers.
        self._stop_android_relay()
        root = Path(tempfile.mkdtemp(prefix="ani-py-relay-"))
        config = root / "config.json"
        ready = root / "ready.json"
        token = secrets.token_urlsafe(18)
        debug = self._android_debug()
        log_file = str(root / "android-relay.log") if debug else ""
        if debug and log_file:
            try:
                Path(log_file).write_text("", encoding="utf-8")
            except OSError:
                pass
        config.write_text(json.dumps({
            "ready": str(ready),
            "token": token,
            "referer": referer,
            "initial_target": initial_target,
            "subtitle_target": subtitle,
            "subtitle_language": subtitle_language,
            "subtitle_label": subtitle_label,
            "user_agent": USER_AGENT,
            "idle_timeout": 3600,
            "debug": debug,
            "log_file": log_file,
        }), encoding="utf-8")
        try:
            config.chmod(0o600)
        except OSError:
            pass

        cmd = [sys.executable, str(Path(__file__).resolve()), "--_android-relay-config", str(config)]
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            warn(f"Could not start Android header relay ({exc}); trying the raw stream URL.")
            shutil.rmtree(root, ignore_errors=True)
            return None

        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            if ready.exists():
                try:
                    data = json.loads(ready.read_text(encoding="utf-8"))
                    endpoint = AndroidRelayEndpoint(port=int(data["port"]), token=str(data["token"]))
                    self._android_relay_proc = proc
                    self._android_relay_dir = root
                    return endpoint
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    pass
            if proc.poll() is not None:
                break
            time.sleep(0.05)

        try:
            proc.terminate()
        except OSError:
            pass
        shutil.rmtree(root, ignore_errors=True)
        warn("Android header relay did not start; trying the raw stream URL.")
        return None

    def _stop_android_relay(self) -> None:
        proc = self._android_relay_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
        self._android_relay_proc = None
        if self._android_relay_dir is not None:
            shutil.rmtree(self._android_relay_dir, ignore_errors=True)
        self._android_relay_dir = None

    def _run_android_intent(self, intent: list[str]) -> bool:
        if self._android_debug():
            subtitle_url: Optional[str] = None
            if "subtitles_location" in intent:
                subtitle_index = intent.index("subtitles_location") + 1
                subtitle_url = intent[subtitle_index] if subtitle_index < len(intent) else None
            self._print_android_intent_debug(intent, subtitle_url, _subtitle_suffix_for(subtitle_url) if subtitle_url else None)
        am = shutil.which("am")
        if am:
            direct = [am, *intent[1:]]
            proc = run_capture(direct)
            if self._android_debug():
                self._print_android_result_debug(proc)
            if self._android_launch_ok(proc):
                return True

        # rish/Shizuku is intentionally only a compatibility fallback. Most
        # Termux users should never need it; users who already have it get a
        # transparent retry of the same targeted intent.
        rish = self._find_rish()
        if rish:
            proc = run_capture([rish, "-c", shlex.join(intent)])
            if self._android_debug():
                self._print_android_result_debug(proc)
            if self._android_launch_ok(proc):
                warn("Direct Termux intent failed; launched through existing rish/Shizuku fallback.")
                return True
        return False

    def _android_chooser(self, url: str) -> bool:
        opener = shutil.which("termux-open")
        if not opener:
            return False
        proc = run_capture([opener, "--view", "--content-type", "video/*", url])
        return proc.returncode == 0

    def _play_android(
        self,
        stream: Stream,
        *,
        title: str,
        subtitle: Optional[str],
        referer: str,
        subtitle_language: Optional[str] = None,
        subtitle_label: Optional[str] = None,
    ) -> int:
        relay = (
            self._start_android_relay(
                referer,
                stream.url,
                subtitle,
                subtitle_language=subtitle_language,
                subtitle_label=subtitle_label,
            )
            if urlsplit(stream.url).scheme in {"http", "https"}
            else None
        )
        video_url = relay.url_for(stream.url) if relay else stream.url
        requested = self.player.removeprefix("android_")
        # WebVTT subtitles ride the relayed HLS playlist as a native rendition
        # for VLC and mpv-android. VLC also receives subtitles_location as a
        # fallback, without writing persistent files into shared storage.
        subtitle_url: Optional[str] = None
        subtitle_suffix: Optional[str] = None
        if subtitle:
            subtitle_url = relay.subtitle_url_for(subtitle) if relay else subtitle
            subtitle_suffix = _subtitle_suffix_for(subtitle)

        if self._android_debug():
            self._android_diagnostics(requested, relay is not None, subtitle_url or subtitle, subtitle_suffix)
        # `auto` intentionally asks Android first instead of querying packages.
        # This supports VLC, mpv-android, MX Player, Just Player, etc. without
        # brittle `pm path` checks. Explicit vlc/mpv modes pin a package.
        if requested == "auto":
            intent = self._android_intent("auto", video_url, title, subtitle_url)
            if self._run_android_intent(intent):
                self._android_launched = True
                return 0
            if self._android_chooser(video_url):
                self._android_launched = True
                return 0
            # If implicit dispatch failed, targeted retries can still help when
            # a device's resolver behaves oddly.
            for candidate in ("vlc", "mpv"):
                if self._run_android_intent(self._android_intent(candidate, video_url, title, subtitle_url)):
                    self._android_launched = True
                    return 0
        else:
            intent = self._android_intent(requested, video_url, title, subtitle_url)
            if self._run_android_intent(intent):
                self._android_launched = True
                return 0
            # Preserve explicit choice as long as possible; only then offer the
            # ordinary Android chooser rather than requiring Shizuku setup.
            if self._android_chooser(video_url):
                warn(f"Could not target {requested}; opened Android's video-player chooser instead.")
                self._android_launched = True
                return 0

        self._stop_android_relay()
        fail(
            "Could not launch an Android video player. Install termux-tools/termux-am and a video player "
            "(VLC or mpv-android). rish/Shizuku is supported only as an optional fallback."
        )
        return 1

    def _skip_args(self, mal_id: Optional[str], episode: str) -> list[str]:
        """Return episode-specific mpv flags from ani-skip.

        Current ani-skip accepts a known MyAnimeList id directly with -i/--id.
        A missing MAL id or a failing ani-skip invocation should never fail
        playback, but it must be visible instead of silently disabling --skip.
        """
        if not self.args.skip:
            return []
        if not mal_id:
            warn("--skip requested, but the provider did not expose a MAL id for this episode.")
            return []
        exe = shutil.which("ani-skip")
        if not exe:
            warn("--skip requested, but ani-skip is not installed.")
            return []
        # Providers already give us the MAL id, so use ani-skip's direct-id
        # interface instead of sending a numeric id through the query path.
        command = [exe, "-i", mal_id, "-e", episode]
        proc = run_capture(command)
        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
            warn(f"ani-skip failed for episode {episode}: {detail}")
            return []
        flags = split_flags(proc.stdout.strip())
        if not flags:
            warn(f"ani-skip returned no mpv flags for episode {episode}.")
        return flags

    def _make_ipc_path(self) -> Path:
        # Explicit opt-in can be used to share a socket, but the default must be
        # private so tools such as yt-cli/mpv-control can keep /tmp/mpvsocket.
        explicit = self.args.ipc_socket or os.getenv("ANI_PY_IPC_SOCKET")
        if explicit:
            return Path(explicit).expanduser()
        root = Path(os.getenv("XDG_RUNTIME_DIR") or tempfile.gettempdir())
        uid = os.getuid() if hasattr(os, "getuid") else "user"
        return root / f"ani-py-{uid}-{os.getpid()}.sock"

    def _ipc(self, command: list[object], timeout: float = 2.0) -> object:
        if self.ipc_path is None:
            raise RuntimeError("mpv IPC is not active")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(self.ipc_path))
            payload = json.dumps({"command": command}).encode("utf-8") + b"\n"
            sock.sendall(payload)
            buf = b""
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    msg = json.loads(line.decode("utf-8", errors="replace"))
                    if "error" not in msg:
                        continue
                    if msg.get("error") != "success":
                        raise RuntimeError(str(msg.get("error")))
                    return msg.get("data")
            raise RuntimeError("no reply from mpv")
        finally:
            sock.close()

    def _wait_ipc(self, timeout: float = 4.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                return False
            try:
                self._ipc(["get_property", "path"], timeout=0.35)
                return True
            except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
                time.sleep(0.08)
        return False

    def _wait_path(self, expected: str, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                current = self._ipc(["get_property", "path"], timeout=0.5)
                if current == expected:
                    return True
            except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
                pass
            time.sleep(0.08)
        return False

    def active(self) -> bool:
        if self._is_android():
            relay_alive = self._android_relay_proc is None or self._android_relay_proc.poll() is None
            return self._android_launched and relay_alive
        if not self._is_mpv() or self.ipc_path is None:
            return self.proc is not None and self.proc.poll() is None
        try:
            self._ipc(["get_property", "path"], timeout=0.4)
            return True
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
            return False

    def adopt_detached_session(self, session: DetachedSession) -> bool:
        """Adopt an existing ani-py mpv IPC socket without restarting playback."""
        if is_android_environment():
            return False
        self.player = session.player
        self.proc = None
        self.ipc_path = Path(session.socket).expanduser()
        self._detached = False
        if not self._is_mpv() or not self.active():
            self.ipc_path = None
            return False
        return True

    def current_path(self) -> Optional[str]:
        if not self._is_mpv() or self.ipc_path is None:
            return None
        try:
            value = self._ipc(["get_property", "path"], timeout=0.5)
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, str) and value else None

    def reattachable(self) -> bool:
        return not self._is_android() and self._is_mpv() and self.ipc_path is not None and self.active()

    def _kill_process_group(self) -> None:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            self.proc = None
            return
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                proc.terminate()
            proc.wait(timeout=2)
        except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
            try:
                if proc.poll() is None:
                    proc.kill()
            except OSError:
                pass
        self.proc = None

    def stop(self) -> None:
        self._detached = False
        if self._is_android():
            # Android intent players are external apps; killing the localhost
            # relay is the least invasive way to stop an ani-py-proxied stream.
            self._stop_android_relay()
            self._android_launched = False
            return
        if self._is_mpv() and self.ipc_path is not None:
            try:
                self._ipc(["quit"], timeout=0.7)
            except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
                pass
            if self.proc is not None:
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._kill_process_group()
        else:
            self._kill_process_group()
        self.proc = None
        self._cleanup_ipc()

    def detach(self) -> None:
        # Leave the player alive. Android's relay is a detached child process
        # with an idle timeout, so playback survives ani-py exiting.
        self._detached = True
        self.proc = None

    def _cleanup_ipc(self) -> None:
        path = self.ipc_path
        if path is not None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
        self.ipc_path = None

    def _mpv_command(
        self,
        stream: Stream,
        *,
        title: str,
        subtitle: Optional[str],
        referer: str,
        mal_id: Optional[str],
        episode: str,
        keep_open: bool = True,
    ) -> list[str]:
        extra = split_flags(os.getenv("ANI_PY_PLAYER_FLAGS", "")) + self.args.player_flag
        ipc_args: list[str] = []
        if self._ipc_supported():
            self.ipc_path = self._make_ipc_path()
            explicit_ipc = bool(self.args.ipc_socket or os.getenv("ANI_PY_IPC_SOCKET"))
            if self.ipc_path.exists():
                in_use = False
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe.settimeout(0.25)
                try:
                    probe.connect(str(self.ipc_path))
                    in_use = True
                except OSError:
                    pass
                finally:
                    probe.close()
                if in_use and explicit_ipc:
                    fail(
                        f"IPC socket is already in use: {self.ipc_path}. "
                        "Choose another --ipc-socket or omit it for a private socket."
                    )
                if not in_use:
                    try:
                        self.ipc_path.unlink()
                    except OSError:
                        pass
            ipc_args = [f"--input-ipc-server={self.ipc_path}"]
        else:
            self.ipc_path = None
        cmd = [
            self.player,
            *ipc_args,
            f"--keep-open={'yes' if keep_open else 'no'}",
            "--video=auto",
            "--vid=auto",
            f"--referrer={referer}",
            f"--force-media-title={title}",
            # Some HLS hosts serve MPEG-TS segments under decoy extensions
            # (.jpg, .html, ...); ffmpeg's hls demuxer rejects those unless its
            # extension allowlist is widened, and only the demuxer option works.
            "--demuxer-lavf-o=allowed_extensions=ALL",
        ]
        if subtitle:
            cmd.append(f"--sub-file={subtitle}")
        cmd += self._skip_args(mal_id, episode) + extra + [stream.url]
        return cmd

    def play(
        self,
        stream: Stream,
        *,
        title: str,
        subtitle: Optional[str],
        referer: str,
        mal_id: Optional[str],
        episode: str,
        subtitle_language: Optional[str] = None,
        subtitle_label: Optional[str] = None,
        foreground: bool = False,
        keep_open: bool = True,
    ) -> int:
        if self.player == "download":
            return self.download(
                stream,
                title=title,
                subtitle=subtitle,
                referer=referer,
                subtitle_language=subtitle_language,
                subtitle_label=subtitle_label,
            )

        extra = split_flags(os.getenv("ANI_PY_PLAYER_FLAGS", "")) + self.args.player_flag
        basename = self.player if self._is_android() else Path(self.player).name.lower()

        if self._is_android():
            if self.args.skip:
                warn("--skip is not available through Android intent players; playback will continue normally.")
            return self._play_android(
                stream,
                title=title,
                subtitle=subtitle,
                referer=referer,
                subtitle_language=subtitle_language,
                subtitle_label=subtitle_label,
            )

        if "mpv" in basename:
            # Never leave two ani-py-owned mpv instances around.
            if self.active():
                self.stop()
            cmd = self._mpv_command(
                stream, title=title, subtitle=subtitle, referer=referer,
                mal_id=mal_id, episode=episode, keep_open=keep_open,
            )
        elif "iina" in basename:
            if self.args.skip:
                warn("--skip is supported only with mpv; IINA playback will continue without ani-skip flags.")
            cmd = [self.player, f"--mpv-referrer={referer}", f"--mpv-force-media-title={title}", "--no-stdin"]
            if subtitle:
                escaped_subtitle = subtitle.replace(":", r"\:")
                cmd.append("--mpv-sub-files=" + escaped_subtitle)
            cmd += extra + [stream.url]
        elif "vlc" in basename:
            if self.args.skip:
                warn("--skip is supported only with mpv; VLC playback will continue without ani-skip flags.")
            cmd = [self.player, f"--http-referrer={referer}", "--play-and-exit", f"--meta-title={title}"] + extra + [stream.url]
            if subtitle:
                cmd.append(f":input-slave={subtitle}")
        else:
            if self.args.skip:
                warn("--skip is supported only with mpv; the selected player will ignore it.")
            cmd = [self.player] + extra + [stream.url]

        if foreground or self.args.no_detach or self.args.exit_after_play:
            rc = subprocess.run(cmd).returncode
            if "mpv" in basename:
                self._cleanup_ipc()
            return rc

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if "mpv" in basename and self.ipc_path is not None and not self._wait_ipc():
            warn("mpv started, but ani-py could not connect to its private IPC socket.")
        return 0

    def replace(
        self,
        stream: Stream,
        *,
        title: str,
        subtitle: Optional[str],
        referer: str,
        mal_id: Optional[str],
        episode: str,
        subtitle_language: Optional[str] = None,
        subtitle_label: Optional[str] = None,
    ) -> int:
        """Replace the current mpv item in-place; restart only as a fallback."""
        if not self._is_mpv() or not self._ipc_supported() or self.args.skip or not self.active():
            # --skip may carry episode-specific mpv flags, so a fresh process is
            # safer than trying to mutate unknown script options over IPC.
            if self.active():
                self.stop()
            return self.play(
                stream,
                title=title,
                subtitle=subtitle,
                referer=referer,
                mal_id=mal_id,
                episode=episode,
                subtitle_language=subtitle_language,
                subtitle_label=subtitle_label,
            )

        try:
            self._ipc(["set_property", "referrer", referer])
            self._ipc(["set_property", "force-media-title", title])
            self._ipc(["loadfile", stream.url, "replace"])
            self._wait_path(stream.url)
            self._ipc(["set_property", "force-media-title", title])
            if subtitle:
                self._ipc(["sub-add", subtitle, "select"])
            return 0
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            warn(f"Live mpv switch failed ({exc}); restarting the player.")
            self.stop()
            return self.play(
                stream,
                title=title,
                subtitle=subtitle,
                referer=referer,
                mal_id=mal_id,
                episode=episode,
                subtitle_language=subtitle_language,
                subtitle_label=subtitle_label,
            )

    def set_subtitle(self, track: Optional[SubtitleTrack]) -> bool:
        """Switch subtitle tracks in-place when mpv IPC is available."""
        if not self._is_mpv() or not self.active():
            return False
        try:
            if track is None:
                self._ipc(["set_property", "sid", "no"])
                return True
            # Keep previously loaded tracks available for quick switching, but
            # select the requested external subtitle immediately.
            self._ipc([
                "sub-add",
                track.url,
                "select",
                track.label or "",
                track.language or "",
            ])
            return True
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
            return False

    def replay(self) -> bool:
        if not self._is_mpv() or not self.active():
            return False
        try:
            self._ipc(["seek", 0, "absolute"])
            self._ipc(["set_property", "pause", False])
            return True
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
            return False

    def download(
        self,
        stream: Stream,
        *,
        title: str,
        subtitle: Optional[str],
        referer: str,
        subtitle_language: Optional[str] = None,
        subtitle_label: Optional[str] = None,
    ) -> int:
        outdir = Path(os.getenv("ANI_PY_DOWNLOAD_DIR", ".")).expanduser()
        outdir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip() or "episode"

        if subtitle:
            curl = HttpClient().exe
            raw_tag = subtitle_language or subtitle_label
            suffix = _subtitle_suffix_for(subtitle)
            if raw_tag:
                tag = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_tag).strip("-._") or "sub"
                subtitle_name = f"{safe}.{tag}{suffix}"
            else:
                subtitle_name = f"{safe}{suffix}"
            sub_cmd = [
                curl, "--fail", "-sS", "-L", "--max-time", "30",
                "-A", USER_AGENT, "-e", referer, subtitle,
                "-o", str(outdir / subtitle_name),
            ]
            sub_proc = subprocess.run(sub_cmd)
            if sub_proc.returncode != 0:
                warn(f"Subtitle download failed for {title}; continuing with the video download.")

        yt_dlp = shutil.which("yt-dlp")
        ffmpeg = shutil.which("ffmpeg")
        output = str(outdir / f"{safe}.mp4")
        if yt_dlp:
            cmd = [
                yt_dlp,
                "--referer", referer,
                "--user-agent", USER_AGENT,
                "--no-skip-unavailable-fragments",
                "--fragment-retries", "infinite",
                "-N", "16",
                "-o", output,
                stream.url,
            ]
        elif ffmpeg:
            cmd = [
                ffmpeg,
                "-extension_picky", "0",
                "-referer", referer,
                "-user_agent", USER_AGENT,
                "-loglevel", "error", "-stats",
                "-i", stream.url,
                "-c", "copy",
                output,
            ]
        else:
            fail("Download mode requires yt-dlp or ffmpeg.")
        return subprocess.run(cmd).returncode


# ---------- selection helpers ----------

def stream_rank(stream: Stream) -> int:
    m = re.match(r"(\d+)", stream.quality)
    return int(m.group(1)) if m else 0


def choose_quality(streams: Sequence[Stream], requested: str) -> Stream:
    if not streams:
        fail("No streams available.")
    req = requested.lower().rstrip("p")
    if req == "best":
        return max(streams, key=stream_rank)
    if req == "worst":
        positive = [s for s in streams if stream_rank(s)]
        return min(positive, key=stream_rank) if positive else streams[-1]
    wanted = re.sub(r"\D", "", req)
    if wanted:
        for stream in streams:
            if re.sub(r"\D", "", stream.quality) == wanted:
                return stream
        warn(f"Quality {requested} not found; using best.")
    return max(streams, key=stream_rank)


def choose_subtitle_track(bundle: StreamBundle, preference: Optional[str]) -> Optional[SubtitleTrack]:
    tracks = bundle.subtitle_tracks()
    if not tracks:
        return None

    pref = (preference or "auto").strip()
    folded = pref.casefold()
    if folded in {"off", "none", "no", "false", "0"}:
        return None

    if folded.startswith("label:"):
        wanted = folded.split(":", 1)[1].strip()
        exact = next((track for track in tracks if (track.label or "").casefold() == wanted), None)
        if exact:
            return exact

    if folded not in {"", "auto", "default"}:
        language = HianimeProvider._subtitle_language(pref)
        if language:
            exact_language = next((track for track in tracks if track.language == language), None)
            if exact_language:
                return exact_language
        exact_label = next((track for track in tracks if (track.label or "").casefold() == folded), None)
        if exact_label:
            return exact_label
        partial_label = next((track for track in tracks if folded in (track.label or "").casefold()), None)
        if partial_label:
            return partial_label
        return None

    default = next((track for track in tracks if track.default), None)
    if default:
        return default
    if bundle.subtitle:
        legacy = next((track for track in tracks if track.url == bundle.subtitle), None)
        if legacy:
            return legacy
    return next((track for track in tracks if track.language == "en"), None) or tracks[0]


def subtitle_menu_rows(
    tracks: Sequence[SubtitleTrack], current: Optional[SubtitleTrack]
) -> tuple[list[str], dict[str, Optional[SubtitleTrack]]]:
    rows = ["Off"]
    mapping: dict[str, Optional[SubtitleTrack]] = {"Off": None}
    for track in tracks:
        parts = [track.name]
        if track.language and track.language.casefold() not in track.name.casefold():
            parts.append(f"[{track.language}]")
        if track.default:
            parts.append("(default)")
        if current is not None and track.url == current.url:
            parts.append("• current")
        row = " ".join(parts)
        if row in mapping:
            row = f"{row}  {len(mapping)}"
        rows.append(row)
        mapping[row] = track
    return rows, mapping


def episode_index(episodes: Sequence[Episode], number: str) -> Optional[int]:
    for i, ep in enumerate(episodes):
        if ep.number == number:
            return i
    return None


def parse_episode_spec(spec: str, episodes: Sequence[Episode]) -> list[Episode]:
    if not spec:
        return []
    numbers = [ep.number for ep in episodes]
    if spec == "0":
        return [episodes[0]] if episodes else []
    if spec == "-1":
        return [episodes[-1]] if episodes else []
    if spec in numbers:
        return [episodes[numbers.index(spec)]]

    # Accept 2-5, 2:5, 2..5; episode identifiers may themselves be decimal.
    m = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*(?:-|:|\.\.)\s*(-?\d+(?:\.\d+)?)\s*", spec)
    if m:
        start, end = m.groups()
        start = numbers[0] if start == "0" else numbers[-1] if start == "-1" else start
        end = numbers[-1] if end == "-1" else numbers[0] if end == "0" else end
        if start in numbers and end in numbers:
            a, b = numbers.index(start), numbers.index(end)
            step = 1 if a <= b else -1
            return [episodes[i] for i in range(a, b + step, step)]
    return []


def format_anime_rows(anime: Sequence[Anime]) -> tuple[list[str], dict[str, Anime]]:
    width = max(3, len(str(len(anime))))
    mapping: dict[str, Anime] = {}
    rows: list[str] = []
    for i, item in enumerate(anime, 1):
        row = f"{str(i).rjust(width)}  {item.title}"
        rows.append(row)
        mapping[row] = item
    return rows, mapping


def format_episode_rows(episodes: Sequence[Episode]) -> tuple[list[str], dict[str, Episode]]:
    rows: list[str] = []
    mapping: dict[str, Episode] = {}
    for ep in episodes:
        row = f"Episode {ep.number}"
        rows.append(row)
        mapping[row] = ep
    return rows, mapping


# ---------- app ----------

class App:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.http = HttpClient()
        order = [x.strip().lower() for x in args.provider_order.split(",") if x.strip()]
        self.providers = ProviderManager(
            [
                HianimeProvider(self.http),
                AniLightProvider(self.http),
            ],
            order,
        )
        self.history = HistoryStore()
        self.session_store = DetachedSessionStore()
        self.menu = Menu(args.menu, args.menu_flags)
        self.playback = None if args.list_providers else Playback(args)
        self.bundle_cache: dict[tuple[str, str, str, str], StreamBundle] = {}
        self.episode_cache: dict[tuple[str, str], list[Episode]] = {}
        self.fallback_map: dict[tuple[str, str], Anime] = {}
        self.last_stream: Optional[Stream] = None
        self.last_provider: Optional[str] = None
        self.last_subtitle: Optional[SubtitleTrack] = None
        self.subtitle_preference = getattr(args, "sub_lang", None) or "auto"

    def _search_anime(self, query: str) -> Anime:
        status(f"Searching for {sty(query, C.BOLD)}")
        try:
            results = self.providers.search(query, self.args.provider)
        except ProviderError as exc:
            fail(str(exc))
        if not results:
            fail("No results found.")
        rows, mapping = format_anime_rows(results)
        if self.args.select_nth:
            idx = self.args.select_nth - 1
            if not (0 <= idx < len(results)):
                fail("--select-nth is outside the result list.")
            chosen = results[idx]
        else:
            picked = self.menu.choose(rows, "Anime › ")
            if not picked:
                raise SystemExit(0)
            if picked[0] not in mapping:
                fail("Selection did not match any result.")
            chosen = mapping[picked[0]]
        provider = self.providers.get(chosen.provider)
        ok(f"Selected {chosen.title}  [{provider.display_name}]")
        return chosen

    def _from_history(self) -> tuple[Anime, str]:
        entries = self.history.load()
        if not entries:
            fail("History is empty.")
        rows = [
            f"{e.title}  {sty('•', C.DIM)}  last watched {e.episode}  {sty('• ' + e.provider, C.DIM)}"
            for e in entries
        ]
        mapping = dict(zip(rows, entries))
        picked = self.menu.choose(rows, "Continue › ")
        if not picked:
            raise SystemExit(0)
        if picked[0] in mapping:
            entry = mapping[picked[0]]
        else:
            # fzf --ansi strips ANSI codes from its output, so fall back
            # to an ANSI-insensitive match before giving up.
            stripped_rows = [_strip_ansi(r) for r in rows]
            needle = _strip_ansi(picked[0])
            if needle in stripped_rows:
                entry = entries[stripped_rows.index(needle)]
            else:
                fail("History selection did not match any entry.")
                raise SystemExit(1)
        return Anime(entry.provider_id, entry.title, entry.provider), entry.episode

    @staticmethod
    def _match_score(left: str, right: str) -> float:
        a, b = _normalize_title(left), _normalize_title(right)
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        return SequenceMatcher(None, a, b).ratio()

    def _choose_fallback_candidate(self, original: Anime, candidates: Sequence[Anime]) -> Optional[Anime]:
        if not candidates:
            return None
        normalized = _normalize_title(original.title)
        exact = [c for c in candidates if _normalize_title(c.title) == normalized]
        if len(exact) == 1:
            return exact[0]

        ranked = sorted(
            ((self._match_score(original.title, c.title), c) for c in candidates),
            key=lambda item: item[0],
            reverse=True,
        )
        if ranked:
            best_score = ranked[0][0]
            second = ranked[1][0] if len(ranked) > 1 else 0.0
            if best_score >= 0.94 and best_score - second >= 0.06:
                return ranked[0][1]

        # Ambiguous matches are shown to the user instead of silently guessing.
        shortlist = [c for score, c in ranked[:8] if score >= 0.50] or [c for _, c in ranked[:8]]
        if not shortlist:
            return None
        rows = [f"{c.title}  {sty('• ' + self.providers.get(c.provider).display_name, C.DIM)}" for c in shortlist]
        mapping = dict(zip(rows, shortlist))
        chosen = self.menu.choose(
            rows,
            "Fallback › ",
            compact=len(rows) <= 8,
            header=f"Primary source failed. Match {original.title} on a backup provider:",
        )
        if not chosen:
            return None
        picked_candidate = mapping.get(chosen[0])
        if picked_candidate is not None:
            return picked_candidate
        # fzf --ansi strips ANSI codes from its output.
        needle = _strip_ansi(chosen[0])
        for row, candidate in mapping.items():
            if _strip_ansi(row) == needle:
                return candidate
        return None

    def _find_fallback_anime(self, anime: Anime) -> Optional[Anime]:
        cached = self.fallback_map.get((anime.provider, anime.provider_id))
        if cached:
            return cached
        if self.args.provider != "auto":
            return None
        for name in self.providers.fallback_names(anime.provider):
            provider = self.providers.get(name)
            status(f"Trying backup provider {provider.display_name}")
            try:
                candidates = provider.search(anime.title)
            except ProviderError as exc:
                warn(f"{provider.display_name} search failed: {exc}")
                continue
            chosen = self._choose_fallback_candidate(anime, candidates)
            if chosen:
                self.fallback_map[(anime.provider, anime.provider_id)] = chosen
                ok(f"Matched backup: {chosen.title}  [{provider.display_name}]")
                return chosen
        return None

    def _episodes(self, anime: Anime) -> list[Episode]:
        key = (anime.provider, anime.provider_id)
        if key not in self.episode_cache:
            self.episode_cache[key] = self.providers.episodes(anime)
        return self.episode_cache[key]

    def _episodes_with_fallback(self, anime: Anime) -> tuple[Anime, list[Episode]]:
        try:
            return anime, self._episodes(anime)
        except ProviderError as exc:
            if self.args.provider != "auto":
                raise
            warn(f"{self.providers.get(anime.provider).display_name} episode list failed: {exc}")
            fallback = self._find_fallback_anime(anime)
            if not fallback:
                raise ProviderUnavailable(f"No backup provider could match {anime.title}.") from exc
            return fallback, self._episodes(fallback)

    def _pick_episodes(
        self, anime: Anime, continue_after: Optional[str]
    ) -> tuple[Anime, list[Episode], list[Episode]]:
        status("Loading episodes")
        try:
            anime, episodes = self._episodes_with_fallback(anime)
        except ProviderError as exc:
            fail(str(exc))
        if not episodes:
            fail("No episodes were found.")

        if continue_after is not None:
            idx = episode_index(episodes, continue_after)
            if idx is None or idx + 1 >= len(episodes):
                fail("No unwatched episode is available in history.")
            return anime, episodes, [episodes[idx + 1]]

        if self.args.episode:
            selected = parse_episode_spec(self.args.episode, episodes)
            if not selected:
                fail(f"Invalid episode/range: {self.args.episode}")
            return anime, episodes, selected

        rows, mapping = format_episode_rows(episodes)
        picked = self.menu.choose(rows, "Episode › ", multi=True)
        selected = [mapping[row] for row in picked if row in mapping]
        if not selected:
            raise SystemExit(0)
        return anime, episodes, selected

    def _search_another_anime(self) -> Optional[tuple[Anime, list[Episode], Episode]]:
        """Search/select a new title without ending the current playback session."""
        clear_screen()
        banner("Search another anime")
        print()
        try:
            query = input(sty("  Search › ", C.BOLD, C.CYAN)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not query:
            return None

        status(f"Searching for {sty(query, C.BOLD)}")
        try:
            results = self.providers.search(query, self.args.provider)
        except ProviderError as exc:
            warn(str(exc))
            return None
        if not results:
            warn("No results found.")
            return None

        rows, mapping = format_anime_rows(results)
        picked = self.menu.choose(rows, "Anime › ")
        if not picked:
            return None
        chosen = mapping.get(picked[0])
        if chosen is None:
            needle = _strip_ansi(picked[0])
            chosen = next(
                (item for row, item in mapping.items() if _strip_ansi(row) == needle),
                None,
            )
        if chosen is None:
            warn("Selection did not match any result.")
            return None

        provider = self.providers.get(chosen.provider)
        ok(f"Selected {chosen.title}  [{provider.display_name}]")
        status("Loading episodes")
        try:
            chosen, episodes = self._episodes_with_fallback(chosen)
        except ProviderError as exc:
            warn(str(exc))
            return None
        if not episodes:
            warn("No episodes were found.")
            return None

        episode_rows, episode_mapping = format_episode_rows(episodes)
        episode_pick = self.menu.choose(episode_rows, "Episode › ")
        if not episode_pick:
            return None
        episode = episode_mapping.get(episode_pick[0])
        if episode is None:
            needle = _strip_ansi(episode_pick[0])
            episode = next(
                (item for row, item in episode_mapping.items() if _strip_ansi(row) == needle),
                None,
            )
        if episode is None:
            warn("Episode selection did not match any entry.")
            return None
        return chosen, episodes, episode

    def _clear_detached_session(self) -> None:
        store = getattr(self, "session_store", None)
        if store is not None:
            store.clear()

    def _save_detached_session(
        self,
        anime: Anime,
        current: Episode,
        quality: str,
    ) -> bool:
        playback = self.playback
        store = getattr(self, "session_store", None)
        if (
            playback is None
            or store is None
            or playback._is_android()
            or not playback._is_mpv()
            or playback.ipc_path is None
            or not playback.active()
        ):
            return False
        session = DetachedSession(
            socket=str(playback.ipc_path),
            player=playback.player,
            provider=anime.provider,
            provider_id=anime.provider_id,
            title=anime.title,
            episode=current.number,
            quality=self.last_stream.quality if self.last_stream else quality,
            mode=self.args.mode,
            source_provider=self.last_provider or anime.provider,
            subtitle_preference=self.subtitle_preference,
        )
        store.save(session)
        return True

    def _resume_detached_session(self, session: DetachedSession) -> int:
        assert self.playback is not None
        if not self.playback.adopt_detached_session(session):
            self._clear_detached_session()
            warn("The saved detached mpv session is no longer running.")
            return 1

        self.args.mode = session.mode
        self.subtitle_preference = session.subtitle_preference or "auto"
        anime = Anime(session.provider_id, session.title, session.provider)
        try:
            anime, episodes = self._episodes_with_fallback(anime)
        except ProviderError as exc:
            warn(f"Could not refresh episode metadata while reattaching: {exc}")
            episodes = [Episode(session.episode, session.episode)]

        idx = episode_index(episodes, session.episode)
        if idx is None:
            current = Episode(session.episode, session.episode)
            episodes = [current]
        else:
            current = episodes[idx]

        current_path = self.playback.current_path() or ""
        self.last_stream = Stream(session.quality or "auto", current_path)
        self.last_provider = session.source_provider or anime.provider
        self.last_subtitle = None
        ok(f"Reattached to {anime.title} Episode {current.number}.")
        self._interactive_loop(anime, episodes, current, session.quality or "best")
        return 0

    def _maybe_resume_detached_session(self, *, force: bool = False) -> Optional[int]:
        store = getattr(self, "session_store", None)
        if store is None or self.playback is None:
            return 1 if force else None
        session = store.load()
        if session is None:
            if force:
                warn("No detached ani-py mpv session was found.")
                return 1
            return None

        if not self.playback.adopt_detached_session(session):
            store.clear()
            if force:
                warn("The saved detached mpv session is no longer running.")
                return 1
            return None

        if force:
            return self._resume_detached_session(session)

        choice = self.menu.choose(
            ["Reattach controls", "Stop playback and search", "Exit"],
            "Detached › ",
            compact=True,
            header=f"Detached playback found: {session.title} • Episode {session.episode}",
        )
        if not choice or choice[0] == "Exit":
            self.playback.detach()
            return 0
        if choice[0] == "Reattach controls":
            return self._resume_detached_session(session)
        if choice[0] == "Stop playback and search":
            self.playback.stop()
            store.clear()
            self.playback = Playback(self.args)
            return None
        self.playback.detach()
        return 0

    def _resolve_on(self, anime: Anime, number: str) -> StreamBundle:
        episodes = self._episodes(anime)
        idx = episode_index(episodes, number)
        if idx is None:
            raise EpisodeNotFound(
                f"{self.providers.get(anime.provider).display_name} does not have episode {number}."
            )
        return self.providers.resolve(anime, episodes[idx], self.args.mode)

    def _bundle(self, anime: Anime, episode: Episode) -> StreamBundle:
        key = (anime.provider, anime.provider_id, episode.number, self.args.mode)
        if key in self.bundle_cache:
            return self.bundle_cache[key]

        status(f"Resolving Episode {episode.number} [{self.args.mode}] via {self.providers.get(anime.provider).display_name}")

        mapped = self.fallback_map.get((anime.provider, anime.provider_id))
        if mapped:
            try:
                bundle = self._resolve_on(mapped, episode.number)
                self.bundle_cache[key] = bundle
                return bundle
            except ProviderError as exc:
                warn(f"Cached backup {self.providers.get(mapped.provider).display_name} failed: {exc}")
                self.fallback_map.pop((anime.provider, anime.provider_id), None)

        try:
            bundle = self.providers.resolve(anime, episode, self.args.mode)
            self.bundle_cache[key] = bundle
            return bundle
        except ProviderError as primary_exc:
            if self.args.provider != "auto":
                fail(str(primary_exc))
            warn(f"{self.providers.get(anime.provider).display_name} stream failed: {primary_exc}")

        # Automatic failover is conservative: exact/high-confidence matches are
        # automatic; ambiguous title matches are presented to the user.
        fallback = self._find_fallback_anime(anime)
        if not fallback:
            fail(f"No backup source could resolve {anime.title} episode {episode.number}.")
        try:
            bundle = self._resolve_on(fallback, episode.number)
        except ProviderError as backup_exc:
            fail(f"Backup provider failed: {backup_exc}")
        self.bundle_cache[key] = bundle
        return bundle

    def _play_episode(
        self,
        anime: Anime,
        episode: Episode,
        quality: str,
        *,
        replace: bool = False,
        foreground: bool = False,
        keep_open: bool = True,
    ) -> int:
        bundle = self._bundle(anime, episode)
        stream = choose_quality(bundle.streams, quality)
        subtitle_track = choose_subtitle_track(bundle, self.subtitle_preference)
        requested_subtitle = (self.subtitle_preference or "auto").strip().casefold()
        if (
            bundle.subtitle_tracks()
            and subtitle_track is None
            and requested_subtitle not in {"", "auto", "default", "off", "none", "no", "false", "0"}
        ):
            warn(
                f"Subtitle {self.subtitle_preference!r} is not available for this episode; "
                "continuing without an external subtitle."
            )
        self.last_stream = stream
        self.last_provider = bundle.provider
        self.last_subtitle = subtitle_track
        clear_screen()
        banner(f"{anime.title}  •  Episode {episode.number}  •  {stream.quality}")
        print()
        print(f"  {sty('Title', C.DIM)}    {anime.title}")
        print(f"  {sty('Episode', C.DIM)}  {episode.number}")
        print(f"  {sty('Mode', C.DIM)}     {self.args.mode.upper()}")
        print(f"  {sty('Quality', C.DIM)}  {stream.quality}")
        print(f"  {sty('Source', C.DIM)}   {self.providers.get(bundle.provider).display_name}")
        print(f"  {sty('Player', C.DIM)}   {Path(self.playback.player).name if self.playback.player != 'download' else 'download'}")
        subtitle_name = subtitle_track.name if subtitle_track else "off"
        if subtitle_track and subtitle_track.language:
            subtitle_name += f" [{subtitle_track.language}]"
        print(f"  {sty('Subtitle', C.DIM)} {subtitle_name}")
        print()
        assert self.playback is not None
        play_fn = self.playback.replace if replace else self.playback.play
        play_kwargs = dict(
            title=f"{anime.title} Episode {episode.number}",
            subtitle=subtitle_track.url if subtitle_track else None,
            referer=bundle.referer,
            mal_id=bundle.mal_id,
            episode=episode.number,
            subtitle_language=subtitle_track.language if subtitle_track else None,
            subtitle_label=subtitle_track.label if subtitle_track else None,
        )
        if replace:
            rc = play_fn(stream, **play_kwargs)
        else:
            rc = play_fn(stream, foreground=foreground, keep_open=keep_open, **play_kwargs)
        if rc == 0:
            self.history.update(anime, episode.number)
        return rc

    def _interactive_loop(self, anime: Anime, episodes: list[Episode], current: Episode, quality: str) -> None:
        if self.args.download or self.args.exit_after_play:
            return
        assert self.playback is not None
        while True:
            options = [
                "Next episode",
                "Previous episode",
                "Replay",
                "Choose episode",
                "Search another anime",
                "Change quality",
                "Change subtitle",
                "Detach & exit",
                "Stop & quit",
            ]
            state = "playing" if self.playback.active() else "player closed"
            actual_quality = self.last_stream.quality if self.last_stream else quality
            source = self.providers.get(self.last_provider).display_name if self.last_provider else anime.provider
            header = (
                f"{anime.title}\n"
                f"Episode {current.number}  •  {self.args.mode.upper()}  •  {actual_quality}  •  {source}  •  {state}"
            )
            picked = self.menu.choose(
                options,
                f"Episode {current.number} › ",
                compact=True,
                header=header,
            )
            if not picked:
                return
            action = picked[0]
            idx = episode_index(episodes, current.number)
            if idx is None:
                return
            if action == "Stop & quit":
                self._clear_detached_session()
                self.playback.stop()
                return
            if action == "Detach & exit":
                saved = self._save_detached_session(anime, current, quality)
                self.playback.detach()
                if saved:
                    ok("Detached; run ani-py --attach to return to these controls.")
                else:
                    ok("Detached; playback continues in the background.")
                return
            if action == "Next episode":
                if idx + 1 >= len(episodes):
                    warn("Already at the last episode.")
                    continue
                current = episodes[idx + 1]
                self._play_episode(anime, current, quality, replace=True)
            elif action == "Previous episode":
                if idx == 0:
                    warn("Already at the first episode.")
                    continue
                current = episodes[idx - 1]
                self._play_episode(anime, current, quality, replace=True)
            elif action == "Replay":
                if not self.playback.replay():
                    self._play_episode(anime, current, quality, replace=True)
            elif action == "Choose episode":
                rows, mapping = format_episode_rows(episodes)
                chosen = self.menu.choose(rows, "Episode › ")
                if chosen:
                    picked_episode = mapping.get(chosen[0])
                    if picked_episode is None:
                        warn("Episode selection did not match any entry.")
                    else:
                        current = picked_episode
                        self._play_episode(anime, current, quality, replace=True)
            elif action == "Search another anime":
                target = self._search_another_anime()
                if target is None:
                    continue
                next_anime, next_episodes, next_episode = target
                try:
                    rc = self._play_episode(next_anime, next_episode, quality, replace=True)
                except SystemExit:
                    # Provider resolution errors should not tear down the
                    # current session while the existing player is still alive.
                    continue
                if rc == 0:
                    anime, episodes, current = next_anime, next_episodes, next_episode
            elif action == "Change quality":
                bundle = self._bundle(anime, current)
                qrows = list(dict.fromkeys(s.quality for s in bundle.streams))
                chosen = self.menu.choose(
                    qrows,
                    "Quality › ",
                    compact=True,
                    header=f"{anime.title} • Episode {current.number} • {self.providers.get(bundle.provider).display_name}",
                )
                if chosen:
                    quality = chosen[0]
                    self._play_episode(anime, current, quality, replace=True)
            elif action == "Change subtitle":
                bundle = self._bundle(anime, current)
                tracks = bundle.subtitle_tracks()
                if not tracks:
                    warn("This source does not expose switchable subtitle tracks.")
                    continue
                current_subtitle = choose_subtitle_track(bundle, self.subtitle_preference)
                rows, mapping = subtitle_menu_rows(tracks, current_subtitle)
                chosen = self.menu.choose(
                    rows,
                    "Subtitle › ",
                    compact=len(rows) <= 12,
                    header=f"{anime.title} • Episode {current.number} • {self.providers.get(bundle.provider).display_name}",
                )
                if not chosen:
                    continue
                selected = mapping.get(chosen[0])
                if chosen[0] not in mapping:
                    needle = _strip_ansi(chosen[0])
                    selected = next(
                        (track for row, track in mapping.items() if _strip_ansi(row) == needle),
                        None,
                    )
                if selected is None and _strip_ansi(chosen[0]) != "Off":
                    warn("Subtitle selection did not match any track.")
                    continue
                self.subtitle_preference = (
                    "off"
                    if selected is None
                    else f"label:{selected.label}"
                    if selected.label
                    else selected.language or "auto"
                )
                self.last_subtitle = selected
                if self.playback.set_subtitle(selected):
                    ok(f"Subtitle: {selected.name if selected else 'off'}")
                else:
                    self._play_episode(anime, current, quality, replace=True)

    @staticmethod
    def _auto_next_queue(
        episodes: Sequence[Episode], selected: Sequence[Episode]
    ) -> list[Episode]:
        """Build the provider-independent playback queue.

        An explicit multi-episode selection/range is respected exactly. A
        single selected episode means "start here and continue" through the
        already-loaded episode list.
        """
        if len(selected) != 1:
            return list(selected)
        idx = episode_index(episodes, selected[0].number)
        return list(episodes[idx:]) if idx is not None else list(selected)

    def _run_auto_next(
        self,
        anime: Anime,
        episodes: list[Episode],
        selected: list[Episode],
        quality: str,
    ) -> int:
        assert self.playback is not None
        if not self.playback.auto_next_supported():
            fail(
                "--auto-next requires desktop mpv with private IPC. "
                "VLC, IINA, custom players, Windows named-pipe IPC, and Android "
                "intent players do not expose a reliable natural-EOF signal to ani-py."
            )

        queue = self._auto_next_queue(episodes, selected)
        if not queue:
            return 0

        status(
            f"Auto-next: {len(queue)} episode{'s' if len(queue) != 1 else ''} "
            "(provider-independent, desktop mpv)"
        )
        try:
            for index, episode in enumerate(queue):
                rc = self._play_episode(
                    anime,
                    episode,
                    quality,
                    replace=index > 0,
                    keep_open=True,
                )
                if rc != 0:
                    self.playback.stop()
                    return rc

                completion = self.playback.wait_for_completion()
                if completion != "eof":
                    self.playback.stop()
                    return 0

                if index + 1 >= len(queue):
                    self.playback.stop()
                    ok("Auto-next queue finished.")
                    return 0

                next_episode = queue[index + 1]
                ok(
                    f"Episode {episode.number} finished; "
                    f"starting Episode {next_episode.number}."
                )
        except (KeyboardInterrupt, SystemExit):
            self.playback.stop()
            raise
        return 0

    def run(self) -> int:
        if self.args.clear_history:
            self.history.clear()
            ok("History cleared.")
            return 0

        if self.args.list_providers:
            names = list(dict.fromkeys(list(self.providers.order) + list(self.providers.providers)))
            for name in names:
                p = self.providers.get(name)
                caps = []
                if p.capabilities.sub:
                    caps.append("sub")
                if p.capabilities.dub:
                    caps.append("dub")
                if p.capabilities.subtitles:
                    caps.append("subs")
                if p.capabilities.mal_id:
                    caps.append("MAL")
                tag = " [experimental]" if p.experimental else ""
                print(f"{p.name:10} {p.display_name:12} {', '.join(caps)}{tag}")
            return 0

        if getattr(self.args, "attach", False):
            result = self._maybe_resume_detached_session(force=True)
            return 1 if result is None else result

        if (
            not self.args.continue_watching
            and not self.args.download
            and not self.args.query
        ):
            result = self._maybe_resume_detached_session(force=False)
            if result is not None:
                return result

        if self.args.continue_watching:
            anime, continue_after = self._from_history()
        else:
            query = " ".join(self.args.query).strip()
            if not query:
                clear_screen()
                banner("Search • select • watch")
                print()
                try:
                    query = input(sty("  Search › ", C.BOLD, C.CYAN)).strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 130
            if not query:
                return 0
            anime = self._search_anime(query)
            continue_after = None

        anime, episodes, selected = self._pick_episodes(anime, continue_after)

        if getattr(self.args, "auto_next", False):
            if self.args.download:
                fail("--auto-next cannot be combined with --download.")
            if getattr(self.args, "attach", False):
                fail("--auto-next cannot be combined with --attach.")
            if self.args.no_detach:
                fail("--auto-next cannot be combined with --no-detach.")
            if self.args.exit_after_play:
                fail("--auto-next cannot be combined with --exit-after-play.")
            return self._run_auto_next(anime, episodes, selected, self.args.quality)

        rc = 0
        queued_playback = len(selected) > 1 and not self.args.download
        for ep in selected:
            rc = self._play_episode(
                anime,
                ep,
                self.args.quality,
                foreground=queued_playback,
                keep_open=not queued_playback,
            )
            if rc != 0:
                return rc

        if queued_playback or self.args.download or self.args.exit_after_play:
            return rc
        self._interactive_loop(anime, episodes, selected[-1], self.args.quality)
        return rc



# ---------- CLI ----------

_VERSION_RE = re.compile(r"^VERSION\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)


def _declared_version(payload: bytes) -> Optional[str]:
    match = _VERSION_RE.search(payload.decode("utf-8", "replace"))
    return match.group(1) if match else None


def _version_key(version: str) -> Optional[tuple]:
    """Sortable key for a calendar version, or None if it is not numeric.

    Only the leading digits of each dot-separated component count, so a copy
    that still carries the legacy `0.5.2-rc11` semver parses as `(0, 5, 2)` and
    sorts below any CalVer date. That keeps `--update`'s downgrade guard working
    during the transition instead of disabling itself on every old install.
    A genuinely unparseable version returns None, and the guard then lets the
    content comparison decide rather than blocking an update on a bad string.
    """
    parts: list[int] = []
    for component in version.split("."):
        digits = re.match(r"\d+", component.strip())
        if not digits:
            return None
        parts.append(int(digits.group()))
    return tuple(parts) or None


def run_update(http: Optional[HttpClient] = None, target: Optional[Path] = None) -> int:
    """Replace this script with the latest checksummed release asset and exit."""
    path = target if target is not None else Path(__file__).resolve()
    if http is None:
        http = HttpClient()
    try:
        remote = http.get_bytes(UPDATE_URL, timeout=30)
        checksums = http.get(UPDATE_CHECKSUM_URL, timeout=30)
    except AniPyError as exc:
        print(f"{APP_NAME}: could not fetch the latest release: {exc}")
        return 1

    expected_digest: Optional[str] = None
    for line in checksums.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == "ani-py":
            candidate = parts[0].lower()
            if re.fullmatch(r"[0-9a-f]{64}", candidate):
                expected_digest = candidate
                break
    if expected_digest is None:
        print(f"{APP_NAME}: latest release is missing a valid ani-py checksum.")
        return 1

    digest = hashlib.sha256(remote).hexdigest()
    if not secrets.compare_digest(digest, expected_digest):
        print(f"{APP_NAME}: latest release checksum verification failed; leaving {path} untouched.")
        return 1

    local = path.read_bytes()
    if remote == local:
        print(f"{APP_NAME} {VERSION} is up to date with the latest release.")
        return 0

    # "Newer" is decided by VERSION rather than content so a local development
    # build ahead of the latest release is never silently rolled back.
    remote_version = _declared_version(remote)
    local_version = _declared_version(local)
    remote_key = _version_key(remote_version) if remote_version else None
    local_key = _version_key(local_version) if local_version else None
    if remote_key and local_key and remote_key < local_key:
        print(
            f"{APP_NAME}: latest release is older than this copy "
            f"({remote_version} < {local_version}); leaving {path} untouched. "
            "Re-run install.sh to force the latest published release if that is what you want."
        )
        return 1

    # A 200 HTML error page must never overwrite a working install.
    if not remote.startswith(b"#!"):
        print(
            f"{APP_NAME}: the copy fetched from the latest release does not look like a script; "
            "leaving the installed copy untouched."
        )
        return 1

    handle, staged = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".new")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(remote)
        os.chmod(staged, 0o755)
        os.replace(staged, path)
    except PermissionError:
        print(
            f"{APP_NAME}: no permission to write {path}. Try: sudo {APP_NAME} --update"
        )
        return 1
    except OSError as exc:
        print(f"{APP_NAME}: could not write {path}: {exc}")
        return 1
    finally:
        if os.path.exists(staged):
            os.unlink(staged)

    print(
        f"updated {path} from the latest release "
        f"({len(local)} -> {len(remote)} bytes, sha {digest[:7]})"
    )
    print("re-run the installer with --deps if you are missing external tools")
    return 0

def build_parser() -> argparse.ArgumentParser:
    formatter = argparse.RawDescriptionHelpFormatter
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        formatter_class=formatter,
        description="A polished, standalone Python anime CLI (stdlib only).",
        epilog=textwrap.dedent(
            """
            examples:
              ani-py "frieren"
              ani-py -q 1080 -e 3 "dandadan"
              ani-py --dub -e 1-4 "one piece"
              ani-py -c
              ani-py -d -e 1-12 "pluto"
              ani-py --attach
              ani-py --update

            environment:
              ANI_PY_PLAYER          preferred player (mpv, vlc, iina, auto, or executable)
              ANI_PY_PLAYER_FLAGS    extra player flags
              ANI_PY_IPC_SOCKET      override private mpv IPC socket (advanced)
              ANI_PY_MENU            fzf, rofi, dmenu, or fallback terminal UI
              ANI_PY_MENU_FLAGS      extra menu flags
              ANI_PY_DOWNLOAD_DIR    download destination
              ANI_PY_SUB_LANG        preferred subtitle language/label, auto, or off
              ANI_PY_AUTO_NEXT       1 to auto-play following episodes (desktop mpv IPC)
              ANI_PY_HIST_DIR        state directory root
              ANI_PY_CURL            curl/curl-impersonate executable
              ANI_PY_PROVIDER        auto, hianime, or anilight
              ANI_PY_PROVIDER_ORDER  failover order (default hianime; backups are opt-in)
              ANI_PY_ANILIGHT_URL     override AniLight site base URL
              ANI_PY_ANILIGHT_API_URL override AniLight API base URL
              NO_COLOR               disable ANSI color
            """
        ),
    )
    parser.add_argument("query", nargs="*", help="anime search query")
    parser.add_argument("-c", "--continue", dest="continue_watching", action="store_true", help="continue from history")
    parser.add_argument("--attach", action="store_true", help="reattach controls to the last detached desktop mpv session")
    parser.add_argument("-d", "--download", action="store_true", help="download instead of play")
    parser.add_argument("-D", "--delete-history", dest="clear_history", action="store_true", help="clear watch history")
    parser.add_argument("-e", "--episode", "-r", "--range", dest="episode", help="episode or range, e.g. 4 or 4-9")
    parser.add_argument("-q", "--quality", default=os.getenv("ANI_PY_QUALITY", "best"), help="best, worst, 360, 480, 720, 1080")
    parser.add_argument("--sub-lang", default=os.getenv("ANI_PY_SUB_LANG", "auto"), help="subtitle language/label for playback/downloads, or auto/off")
    parser.add_argument("-S", "--select-nth", type=int, help="select search result by index")
    parser.add_argument(
        "--provider",
        choices=["auto", "hianime", "anilight"],
        default=os.getenv("ANI_PY_PROVIDER", "auto"),
        help="source provider (default: auto with failover)",
    )
    parser.add_argument(
        "--provider-order",
        default=os.getenv("ANI_PY_PROVIDER_ORDER", "hianime"),
        help="comma-separated auto-failover order (default: hianime; AniLight is opt-in)",
    )
    parser.add_argument("--list-providers", action="store_true", help="show configured providers and exit")
    parser.add_argument("--dub", dest="mode", action="store_const", const="dub", help="use dubbed stream")
    parser.add_argument("--sub", dest="mode", action="store_const", const="sub", help="use subtitled stream")
    parser.set_defaults(mode=os.getenv("ANI_PY_MODE", "sub"))
    parser.add_argument(
        "-p",
        "--player",
        default=os.getenv("ANI_PY_PLAYER"),
        help="player: mpv, vlc, iina, auto, or a custom executable (Android: auto, vlc, mpv)",
    )
    parser.add_argument("--player-flag", action="append", default=[], help="extra player argument (repeatable; use --player-flag='--flag' for dash-flags)")
    parser.add_argument("--ipc-socket", help="mpv IPC socket path (default: private per ani-py process)")
    parser.add_argument("--menu", choices=["fzf", "rofi", "dmenu"], help="interactive menu frontend")
    parser.add_argument("--menu-flags", default="", help="extra menu frontend flags (use --menu-flags='--flag' for dash-flags)")
    parser.add_argument("--skip", action="store_true", default=os.getenv("ANI_PY_SKIP_INTRO", "0") == "1", help="use ani-skip with mpv")
    parser.add_argument(
        "--auto-next",
        action="store_true",
        default=os.getenv("ANI_PY_AUTO_NEXT", "0") == "1",
        help="auto-play following episodes after natural EOF (desktop mpv IPC only)",
    )
    parser.add_argument("--no-detach", action="store_true", default=os.getenv("ANI_PY_NO_DETACH", "0") == "1", help="keep player attached")
    parser.add_argument("--exit-after-play", action="store_true", default=os.getenv("ANI_PY_EXIT_AFTER_PLAY", "0") == "1", help="exit after player closes/launches")
    parser.add_argument(
        "--android-debug",
        action="store_true",
        default=os.getenv("ANI_PY_ANDROID_DEBUG", "0") == "1",
        help="print Android playback/relay diagnostics to stderr (harmless off Android)",
    )
    parser.add_argument(
        "-U",
        "--update",
        action="store_true",
        help="replace this script with the latest verified release and exit",
    )
    parser.add_argument("--_android-relay-config", help=argparse.SUPPRESS)
    parser.add_argument("-V", "--version", action="version", version=f"{APP_NAME} {VERSION}")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.update:
        return run_update()
    if args._android_relay_config:
        return run_android_relay(args._android_relay_config)
    try:
        return App(args).run()
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
