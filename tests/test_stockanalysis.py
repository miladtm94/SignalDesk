"""StockAnalysis as the last vendor: polite, today-only, and used only when the others fail.

Its terms allow automated access only to crawlers that follow its robots.txt,
so a disallowed path is never fetched, requests are spaced and cached, and
cells behind its paid plan stay dashes. Pages are today's, so a past run is
withheld rather than leaked.
"""
from __future__ import annotations

import pytest

from tradingagents.agents import signaldesk_tools
from tradingagents.dataflows import router
from tradingagents.dataflows.config import run_config
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.vendors import stockanalysis as sa
from tradingagents.dataflows.vendors.yahoo import street

STATISTICS = """<html><body><h2>Total Valuation</h2><table><tbody>
<tr><td><span>Market Cap</span><span class="tip">Market Cap help</span></td><td>31.76B</td></tr>
<tr><td>Enterprise Value</td><td>29.51B</td></tr></tbody></table>
<h2>Valuation Ratios</h2><table><tr><td>PE Ratio</td><td>n/a</td></tr><tr><td>PS Ratio</td><td>41.29</td></tr></table>
<h2>Fair Value</h2><table><tr><td>Graham Number</td><td>12</td></tr></table></body></html>"""
INCOME = """<h1>Income Statement</h1><table>
<tr><th>Fiscal Quarter</th><th>Q2 2026</th><th>Q1 2026</th><th>Q4 2025</th></tr>
<tr><th>Period Ending</th><th>Jun 30, 2026</th><th>Mar 31, 2026</th><th>Dec 31, 2025</th></tr>
<tr><td><div>Revenue</div><div>Revenue Growth</div></td><td>234.07</td><td>200.35</td><td>Pro</td></tr>
<tr><td>Net Income</td><td>-57.5</td><td>-44.8</td><td>Pro</td></tr></table>"""
FORECAST = """<h2>Price Target</h2><table><tr><th>Target</th><th>Low</th><th>Average</th></tr>
<tr><td>Price</td><td>$64</td><td>$102.15</td></tr></table>
<h2>Latest Forecasts</h2><table>
<tr><th>Analyst</th><th>Firm</th><th>Rating</th><th>Rating</th><th>Action</th><th>Price Target</th><th>Upside</th><th>Date</th></tr>
<tr><td>K. Liwag Morgan Stanley</td><td>Morgan Stanley</td><td>Buy Reiterates $105</td><td>Buy</td><td>Reiterates</td><td>$105</td><td>+97%</td><td>Nov 6, 2026</td></tr>
</table>"""
TODAY = "2026-11-09"


class _Response:
    def __init__(self, text="", status=200):
        self.text, self.status_code = text, status


