"""StockAnalysis.com as the last vendor in a chain, for when Yahoo and SEC EDGAR fail.

Serves the company overview (the statistics page: valuation, margins, returns,
short interest, the analyst target), quarterly and annual statements, and an
analyst-coverage page (price targets, the monthly recommendation trend, the
latest dated analyst actions, revenue and EPS forecasts).

Its terms allow automated access only to crawlers that follow its robots.txt,
so every request checks robots.txt (re-read daily), names SignalDesk in the
User-Agent, waits ``MIN_INTERVAL`` seconds after the previous request, and is
cached for ``CACHE_SECONDS``. Cells behind its paid plan ("Pro") are left out,
never worked around, and the output names its source. Every page is today's
snapshot without filing dates, so a past run is withheld, as Yahoo's profile is
(#1300). Fork-owned (SignalDesk).
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated
from urllib.robotparser import RobotFileParser

import requests

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.date_window import (
    is_historical,
    withhold_live_profile,
    withhold_undated_statements,
)
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.files import replace_file
from tradingagents.dataflows.symbols import normalize_symbol

logger = logging.getLogger(__name__)

BASE_URL = "https://stockanalysis.com"
USER_AGENT = "SignalDesk/1.0 (personal research console; follows robots.txt)"
SOURCE = "Source: stockanalysis.com (fallback vendor; today's figures)."
TIMEOUT = 20
MIN_INTERVAL = 3.0           # seconds between requests to the site
CACHE_SECONDS = 6 * 3600     # a page is reused this long
ROBOTS_SECONDS = 24 * 3600
MAX_PERIODS = 8

# Yahoo's exchange suffix to the exchange code in StockAnalysis's quote URLs.
EXCHANGES = {
    ".AX": "asx", ".L": "lon", ".TO": "tsx", ".V": "tsxv", ".HK": "hkg", ".T": "tyo", ".DE": "etr",
    ".PA": "epa", ".AS": "ams", ".SW": "swx", ".MI": "bit", ".NS": "nse", ".BO": "bom", ".NZ": "nzx",
    ".SI": "sgx", ".KS": "krx", ".ST": "sto", ".OL": "osl", ".CO": "cph", ".HE": "hel", ".MC": "bme",
}
STATEMENTS = {"income": "financials/income-statement", "balance": "financials/balance-sheet",
              "cashflow": "financials/cash-flow-statement"}
# The statistics page's tables worth an analyst's time, in reading order.
OVERVIEW_SECTIONS = ("Total Valuation", "Valuation Ratios", "Enterprise Valuation", "Margins",
                     "Financial Efficiency", "Financial Position", "Income Statement", "Balance Sheet",
                     "Cash Flow", "Share Statistics", "Short Selling Information", "Stock Price Statistics",
                     "Analyst Forecast", "Important Dates", "Dividends & Yields", "Scores")
# What the site shows in a cell its free pages do not fill.
_PAID = {"pro", "upgrade"}

_lock = threading.Lock()
_last_request = 0.0
_robots: tuple[float, RobotFileParser] | None = None


class _Tables(HTMLParser):
    """Each <table> on a page with the heading above it, as rows of cells.

    A cell keeps its text pieces apart, so a row label is its first piece:
    StockAnalysis puts a hidden "Revenue Growth" beside "Revenue" in the label cell.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[tuple[str, list[list[list[str]]]]] = []
        self._heading: list[str] | None = None
        self._last_heading = ""
        self._rows: list[list[list[str]]] | None = None
        self._cell: list[str] | None = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("h1", "h2", "h3") and self._rows is None:
            self._heading = []
        elif tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._rows = []
        elif tag == "tr" and self._rows is not None:
            self._rows.append([])
        elif tag in ("td", "th") and self._rows is not None and self._rows:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3") and self._heading is not None:
            self._last_heading = " ".join(" ".join(self._heading).split())
            self._heading = None
        elif tag in ("td", "th") and self._cell is not None and self._rows:
            self._rows[-1].append(self._cell)
            self._cell = None
        elif tag == "table":
            self._depth -= 1
            if self._depth == 0 and self._rows is not None:
                self.tables.append((self._last_heading, [row for row in self._rows if row]))
                self._rows = None

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if self._cell is not None:
            self._cell.append(text)
        elif self._heading is not None:
            self._heading.append(text)


def page_tables(html: str) -> list[tuple[str, list[list[str]]]]:
    """The page's tables as (heading, rows); a cell is its text, a row label its first piece."""
    parser = _Tables()
    parser.feed(html)
    out = []
    for heading, rows in parser.tables:
        flat = []
        for row in rows:
            cells = [(cell[0] if index == 0 and cell else " ".join(cell)) for index, cell in enumerate(row)]
            flat.append(cells)
        out.append((heading, flat))
    return out


