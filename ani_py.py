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
from urllib.parse import quote_plus, urlencode, urljoin, urlsplit

APP_NAME = "ani-py"
VERSION = "0.5.2-rc8"
BASE_URL = "https://hianime.at"
ANIMEKAI_BASE_URL = ""  # no trusted default; set ANI_PY_ANIMEKAI_URL explicitly
KUHI_BASE_URL = "https://anime-scraper-v2.vercel.app"
ANIMEKAI_ENC_URL = "https://enc-dec.app/api/enc-kai"
ANIMEKAI_DEC_KAI_URL = "https://enc-dec.app/api/dec-kai"
ANIMEKAI_DEC_MEGA_URL = "https://enc-dec.app/api/dec-mega"
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


@dataclasses.dataclass
class StreamBundle:
    streams: list[Stream]
    subtitle: Optional[str]
    referer: str
    mal_id: Optional[str]
    provider: str = "unknown"
    subtitle_language: Optional[str] = None
    subtitle_label: Optional[str] = None


@dataclasses.dataclass
class HistoryEntry:
    episode: str
    provider: str
    provider_id: str
    title: str

    @property
    def anime_slug(self) -> str:
        return self.provider_id


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
    def _pick_subtitle_info(payload: object) -> tuple[Optional[str], Optional[str], Optional[str]]:
        if isinstance(payload, dict):
            subtitles = payload.get("subtitles")
            if isinstance(subtitles, list):
                items = [x for x in subtitles if isinstance(x, dict)]
                preferred = [x for x in items if x.get("default")]
                for item in preferred + [x for x in items if x not in preferred]:
                    src = item.get("src") or item.get("file") or item.get("url")
                    if not isinstance(src, str) or not src:
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
                    return src, language, label
            for value in payload.values():
                hit = HianimeProvider._pick_subtitle_info(value)
                if hit[0]:
                    return hit
        elif isinstance(payload, list):
            for value in payload:
                hit = HianimeProvider._pick_subtitle_info(value)
                if hit[0]:
                    return hit
        return None, None, None

    @staticmethod
    def _pick_subtitle(payload: object) -> Optional[str]:
        return HianimeProvider._pick_subtitle_info(payload)[0]

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

        candidates = re.findall(r'<[^>]*class="[^"]*server-item[^"]*"[^>]*>', server_page, re.I)
        encoded = None
        for tag in candidates:
            attrs = _attrs(tag)
            if attrs.get("data-type") == mode and attrs.get("data-server-name", "").lower() == "zokoanime":
                encoded = attrs.get("data-hash")
                if encoded:
                    break
        if not encoded:
            m = re.search(
                rf'data-type="{re.escape(mode)}".*?data-server-name="ZokoAnime".*?data-hash="([^"]+)"',
                server_page,
                re.I | re.S,
            )
            encoded = m.group(1) if m else None
        embed_url = self._decode_embed_hash(encoded or "")
        if not embed_url:
            raise StreamNotFound(f"HiAnime has no {mode} source for episode {episode.number}.")

        parts = urlsplit(embed_url)
        referer = f"{parts.scheme}://{parts.netloc}/"
        mal_match = re.search(r"/mal/(\d+)/", embed_url)
        mal_id = mal_match.group(1) if mal_match else None
        try:
            embed_page = self.http.get(embed_url)
        except HttpError as exc:
            raise ProviderUnavailable(f"HiAnime embed host failed: {exc}") from exc
        blob_match = re.search(r'window\.__P\s*=\s*"([^"]+)"', embed_page)
        if not blob_match:
            raise ProviderChanged("HiAnime player payload was not found; provider markup may have changed.")
        try:
            payload = self._deobfuscate(blob_match.group(1))
        except ValueError as exc:
            raise ProviderChanged(str(exc)) from exc

        master_url = self._pick_source_url(payload)
        if not master_url:
            raise StreamNotFound("HiAnime payload contained no HLS source.")
        subtitle, subtitle_language, subtitle_label = self._pick_subtitle_info(payload)
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
        )


# ---------- AnimeKai manual / experimental provider ----------

