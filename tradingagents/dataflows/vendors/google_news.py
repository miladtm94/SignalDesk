"""Company and market headlines from Google News search, for any date window.

Yahoo's news endpoints and the social feeds serve only their latest items, so a
run dated more than a few days back finds nothing in its window: the analysts are
told the feed is unavailable and decide without the news that moved the stock
(an earnings beat, a guidance cut, analyst actions). Google News search takes a
date range (``after:``/``before:``) and needs no key, so a past run reads what was
published in its own window.

The feed carries a headline, its source and a publication date, not the article.
Every item is kept to the requested window on its publication date, like every
other dated path (``in_window``). A window that ended days ago is cached for
good, so a re-run or a backtest cell reads the same headlines as the first run;
a window reaching the present is refetched after half an hour.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.date_window import in_window
from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.files import replace_file
from tradingagents.dataflows.symbols import normalize_symbol
from tradingagents.dataflows.vendors.yahoo.fundamentals import get_company_profile

logger = logging.getLogger(__name__)

SEARCH_URL = "https://news.google.com/rss/search"
SOURCE = "Google News search"
LIVE_CACHE_SECONDS = 30 * 60
THIN_RESULT = 10
# Google answers fast but throttles bursts; analysts run side by side.
_MIN_INTERVAL_SECONDS = 1.0
_request_lock = threading.Lock()
_last_request = 0.0

# Words a company's legal name ends with that its news rarely repeats:
# "Micron Technology, Inc." is reported as "Micron".
_NAME_TAIL = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company", "cos", "ltd", "limited", "plc",
    "llc", "lp", "nv", "sa", "ag", "se", "asa", "ab", "spa", "oyj", "kgaa", "bhd", "tbk", "holdings",
    "holding", "group", "technologies", "technology", "the", "usa", "us", "usd", "trust", "etf",
    "fund", "shares", "class", "ordinary", "common", "adr", "ads",
})
_MONTH_TAIL = re.compile(r"\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{2,4}$", re.I)
# Tickers that are everyday words: quoted alone they match everything.
_WORD_TICKERS = frozenset({
    "all", "and", "are", "big", "box", "car", "cash", "cat", "de", "dog", "eat", "ever", "fast",
    "fly", "for", "free", "fun", "go", "good", "has", "hd", "he", "home", "hope", "it", "key",
    "life", "love", "low", "man", "me", "new", "next", "now", "on", "one", "open", "or", "out",
    "play", "real", "run", "save", "see", "so", "sun", "tech", "true", "two", "up", "us", "very",
    "we", "well", "win", "work", "you",
})

# Local Google News editions cover their market's companies better.
_EDITIONS = {".AX": ("en-AU", "AU"), ".L": ("en-GB", "GB"), ".TO": ("en-CA", "CA"),
             ".V": ("en-CA", "CA"), ".NS": ("en-IN", "IN"), ".BO": ("en-IN", "IN"),
             ".NZ": ("en-NZ", "NZ"), ".SI": ("en-SG", "SG")}


def search_name(name: str | None) -> str | None:
    """The name news uses for a company: its legal name without the legal tail."""
    if not name:
        return None
    text = re.sub(r"\([^)]*\)", " ", name)
    text = re.sub(r"\bclass\s+[a-z]\b", " ", text, flags=re.I)
    text = _MONTH_TAIL.sub("", text.strip())
    words = [w for w in re.split(r"[\s,]+", text) if w]

    def plain(word: str) -> str:
        return word.lower().replace(".", "").strip("&-/")

    while len(words) > 1 and plain(words[-1]) in _NAME_TAIL | {""}:
        words.pop()
    if len(words) > 1 and plain(words[0]) == "the":
        words.pop(0)
    core = " ".join(words).strip(" .,&-")
    return core or None


@functools.lru_cache(maxsize=256)
def _cached_profile_name(canonical: str) -> str | None:
    profile = get_company_profile(canonical)  # raises, so a failed lookup is not cached
    return profile.get("longName") or profile.get("shortName")


def _profile_name(canonical: str) -> str | None:
    try:
        return _cached_profile_name(canonical)
    except Exception as exc:  # noqa: BLE001 - a name only sharpens the search
        logger.debug("No profile name for %s: %s", canonical, exc)
        return None


def _edition(canonical: str) -> tuple[str, str]:
    suffix = canonical[canonical.rfind("."):] if "." in canonical else ""
    return _EDITIONS.get(suffix.upper(), ("en-US", "US"))


def company_queries(ticker: str) -> tuple[list[str], str]:
    """The searches that find this instrument's news, most precise first, and its subject.

    Google News reads a quoted ticker as the listed company, so the symbol
    search is precise, but it misses coverage that names only the company
    (most of a past week's for MU). The name search fills that gap, but a name
    can be generic ("Space Exploration" for SPCX), so it only tops up. A ticker
    that is a word or a letter is searched as "<ticker> stock". The name is
    today's, used only as a search term: every result is still kept to the
    run's window by its publication date.
    """
    canonical = normalize_symbol(ticker)
    base = re.split(r"[.\-=]", canonical.lstrip("^"))[0]
    name = search_name(_profile_name(canonical))
    if canonical.endswith(("-USD", "-USDT", "-USDC")):
        queries = [f'"{name}"' if name else f'"{base}" crypto']
    else:
        if canonical.endswith(".AX"):
            symbol = f"ASX:{base}"
        elif canonical != base:
            symbol = canonical
        elif len(base) < 2 or base.lower() in _WORD_TICKERS:
            symbol = f"{base} stock"
        else:
            symbol = base
        queries = [f'"{symbol}"'] + ([f'"{name}"'] if name and name.lower() != symbol.lower() else [])
    subject = f"{name} ({ticker})" if name else ticker
    return queries, subject


def _cache_path(key: str) -> Path:
    digest = hashlib.sha1(key.encode()).hexdigest()[:20]
    return Path(get_config()["data_cache_dir"]) / "google_news" / f"{digest}.json"


def _cached(path: Path, settled: bool) -> list[dict] | None:
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not settled and time.time() - saved.get("fetched", 0) > LIVE_CACHE_SECONDS:
        return None
    return saved.get("items")


def _get(params: dict, attempts: int = 3) -> bytes:
    global _last_request
    for attempt in range(attempts):
        if attempt:
            time.sleep(2 ** attempt)
        with _request_lock:
            wait = _MIN_INTERVAL_SECONDS - (time.monotonic() - _last_request)
            if wait > 0:
                time.sleep(wait)
            _last_request = time.monotonic()
        try:
            response = requests.get(SEARCH_URL, params=params, timeout=20,
                                    headers={"User-Agent": "Mozilla/5.0"})
        except requests.RequestException as exc:
            error = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code == 200:
                return response.content
            error = f"HTTP {response.status_code}"
            if response.status_code not in (429, 500, 502, 503, 504):
                break
    raise VendorUnavailableError(f"{SOURCE} did not answer ({error})")


def _parse(feed: bytes) -> list[dict]:
    try:
        root = ET.fromstring(feed)
    except ET.ParseError as exc:
        raise VendorUnavailableError(f"{SOURCE} returned something other than a feed ({exc})") from None
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        source = (item.findtext("source") or "").strip()
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(UTC)
        except (TypeError, ValueError):
            continue  # an undated headline cannot be kept to a window
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].rstrip()
        if title:
            items.append({"title": title, "source": source or "unknown", "published": published.isoformat()})
    return items


def search(query: str, start_date: str, end_date: str, edition: tuple[str, str] = ("en-US", "US")) -> list[dict]:
    """Headlines matching ``query`` published from ``start_date`` through ``end_date``.

    In Google's relevance order, each with its title, source and UTC publication
    time. Raises VendorUnavailableError when Google does not answer.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    # Google's date operators are coarse at the edges; ask a day wider on each
    # side and keep the window exactly below.
    after, before = (start - timedelta(days=1)).date(), (end + timedelta(days=2)).date()
    language, country = edition
    params = {"q": f"{query} after:{after} before:{before}", "hl": language, "gl": country,
              "ceid": f"{country}:{language.split('-')[0]}"}
    path = _cache_path(json.dumps(params, sort_keys=True))
    settled = before < (datetime.now(UTC) - timedelta(days=2)).date()
    items = _cached(path, settled)
    if items is None:
        items = _parse(_get(params))
        if len(items) < THIN_RESULT:
            # Google at times answers a query with a few of its items and with all
            # of them moments later (6, then 100). A settled window is cached for
            # good, so a thin answer is asked again before it is kept.
            time.sleep(2)
            again = _parse(_get(params))
            items = again if len(again) > len(items) else items
        path.parent.mkdir(parents=True, exist_ok=True)
        replace_file(path, lambda temp: Path(temp).write_text(
            json.dumps({"fetched": time.time(), "query": params["q"], "items": items}), encoding="utf-8"))
    kept, seen = [], set()
    for item in items:
        published = datetime.fromisoformat(item["published"])
        if _key(item) not in seen and in_window(published, start, end):
            seen.add(_key(item))
            kept.append(item)
    return kept


def _key(item: dict) -> str:
    """A headline as it reads, so one story syndicated with other punctuation counts once."""
    return re.sub(r"\W+", " ", item["title"].lower()).strip()


def _lines(items: list[dict]) -> str:
    ordered = sorted(items, key=lambda item: item["published"], reverse=True)
    return "\n".join(f"- {item['published'][:10]} · {item['source']} · {item['title']}" for item in ordered)


def get_news_google(ticker: str, start_date: str, end_date: str) -> str:
    """Headlines about ``ticker`` published in the window, from Google News search."""
    queries, subject = company_queries(ticker)
    edition = _edition(normalize_symbol(ticker))
    limit = get_config()["news_article_limit"]
    items: list[dict] = []
    failures: list[VendorUnavailableError] = []
    for query in queries:
        if len(items) >= limit:
            break
        try:
            found = search(query, start_date, end_date, edition)
        except VendorUnavailableError as exc:
            failures.append(exc)
            continue
        known = {_key(item) for item in items}
        items += [item for item in found if _key(item) not in known]
    if len(failures) == len(queries):
        raise failures[0]
    if not items:
        return (f"No headlines about {subject} published between {start_date} and {end_date} "
                f"were found by {SOURCE}.")
    return (f"## {ticker} News, from {start_date} to {end_date} ({SOURCE}: date · source · headline, "
            f"{min(limit, len(items))} most relevant found)\n\n{_lines(items[:limit])}\n")


def get_global_news_google(as_of_date: str, look_back_days: int | None = None, limit: int | None = None) -> str:
    """Macro and market headlines for the configured topics, from Google News search."""
    config = get_config()
    look_back_days = config["global_news_lookback_days"] if look_back_days is None else look_back_days
    limit = config["global_news_article_limit"] if limit is None else limit
    queries = config["global_news_queries"]
    start_date = (datetime.strptime(as_of_date, "%Y-%m-%d") - timedelta(days=look_back_days)).strftime("%Y-%m-%d")
    per_topic = max(2, math.ceil(limit / max(1, len(queries))))
    sections, seen, failures = [], set(), []
    for query in queries:
        try:
            items = search(query, start_date, as_of_date)
        except VendorUnavailableError as exc:
            failures.append(exc)
            continue
        fresh = [item for item in items if _key(item) not in seen][:per_topic]
        seen.update(_key(item) for item in fresh)
        if fresh:
            sections.append(f"### {query}\n{_lines(fresh)}")
    if failures and len(failures) == len(queries):
        raise failures[0]
    if not sections:
        return f"No market headlines published between {start_date} and {as_of_date} were found by {SOURCE}."
    return (f"## Global Market News, from {start_date} to {as_of_date} ({SOURCE}: date · source · headline)\n\n"
            + "\n\n".join(sections) + "\n")