def page_path(symbol: str, page: str = "") -> str:
    """The site path of ``page`` for a Yahoo-style symbol; raises when the site has no such listing."""
    canonical = normalize_symbol(symbol).upper()
    if any(mark in canonical for mark in ("^", "=", "/")) or re.search(r"-(USD|USDT|EUR|GBP|AUD|BTC)$", canonical):
        raise NoMarketDataError(symbol, canonical, "StockAnalysis covers listed companies only")
    suffix = next((s for s in EXCHANGES if canonical.endswith(s)), None)
    tail = f"{page.strip('/')}/" if page else ""
    if suffix:
        return f"/quote/{EXCHANGES[suffix]}/{canonical[: -len(suffix)]}/{tail}"
    if "." in canonical:
        raise NoMarketDataError(symbol, canonical, "StockAnalysis does not list this exchange")
    # Share classes: Yahoo writes BRK-B, StockAnalysis brk.b.
    return f"/stocks/{canonical.lower().replace('-', '.')}/{tail}"


def _robots_allow(path: str) -> bool:
    global _robots
    now = time.monotonic()
    if _robots is None or now - _robots[0] > ROBOTS_SECONDS:
        try:
            response = requests.get(f"{BASE_URL}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise VendorUnavailableError(f"StockAnalysis robots.txt could not be read ({type(exc).__name__})") from exc
        parser = RobotFileParser()
        # Without a readable robots.txt nothing is crawled: unknown rules are not permission.
        if response.status_code != 200:
            raise VendorUnavailableError(f"StockAnalysis robots.txt answered HTTP {response.status_code}")
        parser.parse(response.text.splitlines())
        _robots = (now, parser)
    return _robots[1].can_fetch(USER_AGENT, f"{BASE_URL}{path}")


def _cache_file(path: str) -> str:
    key = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")
    folder = os.path.join(get_config()["data_cache_dir"], "stockanalysis")
    os.makedirs(folder, exist_ok=True)
    # Already reduced to letters, digits and underscores: no path can escape the folder.
    return os.path.join(folder, f"{key[:150]}.html")


def fetch(path: str) -> str | None:
    """The page at ``path`` (with any query), from the cache or the site; None when it does not exist."""
    cached = _cache_file(path)
    if os.path.exists(cached) and time.time() - os.path.getmtime(cached) < CACHE_SECONDS:
        with open(cached, encoding="utf-8") as handle:
            return handle.read()
    global _last_request
    with _lock:
        if not _robots_allow(path):
            raise VendorUnavailableError(f"StockAnalysis's robots.txt does not allow {path}")
        wait = MIN_INTERVAL - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            response = requests.get(f"{BASE_URL}{path}", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise VendorUnavailableError(f"StockAnalysis request failed ({type(exc).__name__})") from exc
        finally:
            _last_request = time.monotonic()
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise VendorUnavailableError(f"StockAnalysis answered HTTP {response.status_code}")
    replace_file(cached, lambda temp: Path(temp).write_text(response.text, encoding="utf-8"))
    return response.text


def _tables_for(symbol: str, page: str, query: str = "") -> list[tuple[str, list[list[str]]]]:
    path = page_path(symbol, page) + query
    html = fetch(path)
    if html is None:
        raise NoMarketDataError(symbol, normalize_symbol(symbol), f"no StockAnalysis page at {path}")
    tables = page_tables(html)
    if not tables:
        raise NoMarketDataError(symbol, normalize_symbol(symbol), f"no tables on StockAnalysis's {page or 'overview'} page")
    return tables


def _free(rows: list[list[str]]) -> list[list[str]]:
    """Cells behind the paid plan as a dash, and columns with nothing else dropped."""
    paid = [[cell.strip().lower() in _PAID for cell in row] for row in rows]
    width = max((len(row) for row in rows), default=0)
    keep = [column for column in range(width)
            if column == 0 or not all(row[column] for row in paid[1:] if column < len(row))]
    return [[("—" if marks[column] else row[column]) for column in keep if column < len(row)]
            for row, marks in zip(rows, paid, strict=True)]


def _markdown(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    width = max(len(row) for row in rows)
    head = rows[0] + [""] * (width - len(rows[0]))
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(row + [""] * (width - len(row))) + " |" for row in rows[1:]]
    return lines


def get_fundamentals(
    ticker: Annotated[str, "ticker symbol of the company"],
    as_of_date: Annotated[str, "analysis date in YYYY-MM-DD format"] = None,
) -> str:
    """The statistics page as labelled figures, for a run dated today."""
    canonical = normalize_symbol(ticker)
    withheld = withhold_live_profile(as_of_date, canonical)
    if withheld:
        return withheld
    lines = [f"# Company Fundamentals for {canonical}", SOURCE, ""]
    for heading, rows in _tables_for(ticker, "statistics"):
        if heading not in OVERVIEW_SECTIONS:
            continue
        figures = [f"{row[0]}: {row[1]}" for row in rows if len(row) >= 2 and row[1] not in ("n/a", "-", "")]
        if figures:
            lines += [f"## {heading}", *figures, ""]
    if len(lines) <= 3:
        raise NoMarketDataError(ticker, canonical, "no figures on StockAnalysis's statistics page")
    return "\n".join(lines).rstrip() + "\n"


def _statement(ticker: str, freq: str, as_of_date: str | None, kind: str, title: str) -> str:
    canonical = normalize_symbol(ticker)
    withheld = withhold_undated_statements(as_of_date, canonical, title)
    if withheld:
        return withheld
    quarterly = (freq or "quarterly").lower() == "quarterly"
    tables = _tables_for(ticker, STATEMENTS[kind], "?p=quarterly" if quarterly else "")
    heading, rows = tables[0]
    # The latest two years: older quarters cost tokens and say little about the next one.
    rows = _free([row[:1 + MAX_PERIODS] for row in rows if row and row[0] != "Period Ending"])
    if len(rows) < 2:
        raise NoMarketDataError(ticker, canonical, f"no {title.lower()} rows on StockAnalysis")
    return "\n".join([f"# {title} data for {canonical} ({'quarterly' if quarterly else 'annual'})", SOURCE,
                      "Figures in millions of the reporting currency unless marked; margins and growth in %.", "",
                      *_markdown(rows)]) + "\n"


def get_income_statement(ticker: Annotated[str, "ticker symbol"], freq: Annotated[str, "annual or quarterly"] = "quarterly",
                         as_of_date: Annotated[str, "analysis date, YYYY-MM-DD"] = None) -> str:
    return _statement(ticker, freq, as_of_date, "income", "Income Statement")


def get_balance_sheet(ticker: Annotated[str, "ticker symbol"], freq: Annotated[str, "annual or quarterly"] = "quarterly",
                      as_of_date: Annotated[str, "analysis date, YYYY-MM-DD"] = None) -> str:
    return _statement(ticker, freq, as_of_date, "balance", "Balance Sheet")


def get_cashflow(ticker: Annotated[str, "ticker symbol"], freq: Annotated[str, "annual or quarterly"] = "quarterly",
                 as_of_date: Annotated[str, "analysis date, YYYY-MM-DD"] = None) -> str:
    return _statement(ticker, freq, as_of_date, "cashflow", "Cash Flow")


def get_analyst_coverage(ticker: str, as_of_date: str | None = None) -> str | None:
    """The forecast page as markdown for a run dated today; None for a past date.

    The page is today's: its targets and trend months are not dated, so a past
    run is left to the dated Yahoo records instead.
    """
    if is_historical(as_of_date):
        return None
    canonical = normalize_symbol(ticker)
    wanted = {"Price Target": "Price target", "Recommendation Trends": "Recommendation trend by month",
              "Latest Forecasts": "Latest analyst actions", "Financial Forecast": "Financials and forecasts by fiscal year",
              "Revenue Forecast": "Revenue forecast", "EPS Forecast": "EPS forecast"}
    lines = ["### Analyst coverage from StockAnalysis (Yahoo did not answer)", SOURCE, ""]
    for heading, rows in _tables_for(ticker, "forecast"):
        if heading not in wanted:
            continue
        if heading == "Latest Forecasts":
            # Analyst, firm, rating, action, target, upside, date: the analyst column repeats the firm.
            rows = [[row[1], row[3], row[4], row[5], row[7] if len(row) > 7 else ""] for row in rows if len(row) >= 6]
            rows[0] = ["Firm", "Rating", "Action", "Price target", "Date"]
        rows = [row for row in _free(rows) if len(row) > 1]
        if len(rows) > 1:
            lines += [f"#### {wanted[heading]}", *_markdown(rows), ""]
    if len(lines) <= 3:
        raise NoMarketDataError(ticker, canonical, "no analyst figures on StockAnalysis's forecast page")
    return "\n".join(lines).rstrip() + "\n"