class AnimeKaiProvider(Provider):
    name = "animekai"
    display_name = "AnimeKai"
    capabilities = ProviderCapabilities(sub=True, dub=True, subtitles=True, qualities=True, mal_id=True)
    experimental = True

    def __init__(self, http: HttpClient) -> None:
        self.http = http
        self.base = os.getenv("ANI_PY_ANIMEKAI_URL", ANIMEKAI_BASE_URL).rstrip("/")
        self._info_cache: dict[str, tuple[str, Optional[str]]] = {}
        self._available: Optional[bool] = None

    def _require_base(self) -> str:
        if not self.base:
            raise ProviderUnavailable(
                "AnimeKai has no trusted default domain. Set ANI_PY_ANIMEKAI_URL "
                "to a compatible mirror you have verified."
            )
        return self.base

    def available(self) -> bool:
        """Probe the live endpoint before trusting this experimental provider.

        AnimeKai base domains churn and mirrors serve anti-bot or parking
        pages, so a configured name is not evidence the service is usable.
        Memoized per instance so automatic paths probe at most once.
        """
        if not self.base:
            return False
        if self._available is None:
            self._available = self._preflight()
        return self._available

    def _preflight(self) -> bool:
        url = f"{self.base}/ajax/anime/search?{urlencode({'keyword': 'naruto'})}"
        try:
            data = self.http.get_json(url, headers=self.ajax_headers, referer=self.base + "/", timeout=10)
            result = self._result_object(data, "preflight")
            fragment = result.get("html", "") if isinstance(result, dict) else result
            if not isinstance(fragment, str):
                return False
            # Challenge/park pages never parse as JSON; a live service
            # answers a popular title with result anchors.
            return not fragment or "<a" in fragment
        except (HttpError, ProviderChanged) as exc:
            warn(f"AnimeKai preflight failed ({exc}); treating it as unavailable.")
            return False

    @property
    def ajax_headers(self) -> dict[str, str]:
        return {"X-Requested-With": "XMLHttpRequest", "Referer": self.base + "/"}

    @staticmethod
    def _result_object(data: object, context: str) -> object:
        if not isinstance(data, dict):
            raise ProviderChanged(f"AnimeKai {context} response was not an object.")
        result = data.get("result")
        if result is None:
            raise ProviderChanged(f"AnimeKai {context} response did not contain result.")
        if isinstance(result, str):
            stripped = result.strip()
            if stripped.startswith(("{", "[")):
                try:
                    return json.loads(stripped)
                except json.JSONDecodeError:
                    pass
        return result

    def _encode(self, value: str) -> str:
        try:
            data = self.http.get_json(f"{ANIMEKAI_ENC_URL}?{urlencode({'text': value})}")
        except HttpError as exc:
            raise ProviderUnavailable(f"AnimeKai token helper is unavailable: {exc}") from exc
        result = self._result_object(data, "token")
        if not isinstance(result, str) or not result:
            raise ProviderChanged("AnimeKai token helper returned an invalid token.")
        return result

    def _decrypt_kai(self, value: str) -> dict:
        try:
            data = self.http.post_json(ANIMEKAI_DEC_KAI_URL, {"text": value})
        except HttpError as exc:
            raise ProviderUnavailable(f"AnimeKai decrypt helper is unavailable: {exc}") from exc
        result = self._result_object(data, "embed decrypt")
        if not isinstance(result, dict):
            raise ProviderChanged("AnimeKai embed decrypt returned an invalid payload.")
        return result

    def _decrypt_mega(self, value: str) -> dict:
        try:
            data = self.http.post_json(
                ANIMEKAI_DEC_MEGA_URL, {"text": value, "agent": USER_AGENT}
            )
        except HttpError as exc:
            raise ProviderUnavailable(f"AnimeKai media decrypt helper is unavailable: {exc}") from exc
        result = self._result_object(data, "media decrypt")
        if not isinstance(result, dict):
            raise ProviderChanged("AnimeKai media decrypt returned an invalid payload.")
        return result

    def search(self, query: str) -> list[Anime]:
        self._require_base()
        url = f"{self.base}/ajax/anime/search?{urlencode({'keyword': query})}"
        try:
            data = self.http.get_json(url, headers=self.ajax_headers, referer=self.base + "/")
        except HttpError as exc:
            raise ProviderUnavailable(f"AnimeKai search failed: {exc}") from exc
        result = self._result_object(data, "search")
        if isinstance(result, dict):
            fragment = result.get("html", "")
        else:
            fragment = result
        if not isinstance(fragment, str):
            raise ProviderChanged("AnimeKai search HTML was missing.")

        found: list[Anime] = []
        seen: set[str] = set()
        for match in re.finditer(r"<a\b([^>]*)>(.*?)</a>", fragment, re.I | re.S):
            attrs = _attrs(match.group(1))
            classes = attrs.get("class", "").split()
            href = attrs.get("href", "")
            if "aitem" not in classes or "/watch/" not in href:
                continue
            slug = href.split("/watch/", 1)[1].split("?", 1)[0].strip("/")
            title_match = re.search(r'<h6\b[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</h6>', match.group(2), re.I | re.S)
            title = _plain_text(title_match.group(1)) if title_match else ""
            if slug and title and slug not in seen:
                found.append(Anime(provider_id=slug, title=title, provider=self.name))
                seen.add(slug)
        return found

    def _anime_info(self, anime_id: str) -> tuple[str, Optional[str]]:
        self._require_base()
        cached = self._info_cache.get(anime_id)
        if cached:
            return cached
        try:
            page = self.http.get(f"{self.base}/watch/{anime_id}", referer=self.base + "/")
        except HttpError as exc:
            raise ProviderUnavailable(f"AnimeKai anime page failed: {exc}") from exc
        sync = re.search(r'<script\b[^>]*id=["\']syncData["\'][^>]*>(.*?)</script>', page, re.I | re.S)
        if not sync:
            raise ProviderChanged("AnimeKai syncData was not found on the anime page.")
        try:
            payload = json.loads(html.unescape(sync.group(1)).strip())
        except json.JSONDecodeError as exc:
            raise ProviderChanged("AnimeKai syncData was invalid JSON.") from exc
        internal_id = str(payload.get("anime_id") or "")
        if not internal_id:
            raise ProviderChanged("AnimeKai anime_id was missing from syncData.")
        mal = re.search(r'(?:myanimelist\.net/anime/|["\']mal["\']\s*[:=]\s*["\']?)(\d+)', page, re.I)
        value = (internal_id, mal.group(1) if mal else None)
        self._info_cache[anime_id] = value
        return value

    def episodes(self, anime: Anime | str) -> list[Episode]:
        anime_id = _provider_id(anime)
        internal_id, _ = self._anime_info(anime_id)
        token = self._encode(internal_id)
        url = f"{self.base}/ajax/episodes/list?{urlencode({'ani_id': internal_id, '_': token})}"
        try:
            data = self.http.get_json(url, headers=self.ajax_headers, referer=f"{self.base}/watch/{anime_id}")
        except HttpError as exc:
            raise ProviderUnavailable(f"AnimeKai episode list failed: {exc}") from exc
        fragment = self._result_object(data, "episodes")
        if not isinstance(fragment, str):
            raise ProviderChanged("AnimeKai episode list HTML was missing.")
        episodes: list[Episode] = []
        for match in re.finditer(r"<a\b([^>]*)>", fragment, re.I):
            attrs = _attrs(match.group(1))
            number = attrs.get("num", "")
            ep_token = attrs.get("token", "")
            if number and ep_token:
                episodes.append(Episode(episode_id=ep_token, number=number))
        if not episodes:
            raise EpisodeNotFound(f"AnimeKai returned no episodes for {anime_id}.")
        episodes.sort(key=lambda e: float(e.number) if re.fullmatch(r"\d+(?:\.\d+)?", e.number) else 10**9)
        return episodes

    @staticmethod
    def _server_groups(fragment: str) -> dict[str, list[dict[str, str]]]:
        groups: dict[str, list[dict[str, str]]] = {}
        current = "unknown"
        for match in re.finditer(r"<[^!/][^>]*>", fragment, re.I):
            attrs = _attrs(match.group(0))
            classes = attrs.get("class", "").split()
            if "server-items" in classes:
                current = attrs.get("data-id", "unknown").lower()
                groups.setdefault(current, [])
            elif "server" in classes and attrs.get("data-lid"):
                groups.setdefault(current, []).append({
                    "name": _plain_text(match.group(0)),
                    "link_id": attrs.get("data-lid", ""),
                    "server_id": attrs.get("data-sid", ""),
                })
        return groups

    @staticmethod
    def _mode_servers(groups: dict[str, list[dict[str, str]]], mode: str) -> list[dict[str, str]]:
        if mode == "dub":
            keys = [k for k in groups if "dub" in k]
        else:
            hard = [k for k in groups if k == "sub" or ("sub" in k and "soft" not in k and "dub" not in k)]
            soft = [k for k in groups if "soft" in k]
            keys = hard + soft
        out: list[dict[str, str]] = []
        for key in keys:
            out.extend(groups.get(key, []))
        return out

    @staticmethod
    def _quality(source: dict[str, object]) -> str:
        raw = str(source.get("label") or source.get("quality") or source.get("res") or "")
        match = re.search(r"(\d{3,4})", raw)
        if not match:
            match = re.search(r"(?:/|_|-)(\d{3,4})(?:p|/|_|-|\.)", str(source.get("file") or source.get("url") or ""), re.I)
        return match.group(1) + "p" if match else "auto"

    @staticmethod
    def _subtitle(tracks: object) -> Optional[str]:
        if not isinstance(tracks, list):
            return None
        choices: list[tuple[int, str]] = []
        for item in tracks:
            if not isinstance(item, dict):
                continue
            url = item.get("file") or item.get("url") or item.get("src")
            if not isinstance(url, str) or not url:
                continue
            kind = str(item.get("kind") or "").lower()
            label = str(item.get("label") or "").lower()
            if kind and kind not in {"captions", "subtitles", "subtitle"} and not url.endswith((".vtt", ".srt", ".ass")):
                continue
            score = 2 if item.get("default") else 1 if "english" in label or label in {"en", "eng"} else 0
            choices.append((score, url))
        return max(choices, default=(-1, ""))[1] or None

    def _resolve_link(self, anime_id: str, link_id: str) -> StreamBundle:
        encoded = self._encode(link_id)
        url = f"{self.base}/ajax/links/view?{urlencode({'id': link_id, '_': encoded})}"
        try:
            data = self.http.get_json(url, headers=self.ajax_headers, referer=f"{self.base}/watch/{anime_id}")
        except HttpError as exc:
            raise ProviderUnavailable(f"AnimeKai link lookup failed: {exc}") from exc
        encrypted = self._result_object(data, "link")
        if not isinstance(encrypted, str) or not encrypted:
            raise ProviderChanged("AnimeKai returned an invalid encrypted link.")
        embed = self._decrypt_kai(encrypted)
        embed_url = str(embed.get("url") or "")
        if not embed_url:
            raise ProviderChanged("AnimeKai decrypt payload had no embed URL.")

        fd, cookie_path = tempfile.mkstemp(prefix="ani-py-kai-", suffix=".cookies")
        os.close(fd)
        try:
            self.http.get(embed_url, referer=self.base + "/", cookie_jar=cookie_path)
            video_id = embed_url.rstrip("/").split("/")[-1]
            embed_base = embed_url.rsplit("/e/", 1)[0] if "/e/" in embed_url else embed_url.rsplit("/", 1)[0]
            media_url = f"{embed_base}/media/{video_id}"
            media_data = self.http.get_json(
                media_url,
                referer=embed_url,
                headers={"X-Requested-With": "XMLHttpRequest"},
                cookie_jar=cookie_path,
            )
        except HttpError as exc:
            raise ProviderUnavailable(f"AnimeKai media host failed: {exc}") from exc
        finally:
            try:
                Path(cookie_path).unlink()
            except OSError:
                pass

        encrypted_media = self._result_object(media_data, "media")
        if not isinstance(encrypted_media, str) or not encrypted_media:
            raise ProviderChanged("AnimeKai returned an invalid encrypted media payload.")
        final = self._decrypt_mega(encrypted_media)
        raw_sources = final.get("sources", [])
        streams: list[Stream] = []
        if isinstance(raw_sources, list):
            for item in raw_sources:
                if not isinstance(item, dict):
                    continue
                source_url = item.get("file") or item.get("url") or item.get("src")
                if isinstance(source_url, str) and source_url:
                    streams.append(Stream(self._quality(item), source_url))
        if not streams:
            raise StreamNotFound("AnimeKai resolved the server but returned no playable streams.")

        # If the provider gives one master HLS without labels, expose its actual variants.
        if len(streams) == 1 and streams[0].quality == "auto" and ".m3u8" in streams[0].url:
            try:
                master = self.http.get(streams[0].url, referer=embed_url)
                variants = HianimeProvider._parse_master(master, streams[0].url)
                if variants:
                    streams = variants
            except HttpError:
                pass
        streams.sort(key=stream_rank, reverse=True)
        _, mal_id = self._anime_info(anime_id)
        return StreamBundle(
            streams=streams,
            subtitle=self._subtitle(final.get("tracks")),
            referer=embed_url,
            mal_id=mal_id,
            provider=self.name,
        )

    def resolve(self, anime: Anime | str, episode: Episode, mode: str) -> StreamBundle:
        self._require_base()
        anime_id = _provider_id(anime)
        token = self._encode(episode.episode_id)
        url = f"{self.base}/ajax/links/list?{urlencode({'token': episode.episode_id, '_': token})}"
        try:
            data = self.http.get_json(url, headers=self.ajax_headers, referer=f"{self.base}/watch/{anime_id}")
        except HttpError as exc:
            raise ProviderUnavailable(f"AnimeKai server list failed: {exc}") from exc
        fragment = self._result_object(data, "servers")
        if not isinstance(fragment, str):
            raise ProviderChanged("AnimeKai server list HTML was missing.")
        servers = self._mode_servers(self._server_groups(fragment), mode)
        if not servers:
            raise StreamNotFound(f"AnimeKai has no {mode} server for episode {episode.number}.")

        failures: list[str] = []
        for server in servers:
            link_id = server.get("link_id", "")
            if not link_id:
                continue
            try:
                return self._resolve_link(anime_id, link_id)
            except ProviderError as exc:
                failures.append(str(exc))
        detail = failures[-1] if failures else "no usable server"
        raise StreamNotFound(f"AnimeKai could not resolve episode {episode.number}: {detail}")



