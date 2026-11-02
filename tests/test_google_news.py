"""Google News search serves a past run the headlines published in its own window."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import requests

from tradingagents.dataflows import router
from tradingagents.dataflows.config import get_config, set_config
from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.vendors import google_news as gn


def _feed(*items) -> bytes:
    rows = "".join(
        f"<item><title>{title} - {source}</title><source url='https://x'>{source}</source>"
        f"<pubDate>{published}</pubDate></item>"
        for title, source, published in items
    )
    return f"<?xml version='1.0'?><rss><channel><title>t</title>{rows}</channel></rss>".encode()


def _many(count: int, published: str = "Thu, 23 Jul 2026 07:00:00 GMT") -> bytes:
    return _feed(*[(f"FCX story {i}", "Wire", published) for i in range(count)])


JULY = _feed(
    ("Freeport-McMoRan's Q2 profit beats expectations, helped by rising copper prices", "Reuters",
     "Thu, 23 Jul 2026 07:00:00 GMT"),
    ("FCX Stock Falls Amid Soft Q3 Outlook As Copper Price Slips", "Investor's Business Daily",
     "Thu, 23 Jul 2026 07:00:00 GMT"),
    ("FCX Stock Falls Amid Soft Q3 Outlook, As Copper Price Slips", "Syndicated", "Thu, 23 Jul 2026 09:00:00 GMT"),
    ("Freeport shares hit a 52-week high", "Reuters", "Mon, 03 Aug 2026 07:00:00 GMT"),   # after the run
    ("Freeport earnings preview", "Barron's", "Mon, 20 Jul 2026 07:00:00 GMT"),        # before the window
    ("Undated item", "Somewhere", "not a date"),
)
EMPTY = _feed()


class _Response:
    def __init__(self, status: int, body: bytes = b""):
        self.status_code, self.content = status, body


OK_EMPTY = _Response(200, EMPTY)


@pytest.fixture
def google(monkeypatch, tmp_path):
    """Answers each search from its script (the last answer repeats); records the requests."""
    saved = dict(get_config())
    set_config({"data_cache_dir": str(tmp_path)})
    monkeypatch.setattr(gn, "_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(gn.time, "sleep", lambda s: None)
    monkeypatch.setattr(gn, "_profile_name", lambda canonical: {
        "FCX": "Freeport-McMoRan Inc.", "MU": "Micron Technology, Inc.", "SPCX": "Space Exploration Technologies Corp.",
        "CSL.AX": "CSL Limited", "BTC-USD": "Bitcoin USD", "ON": "ON Semiconductor Corporation",
    }.get(canonical))
    state = {"scripts": {"*": [OK_EMPTY]}, "queries": []}

    def fake_get(url, params=None, timeout=None, headers=None):
        state["queries"].append(params["q"])
        query = params["q"].split(" after:")[0]
        script = state["scripts"].get(query, state["scripts"]["*"])
        answer = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(gn.requests, "get", fake_get)
    yield state
    set_config(saved)


@pytest.mark.unit
@pytest.mark.parametrize("legal, core", [
    ("Freeport-McMoRan Inc.", "Freeport-McMoRan"),
    ("Micron Technology, Inc.", "Micron"),
    ("Nebius Group N.V.", "Nebius"),
    ("Rocket Lab USA, Inc.", "Rocket Lab"),
    ("Alphabet Inc.", "Alphabet"),
    ("CSL Limited", "CSL"),
    ("Bitcoin USD", "Bitcoin"),
    ("The Coca-Cola Company", "Coca-Cola"),
    ("Taiwan Semiconductor Manufacturing Company Limited", "Taiwan Semiconductor Manufacturing"),
    ("Gold Jun 26", "Gold"),
    ("Inc.", "Inc"),
    (None, None),
])
def test_a_legal_name_is_searched_as_the_name_news_uses(legal, core):
    assert gn.search_name(legal) == core


@pytest.mark.unit
def test_the_ticker_is_searched_first_and_the_name_only_tops_up(google):
    assert gn.company_queries("FCX") == (['"FCX"', '"Freeport-McMoRan"'], "Freeport-McMoRan (FCX)")
    assert gn.company_queries("SPCX")[0] == ['"SPCX"', '"Space Exploration"']
    assert gn.company_queries("ON")[0][0] == '"ON stock"'
    assert gn.company_queries("CSL.AX")[0] == ['"ASX:CSL"', '"CSL"']
    assert gn.company_queries("BTC-USD")[0] == ['"Bitcoin"']


@pytest.mark.unit
def test_a_past_run_reads_the_headlines_of_its_own_window(google):
    google["scripts"]['"FCX"'] = [_Response(200, JULY)]
    out = gn.get_news_google("FCX", "2026-07-23", "2026-07-30")

    assert "Q2 profit beats expectations, helped by rising copper prices" in out
    assert "2026-07-23 · Reuters ·" in out
    assert "52-week high" not in out           # published after the run date
    assert "earnings preview" not in out       # before the window
    assert "Undated" not in out
    assert out.count("Soft Q3 Outlook") == 1   # one story, two spellings
    assert google["queries"][0] == '"FCX" after:2026-07-22 before:2026-08-01'


@pytest.mark.unit
def test_the_name_search_is_skipped_once_the_ticker_fills_the_report(google):
    google["scripts"]['"FCX"'] = [_Response(200, _many(25))]
    out = gn.get_news_google("FCX", "2026-07-23", "2026-07-30")
    assert google["queries"] == ['"FCX" after:2026-07-22 before:2026-08-01']
    assert out.count("\n- ") == get_config()["news_article_limit"]


@pytest.mark.unit
def test_a_thin_answer_is_asked_again_and_the_fuller_one_kept(google):
    google["scripts"]['"FCX"'] = [_Response(200, _many(6)), _Response(200, _many(40))]
    assert len(gn.search('"FCX"', "2026-07-23", "2026-07-30")) == 40
    google["scripts"]['"MU"'] = [_Response(200, _many(3))]
    assert len(gn.search('"MU"', "2026-07-23", "2026-07-30")) == 3   # a quiet week stays quiet


@pytest.mark.unit
def test_a_settled_window_is_read_from_the_cache(google):
    google["scripts"]['"FCX"'] = [_Response(200, JULY)]
    first = gn.get_news_google("FCX", "2026-07-23", "2026-07-30")
    asked = len(google["queries"])
    assert gn.get_news_google("FCX", "2026-07-23", "2026-07-30") == first
    assert len(google["queries"]) == asked   # the second run made no request


@pytest.mark.unit
def test_a_window_reaching_today_is_refetched_after_the_live_ttl(google, monkeypatch):
    now = datetime.now(UTC)
    today, week = now.strftime("%Y-%m-%d"), (now - timedelta(days=7)).strftime("%Y-%m-%d")
    google["scripts"]['"FCX"'] = [_Response(200, _many(12, now.strftime("%a, %d %b %Y %H:%M:%S GMT")))]
    gn.search('"FCX"', week, today)
    gn.search('"FCX"', week, today)
    assert len(google["queries"]) == 1
    clock = gn.time.time() + gn.LIVE_CACHE_SECONDS + 1
    monkeypatch.setattr(gn.time, "time", lambda: clock)
    gn.search('"FCX"', week, today)
    assert len(google["queries"]) == 2


@pytest.mark.unit
def test_an_asx_listing_is_searched_in_the_australian_edition(google, monkeypatch):
    editions = []
    real_search = gn.search
    monkeypatch.setattr(gn, "search", lambda *a: editions.append(a[3]) or real_search(*a))
    gn.get_news_google("CSL.AX", "2026-08-11", "2026-08-18")
    assert editions[0] == ("en-AU", "AU")


@pytest.mark.unit
def test_throttling_is_retried_then_reported_as_an_outage(google):
    google["scripts"]['"FCX"'] = [_Response(429), _Response(503), _Response(200, _many(30))]
    assert "FCX story" in gn.get_news_google("FCX", "2026-07-23", "2026-07-30")

    google["scripts"]["*"] = [_Response(503), _Response(503), requests.ConnectionError("down")]
    with pytest.raises(VendorUnavailableError):
        gn.get_news_google("MU", "2026-07-23", "2026-07-30")


@pytest.mark.unit
def test_a_page_that_is_not_a_feed_is_an_outage(google):
    google["scripts"]["*"] = [_Response(200, b"<html>captcha")]
    with pytest.raises(VendorUnavailableError):
        gn.get_news_google("FCX", "2026-07-23", "2026-07-30")


@pytest.mark.unit
def test_the_name_search_failing_keeps_what_the_ticker_found(google):
    google["scripts"]['"FCX"'] = [_Response(200, JULY)]
    google["scripts"]['"Freeport-McMoRan"'] = [_Response(503)]
    assert "Q2 profit beats" in gn.get_news_google("FCX", "2026-07-23", "2026-07-30")


@pytest.mark.unit
def test_no_headlines_is_reported_as_none_found_not_as_an_outage(google):
    out = gn.get_news_google("FCX", "2026-07-23", "2026-07-30")
    assert out.startswith("No headlines about Freeport-McMoRan (FCX) published between 2026-07-23 and 2026-07-30")


@pytest.mark.unit
def test_global_news_covers_each_topic_within_the_window(google):
    fed = _feed(("Fed holds rates with three dissents", "AP News", "Wed, 29 Jul 2026 18:00:00 GMT"),
                ("Fed cuts rates", "AP News", "Wed, 05 Aug 2026 18:00:00 GMT"))
    saved = get_config()["global_news_queries"]
    set_config({"global_news_queries": ["Federal Reserve", "oil"]})
    google["scripts"]["Federal Reserve"] = [_Response(200, fed)]
    google["scripts"]["oil"] = [_Response(503)]
    try:
        out = gn.get_global_news_google("2026-07-30", 7, 10)
    finally:
        set_config({"global_news_queries": saved})
    assert "### Federal Reserve" in out and "three dissents" in out
    assert "Fed cuts rates" not in out
    assert "### oil" not in out   # that topic's search failed; the others still serve


@pytest.mark.unit
def test_a_google_outage_moves_news_to_yahoo(google):
    google["scripts"]["*"] = [_Response(503)]
    saved = dict(get_config()["data_vendors"])
    set_config({"data_vendors": {**saved, "news_data": "google_news,yfinance"}})
    try:
        with patch.dict(router.VENDOR_METHODS, {"get_news": {
            "google_news": gn.get_news_google, "yfinance": lambda *a, **k: "YAHOO NEWS",
        }}):
            out = router.route_to_vendor("get_news", "FCX", "2026-07-23", "2026-07-30")
    finally:
        set_config({"data_vendors": saved})
    assert out == "YAHOO NEWS"


@pytest.mark.unit
def test_news_comes_from_google_first_by_default():
    from tradingagents.default_config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["data_vendors"]["news_data"].split(",")[0] == "google_news"
    assert "google_news" in router.VENDOR_METHODS["get_news"]
    assert "google_news" in router.VENDOR_METHODS["get_global_news"]