@pytest.fixture
def site(monkeypatch, tmp_path):
    """A fake stockanalysis.com: robots.txt plus the pages a test serves."""
    pages: dict[str, str] = {}
    asked: list[str] = []
    robots = {"text": "User-agent: *\nDisallow: /e/\nDisallow: /p/\n"}

    def get(url, headers, timeout):
        assert "SignalDesk" in headers["User-Agent"]
        path = url.removeprefix(sa.BASE_URL)
        asked.append(path)
        if path == "/robots.txt":
            return _Response(robots["text"])
        return _Response(pages[path]) if path in pages else _Response("", 404)

    monkeypatch.setattr(sa.requests, "get", get)
    monkeypatch.setattr(sa.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(sa, "_robots", None)
    monkeypatch.setattr(sa, "is_historical", lambda day: bool(day) and day < TODAY)
    monkeypatch.setattr("tradingagents.dataflows.date_window.get_current_date", lambda: TODAY)
    with run_config({"data_cache_dir": str(tmp_path)}):
        yield pages, asked, robots


def test_paths_follow_the_site_s_listing_scheme():
    assert sa.page_path("RKLB", "statistics") == "/stocks/rklb/statistics/"
    assert sa.page_path("BRK-B", "forecast") == "/stocks/brk.b/forecast/"
    assert sa.page_path("NXT.AX", "statistics") == "/quote/asx/NXT/statistics/"
    for symbol in ("BTC-USD", "^GSPC", "GC=F", "ABC.XX"):
        with pytest.raises(NoMarketDataError):
            sa.page_path(symbol, "statistics")


def test_the_overview_reads_labelled_figures_and_names_its_source(site):
    pages, asked, _ = site
    pages["/stocks/rklb/statistics/"] = STATISTICS
    text = sa.get_fundamentals("RKLB", TODAY)
    assert "Market Cap: 31.76B" in text and "PS Ratio: 41.29" in text
    assert "Market Cap help" not in text and "PE Ratio" not in text  # tooltip and n/a left out
    assert "Graham" not in text and sa.SOURCE in text
    sa.get_fundamentals("RKLB", TODAY)
    assert asked == ["/robots.txt", "/stocks/rklb/statistics/"]  # the second read came from the cache


def test_a_past_run_is_withheld_without_a_request(site):
    _, asked, _ = site
    assert "withheld" in sa.get_fundamentals("RKLB", "2026-08-03")
    assert "withheld" in sa.get_income_statement("RKLB", "quarterly", "2026-08-03").lower()
    assert sa.get_analyst_coverage("RKLB", "2026-08-03") is None
    assert asked == []


def test_a_path_robots_txt_disallows_is_never_fetched(site):
    pages, asked, robots = site
    robots["text"] = "User-agent: *\nDisallow: /stocks/\n"
    pages["/stocks/rklb/statistics/"] = STATISTICS
    with pytest.raises(VendorUnavailableError, match="robots.txt"):
        sa.get_fundamentals("RKLB", TODAY)
    assert asked == ["/robots.txt"]


def test_paid_cells_are_dashes_and_a_column_of_them_is_dropped(site):
    pages, _, _ = site
    pages["/stocks/rklb/financials/income-statement/?p=quarterly"] = INCOME
    text = sa.get_income_statement("RKLB", "quarterly", TODAY)
    assert "| Fiscal Quarter | Q2 2026 | Q1 2026 |" in text and "Q4 2025" not in text
    assert "| Revenue | 234.07 | 200.35 |" in text and "Revenue Growth" not in text
    assert "Period Ending" not in text and "Pro" not in text


def test_a_missing_page_is_no_data_for_the_router_to_pass_over(site):
    with pytest.raises(NoMarketDataError):
        sa.get_fundamentals("ZZZZ", TODAY)


def test_the_router_reaches_stockanalysis_only_after_yahoo_fails(site, monkeypatch):
    pages, asked, _ = site
    pages["/stocks/rklb/statistics/"] = STATISTICS

    def yahoo_down(*args, **kwargs):
        raise VendorUnavailableError("Yahoo Finance request failed")

    monkeypatch.setitem(router.VENDOR_METHODS["get_fundamentals"], "yfinance", lambda *a, **k: "# Yahoo overview")
    with run_config({"data_vendors": {"fundamental_data": "sec_edgar,yfinance,stockanalysis"}}):
        assert router.route_to_vendor("get_fundamentals", "RKLB", TODAY) == "# Yahoo overview"
        assert asked == []
        monkeypatch.setitem(router.VENDOR_METHODS["get_fundamentals"], "yfinance", yahoo_down)
        assert "Market Cap: 31.76B" in router.route_to_vendor("get_fundamentals", "RKLB", TODAY)


def test_analyst_coverage_falls_back_when_yahoo_is_down_today_only(site, monkeypatch):
    pages, _, _ = site
    pages["/stocks/rklb/forecast/"] = FORECAST

    def down(*args, **kwargs):
        raise VendorUnavailableError("Yahoo is down")

    monkeypatch.setattr(signaldesk_tools, "yahoo_analyst_coverage", down)
    text = signaldesk_tools.get_analyst_coverage.func("RKLB", TODAY, trade_date=TODAY)
    assert "Analyst coverage from StockAnalysis" in text
    assert "| Morgan Stanley | Buy | Reiterates | $105 | Nov 6, 2026 |" in text
    past = signaldesk_tools.get_analyst_coverage.func("RKLB", "2026-08-03", trade_date="2026-08-03")
    assert past.startswith("DATA_UNAVAILABLE")


def test_yahoo_with_nothing_to_say_is_filled_in_from_stockanalysis(site, monkeypatch):
    pages, _, _ = site
    pages["/stocks/rklb/forecast/"] = FORECAST
    monkeypatch.setattr(street, "_fetch", lambda canonical, attribute: None)
    monkeypatch.setattr(street, "get_current_date", lambda: TODAY)
    text = signaldesk_tools.get_analyst_coverage.func("RKLB", TODAY, trade_date=TODAY)
    assert text.startswith("### Analyst coverage from StockAnalysis")