# ---------- Kuhi multi-source backup provider ----------

class KuhiProvider(Provider):
    """AniList-keyed multi-source backup using the Kuhi API.

    Kuhi races multiple native upstream providers itself, so ani-py gets a
    second layer of failover without depending on one mirror/domain.  Automatic
    use is guarded by a live media preflight: a provider is considered
    available only if a known episode resolves to a direct playable stream.
    """

    name = "kuhi"
    display_name = "Kuhi"
    capabilities = ProviderCapabilities(
        sub=True, dub=True, subtitles=True, qualities=True, mal_id=True
    )
    experimental = True

    def __init__(self, http: HttpClient) -> None:
        self.http = http
        self.base = os.getenv("ANI_PY_KUHI_URL", KUHI_BASE_URL).rstrip("/")
        self._available: Optional[bool] = None
        self._mal_cache: dict[str, Optional[str]] = {}

    @staticmethod
    def _response_payload(data: object, context: str) -> dict:
        if not isinstance(data, dict):
            raise ProviderChanged(f"Kuhi {context} response was not an object.")
        if data.get("success") is False:
            message = data.get("message") or data.get("detail") or "request failed"
            raise ProviderUnavailable(f"Kuhi {context} failed: {message}")
        nested = data.get("results")
        if isinstance(nested, dict):
            # Some deployments wrap API payloads in {success, results:{...}}.
            return nested
        return data

    @staticmethod
    def _stream_objects(data: object) -> list[dict[str, object]]:
        try:
            payload = KuhiProvider._response_payload(data, "extract")
        except ProviderError:
            return []
        raw = payload.get("streams")
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    def available(self) -> bool:
        """Deep preflight that proves the stream resolver, not just DNS/search.

        Naruto (AniList 20), episode 1 is used as a stable probe.  The result is
        memoized so auto-failover pays this cost at most once per ani-py run.
        """
        if self._available is not None:
            return self._available
        probe = (
            f"{self.base}/anime/extract/20?"
            + urlencode({"e": "1", "type": "sub"})
        )
        try:
            data = self.http.get_json(probe, timeout=20)
            streams = self._stream_objects(data)
            self._available = any(
                isinstance(item.get("url"), str)
                and str(item.get("url")).startswith(("http://", "https://"))
                and str(item.get("type") or "hls").lower() != "embed"
                for item in streams
            )
            if not self._available:
                warn("Kuhi preflight returned no direct playable stream; treating it as unavailable.")
        except (HttpError, ProviderError) as exc:
            warn(f"Kuhi media preflight failed ({exc}); treating it as unavailable.")
            self._available = False
        return self._available

    @staticmethod
    def _title(item: dict[str, object]) -> str:
        title = item.get("title")
        if isinstance(title, dict):
            for key in ("english", "romaji", "native"):
                value = title.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        if isinstance(title, str):
            return title.strip()
        for key in ("name", "englishName"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def search(self, query: str) -> list[Anime]:
        url = f"{self.base}/anime/search?{urlencode({'query': query, 'page': 1, 'per_page': 40})}"
        try:
            data = self.http.get_json(url, timeout=15)
        except HttpError as exc:
            raise ProviderUnavailable(f"Kuhi search failed: {exc}") from exc

        if not isinstance(data, dict):
            raise ProviderChanged("Kuhi search response was not an object.")
        if data.get("success") is False:
            raise ProviderUnavailable(f"Kuhi search failed: {data.get('message') or 'request failed'}")

        results = data.get("results")
        if isinstance(results, dict):
            results = results.get("results")
        if not isinstance(results, list):
            raise ProviderChanged("Kuhi search response did not contain a result list.")

        found: list[Anime] = []
        seen: set[str] = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            ident = item.get("id") or item.get("anilistId")
            title = self._title(item)
            if ident is None or not title:
                continue
            provider_id = str(ident)
            if provider_id not in seen:
                found.append(Anime(provider_id=provider_id, title=title, provider=self.name))
                seen.add(provider_id)
        return found

    def _episode_payload(self, anime_id: str) -> dict:
        try:
            data = self.http.get_json(f"{self.base}/anime/episodes/{quote_plus(anime_id)}", timeout=20)
        except HttpError as exc:
            raise ProviderUnavailable(f"Kuhi episode lookup failed: {exc}") from exc
        return self._response_payload(data, "episodes")

    def episodes(self, anime: Anime | str) -> list[Episode]:
        anime_id = _provider_id(anime)
        payload = self._episode_payload(anime_id)
        providers = payload.get("providers")
        if not isinstance(providers, dict):
            raise ProviderChanged("Kuhi episode response did not contain providers.")

        numbers: dict[str, Episode] = {}
        for provider_data in providers.values():
            if not isinstance(provider_data, dict):
                continue
            groups = provider_data.get("episodes")
            if not isinstance(groups, dict):
                continue
            for group_name in ("sub", "dub"):
                rows = groups.get(group_name)
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    raw_number = row.get("number")
                    if raw_number is None:
                        continue
                    number = str(raw_number)
                    if number.endswith(".0"):
                        number = number[:-2]
                    # Kuhi's extract endpoint is AniList-id + episode number,
                    # so the number itself is the stable episode key we need.
                    numbers.setdefault(number, Episode(episode_id=number, number=number))

        episodes = list(numbers.values())
        episodes.sort(
            key=lambda e: float(e.number)
            if re.fullmatch(r"\d+(?:\.\d+)?", e.number)
            else 10**9
        )
        if not episodes:
            raise EpisodeNotFound(f"Kuhi returned no episodes for AniList id {anime_id}.")
        return episodes

    @staticmethod
    def _quality(item: dict[str, object]) -> str:
        raw = item.get("quality")
        if isinstance(raw, str):
            match = re.search(r"(\d{3,4})", raw)
            if match:
                return match.group(1) + "p"
        resolution = item.get("resolution")
        if isinstance(resolution, dict):
            height = resolution.get("height")
            if isinstance(height, (int, float)) and height > 0:
                return f"{int(height)}p"
        return "auto"

    @staticmethod
    def _subtitle(items: object) -> Optional[str]:
        if not isinstance(items, list):
            return None
        ranked: list[tuple[int, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("file") or item.get("url") or item.get("src")
            if not isinstance(url, str) or not url:
                continue
            label = str(item.get("label") or item.get("lang") or item.get("language") or "").lower()
            default = bool(item.get("default"))
            score = 3 if default else 2 if "english" in label or label in {"en", "eng"} else 1
            ranked.append((score, url))
        return max(ranked, default=(-1, ""))[1] or None

    def _mal_id(self, anime_id: str) -> Optional[str]:
        if anime_id in self._mal_cache:
            return self._mal_cache[anime_id]
        mal_id: Optional[str] = None
        try:
            data = self.http.get_json(f"{self.base}/anime/info/{quote_plus(anime_id)}", timeout=12)
            payload = self._response_payload(data, "info")
            raw = payload.get("idMal") or payload.get("malId") or payload.get("mal_id")
            if raw is not None and str(raw).isdigit():
                mal_id = str(raw)
        except (HttpError, ProviderError):
            # Stream playback should not fail just because MAL metadata is absent.
            pass
        self._mal_cache[anime_id] = mal_id
        return mal_id

    def resolve(self, anime: Anime | str, episode: Episode, mode: str) -> StreamBundle:
        anime_id = _provider_id(anime)
        url = (
            f"{self.base}/anime/extract/{quote_plus(anime_id)}?"
            + urlencode({"e": episode.number, "type": mode})
        )
        try:
            data = self.http.get_json(url, timeout=25)
        except HttpError as exc:
            raise ProviderUnavailable(f"Kuhi stream extraction failed: {exc}") from exc

        payload = self._response_payload(data, "extract")
        raw_streams = payload.get("streams")
        if not isinstance(raw_streams, list):
            raise ProviderChanged("Kuhi extract response did not contain streams.")

        streams: list[Stream] = []
        seen: set[str] = set()
        referer = self.base + "/"
        for item in raw_streams:
            if not isinstance(item, dict):
                continue
            source_url = item.get("url") or item.get("file") or item.get("src")
            if not isinstance(source_url, str) or not source_url or source_url in seen:
                continue
            source_type = str(item.get("type") or "").lower()
            if source_type == "embed":
                continue
            if not source_url.startswith(("http://", "https://")):
                continue
            seen.add(source_url)
            streams.append(Stream(self._quality(item), source_url))
            item_referer = item.get("referer")
            headers = item.get("headers")
            if isinstance(item_referer, str) and item_referer:
                referer = item_referer
            elif isinstance(headers, dict):
                header_referer = headers.get("Referer") or headers.get("referer")
                if isinstance(header_referer, str) and header_referer:
                    referer = header_referer

        if not streams:
            raise StreamNotFound(f"Kuhi returned no direct {mode} stream for episode {episode.number}.")

        # Expand a single unlabeled HLS master so Change quality shows the real
        # variants rather than only "auto".
        if len(streams) == 1 and streams[0].quality == "auto" and ".m3u8" in streams[0].url:
            try:
                master = self.http.get(streams[0].url, referer=referer, timeout=12)
                variants = HianimeProvider._parse_master(master, streams[0].url)
                if variants:
                    streams = variants
            except HttpError:
                pass

        streams.sort(key=stream_rank, reverse=True)
        subtitle = self._subtitle(payload.get("subtitles") or payload.get("tracks"))
        return StreamBundle(
            streams=streams,
            subtitle=subtitle,
            referer=referer,
            mal_id=self._mal_id(anime_id),
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
        self.last_activity = time.monotonic()

    def relay_url(self, target: str) -> str:
        host, port = self.server_address[:2]
        return f"http://127.0.0.1:{port}/{self.token}/{_relay_encode(target)}"

    def subtitle_relay_url(self, target: str) -> str:
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
            requested = requested_player or os.getenv("ANI_PY_ANDROID_PLAYER", "auto")
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

        ani-skip 1.x uses -q/--query for the anime identifier.  A missing MAL
        id or a failing ani-skip invocation should never fail playback, but it
        must be visible to the user instead of silently disabling --skip.
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
        # ani-skip 1.x accepts the anime identifier through -q/--query.
        # Numeric MAL IDs work as query values, so use the documented interface
        # directly instead of probing an incompatible flag first.
        command = [exe, "-q", mal_id, "-e", episode]
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
            return self.download(stream, title=title, subtitle=subtitle, referer=referer)

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

    def replay(self) -> bool:
        if not self._is_mpv() or not self.active():
            return False
        try:
            self._ipc(["seek", 0, "absolute"])
            self._ipc(["set_property", "pause", False])
            return True
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
            return False

    def download(self, stream: Stream, *, title: str, subtitle: Optional[str], referer: str) -> int:
        outdir = Path(os.getenv("ANI_PY_DOWNLOAD_DIR", ".")).expanduser()
        outdir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip() or "episode"

        if subtitle:
            curl = HttpClient().exe
            sub_cmd = [
                curl, "--fail", "-sS", "-L", "--max-time", "30",
                "-A", USER_AGENT, "-e", referer, subtitle,
                "-o", str(outdir / f"{safe}.vtt"),
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
            [HianimeProvider(self.http), KuhiProvider(self.http), AnimeKaiProvider(self.http)],
            order,
        )
        self.history = HistoryStore()
        self.menu = Menu(args.menu, args.menu_flags)
        self.playback = None if args.list_providers else Playback(args)
        self.bundle_cache: dict[tuple[str, str, str, str], StreamBundle] = {}
        self.episode_cache: dict[tuple[str, str], list[Episode]] = {}
        self.fallback_map: dict[tuple[str, str], Anime] = {}
        self.last_stream: Optional[Stream] = None
        self.last_provider: Optional[str] = None

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
        picked = self.menu.choose(rows, "Continue › ")
        if not picked:
            raise SystemExit(0)
        entry = entries[rows.index(picked[0])]
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
        return mapping.get(chosen[0]) if chosen else None

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
        self.last_stream = stream
        self.last_provider = bundle.provider
        clear_screen()
        banner(f"{anime.title}  •  Episode {episode.number}  •  {stream.quality}")
        print()
        print(f"  {sty('Title', C.DIM)}    {anime.title}")
        print(f"  {sty('Episode', C.DIM)}  {episode.number}")
        print(f"  {sty('Mode', C.DIM)}     {self.args.mode.upper()}")
        print(f"  {sty('Quality', C.DIM)}  {stream.quality}")
        print(f"  {sty('Source', C.DIM)}   {self.providers.get(bundle.provider).display_name}")
        print(f"  {sty('Player', C.DIM)}   {Path(self.playback.player).name if self.playback.player != 'download' else 'download'}")
        print(f"  {sty('Subtitle', C.DIM)} {'yes' if bundle.subtitle else 'none'}")
        print()
        assert self.playback is not None
        play_fn = self.playback.replace if replace else self.playback.play
        play_kwargs = dict(
            title=f"{anime.title} Episode {episode.number}",
            subtitle=bundle.subtitle,
            referer=bundle.referer,
            mal_id=bundle.mal_id,
            episode=episode.number,
            subtitle_language=bundle.subtitle_language,
            subtitle_label=bundle.subtitle_label,
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
                "Change quality",
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
                self.playback.stop()
                return
            if action == "Detach & exit":
                self.playback.detach()
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
                    current = mapping[chosen[0]]
                    self._play_episode(anime, current, quality, replace=True)
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

            environment:
              ANI_PY_PLAYER          preferred player (mpv, vlc, iina, auto, or executable)
              ANI_PY_PLAYER_FLAGS    extra player flags
              ANI_PY_IPC_SOCKET      override private mpv IPC socket (advanced)
              ANI_PY_MENU            fzf, rofi, dmenu, or fallback terminal UI
              ANI_PY_MENU_FLAGS      extra menu flags
              ANI_PY_DOWNLOAD_DIR    download destination
              ANI_PY_HIST_DIR        state directory root
              ANI_PY_CURL            curl/curl-impersonate executable
              ANI_PY_PROVIDER        auto, hianime, kuhi, or animekai
              ANI_PY_PROVIDER_ORDER  failover order (default hianime; add kuhi/animekai to opt in)
              ANI_PY_KUHI_URL        override Kuhi API base URL
              ANI_PY_ANIMEKAI_URL    override AnimeKai base URL
              NO_COLOR               disable ANSI color
            """
        ),
    )
    parser.add_argument("query", nargs="*", help="anime search query")
    parser.add_argument("-c", "--continue", dest="continue_watching", action="store_true", help="continue from history")
    parser.add_argument("-d", "--download", action="store_true", help="download instead of play")
    parser.add_argument("-D", "--delete-history", dest="clear_history", action="store_true", help="clear watch history")
    parser.add_argument("-e", "--episode", "-r", "--range", dest="episode", help="episode or range, e.g. 4 or 4-9")
    parser.add_argument("-q", "--quality", default=os.getenv("ANI_PY_QUALITY", "best"), help="best, worst, 360, 480, 720, 1080")
    parser.add_argument("-S", "--select-nth", type=int, help="select search result by index")
    parser.add_argument(
        "--provider",
        choices=["auto", "hianime", "kuhi", "animekai"],
        default=os.getenv("ANI_PY_PROVIDER", "auto"),
        help="source provider (default: auto with failover)",
    )
    parser.add_argument(
        "--provider-order",
        default=os.getenv("ANI_PY_PROVIDER_ORDER", "hianime"),
        help="comma-separated auto-failover order (default: hianime; kuhi/animekai are experimental opt-in)",
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
    # Compatibility aliases from pre-0.5.2 releases. Keep parsing them for now,
    # but expose --player/-p as the single documented player interface.
    parser.add_argument(
        "-v", "--vlc",
        dest="player",
        action="store_const",
        const="vlc",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--android-player",
        dest="player",
        choices=["auto", "vlc", "mpv"],
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--player-flag", action="append", default=[], help="extra player argument (repeatable; use --player-flag='--flag' for dash-flags)")
    parser.add_argument("--ipc-socket", help="mpv IPC socket path (default: private per ani-py process)")
    parser.add_argument("--menu", choices=["fzf", "rofi", "dmenu"], help="interactive menu frontend")
    parser.add_argument("--menu-flags", default="", help="extra menu frontend flags (use --menu-flags='--flag' for dash-flags)")
    parser.add_argument("--skip", action="store_true", default=os.getenv("ANI_PY_SKIP_INTRO", "0") == "1", help="use ani-skip with mpv")
    parser.add_argument("--no-detach", action="store_true", default=os.getenv("ANI_PY_NO_DETACH", "0") == "1", help="keep player attached")
    parser.add_argument("--exit-after-play", action="store_true", default=os.getenv("ANI_PY_EXIT_AFTER_PLAY", "0") == "1", help="exit after player closes/launches")
    parser.add_argument(
        "--android-debug",
        action="store_true",
        default=os.getenv("ANI_PY_ANDROID_DEBUG", "0") == "1",
        help="print Android playback/relay diagnostics to stderr (harmless off Android)",
    )
    parser.add_argument("--_android-relay-config", help=argparse.SUPPRESS)
    parser.add_argument("-V", "--version", action="version", version=f"{APP_NAME} {VERSION}")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args._android_relay_config:
        return run_android_relay(args._android_relay_config)
    try:
        return App(args).run()
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
