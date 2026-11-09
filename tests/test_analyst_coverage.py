"""Analyst coverage: what the sell side thinks, served as it was known on the run date.

Rating changes and reported quarters are dated, so a past run sees the ones made
by then (and a report after the close belongs to the next session); consensus,
estimates, short interest and ownership are today's snapshot and are withheld
for a past date. One unreadable section must not cost the others.
"""
from __future__ import annotations

import pandas as pd
import pytest

from tradingagents.agents import signaldesk_tools
from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.vendors.yahoo import street

RATINGS = pd.DataFrame(
    {
        "Firm": ["Late Bank", "Alpha", "Beta", "Alpha", "Old Co"],
        "ToGrade": ["Buy", "Buy", "Hold", "Neutral", "Sell"],
        "FromGrade": ["", "Neutral", "Buy", "", ""],
        "Action": ["init", "up", "down", "main", "main"],
        "priceTargetAction": ["Announces", "Raises", "Lowers", "Maintains", "Maintains"],
        "currentPriceTarget": [500.0, 240.0, 180.0, 200.0, 90.0],
        "priorPriceTarget": [0.0, 200.0, 210.0, 200.0, 90.0],
    },
    index=pd.DatetimeIndex(["2026-08-20 13:00", "2026-08-03 12:00", "2026-07-15 12:00",
                            "2026-06-01 12:00", "2025-09-01 12:00"], name="GradeDate"),
)
EARNINGS = pd.DataFrame(
    {"EPS Estimate": [0.52, 0.41, 0.35, 0.28], "Reported EPS": [float("nan"), 0.44, 0.41, 0.30],
     "Surprise(%)": [float("nan"), 7.3, 17.1, 7.1]},
    index=pd.DatetimeIndex(["2027-02-01 16:00", "2026-11-02 16:00", "2026-08-03 16:00", "2026-05-04 08:00"],
                           tz="America/New_York", name="Earnings Date"),
)
LIVE = {
    "info": {"numberOfAnalystOpinions": 28, "averageAnalystRating": "1.8 - Buy", "shortPercentOfFloat": 0.026,
             "sharesShort": 56e6, "sharesShortPriorMonth": 60e6, "shortRatio": 2.4,
             "heldPercentInstitutions": 0.65, "heldPercentInsiders": 0.03, "floatShares": 2.1e9,
             "enterpriseValue": 3.7e11, "enterpriseToEbitda": 112.1, "revenueGrowth": 0.96},
    "analyst_price_targets": {"current": 160.0, "mean": 222.0, "median": 235.0, "low": 80.0, "high": 273.0},
    "recommendations_summary": pd.DataFrame({"period": ["0m"], "strongBuy": [3], "buy": [19], "hold": [9],
                                             "sell": [1], "strongSell": [1]}),
    "earnings_estimate": pd.DataFrame({"avg": [0.52], "low": [0.5], "high": [0.59], "yearAgoEps": [0.25],
                                       "numberOfAnalysts": [24], "growth": [1.08]}, index=["+1q"]),
    "revenue_estimate": pd.DataFrame({"avg": [2.7e9], "growth": [0.66]}, index=["+1q"]),
    "eps_trend": pd.DataFrame({"current": [0.52], "30daysAgo": [0.48], "90daysAgo": [0.47]}, index=["+1q"]),
    "eps_revisions": pd.DataFrame({"upLast30days": [1], "downLast30days": [1]}, index=["+1q"]),
}


def _yahoo(monkeypatch, answers, today="2026-11-09"):
    asked = []

    def fetch(canonical, attribute):
        asked.append(attribute)
        value = answers.get(attribute)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(street, "_fetch", fetch)
    monkeypatch.setattr(street, "get_current_date", lambda: today)
    monkeypatch.setattr("tradingagents.dataflows.date_window.get_current_date", lambda: today)
    return asked


@pytest.mark.unit
def test_a_past_run_sees_only_the_ratings_and_quarters_known_by_its_date(monkeypatch):
    asked = _yahoo(monkeypatch, {"upgrades_downgrades": RATINGS, "earnings_dates": EARNINGS, **LIVE})
    text = street.get_analyst_coverage("PLTR", "2026-08-03")
    assert "Late Bank" not in text and "500.00" not in text  # rated after the run date
    # Each firm's latest target in the 180 days to the date: Alpha 240, Beta 180.
    assert "2 firm(s), each at its latest target: median 210.00" in text
    assert "Old Co" not in text  # outside the window
    # Released at 16:00 on the run date: the next session's news.
    assert "| 2026-08-03 | 0.35 |" not in text and "| 2026-05-04 | 0.28 | 0.30 | +7.1% |" in text
    assert "Withheld for 2026-08-03" in text
    assert "Consensus today" not in text and "Next earnings" not in text
    assert not {"info", "analyst_price_targets", "earnings_estimate"} & set(asked)


@pytest.mark.unit
def test_a_report_the_morning_after_counts_for_the_next_day(monkeypatch):
    _yahoo(monkeypatch, {"upgrades_downgrades": None, "earnings_dates": EARNINGS})
    assert "| 2026-08-03 | 0.35 | 0.41 | +17.1% |" in street.get_analyst_coverage("PLTR", "2026-08-04")


@pytest.mark.unit
def test_a_run_dated_today_gets_the_consensus_estimates_and_positioning(monkeypatch):
    _yahoo(monkeypatch, {"upgrades_downgrades": RATINGS, "earnings_dates": EARNINGS, **LIVE})
    text = street.get_analyst_coverage("PLTR", "2026-11-09")
    assert "Price target: mean 222.00 (+38.7% from 160.00)" in text
    assert "| This month | 3 | 19 | 9 | 1 | 1 |" in text
    assert "| Next quarter | 0.52 (0.50–0.59) | +108.0% | 2.70B | +66.0% | 24 |" in text
    assert "**Next earnings: 2027-02-01** (in 84 days), EPS estimate 0.52" in text
    assert "Short interest: 2.60% of the float" in text and "-6.7% on the month before" in text
    assert "Withheld" not in text


@pytest.mark.unit
def test_one_section_that_fails_leaves_the_others(monkeypatch):
    _yahoo(monkeypatch, {"upgrades_downgrades": VendorUnavailableError("down"), "earnings_dates": EARNINGS})
    text = street.get_analyst_coverage("PLTR", "2026-10-01")
    assert "Earnings record" in text and "1 section(s) could not be fetched" in text


@pytest.mark.unit
def test_nothing_known_says_so_and_nothing_reachable_is_an_outage(monkeypatch):
    _yahoo(monkeypatch, {})
    assert street.get_analyst_coverage("SPY", "2026-10-01").startswith("No analyst coverage found for SPY")
    down = VendorUnavailableError("Yahoo is down")
    _yahoo(monkeypatch, {"upgrades_downgrades": down, "earnings_dates": down})
    with pytest.raises(VendorUnavailableError):
        street.get_analyst_coverage("SPY", "2026-10-01")
    assert signaldesk_tools.get_analyst_coverage.func("SPY", "2026-10-01").startswith("DATA_UNAVAILABLE")


@pytest.mark.unit
def test_a_loss_making_base_year_gives_no_growth_rate():
    assert street._growth({"yearAgoEps": -0.2, "growth": -9.6}) == "n/a"
    assert street._growth({"yearAgoEps": 0.25, "growth": 1.08}) == "+108.0%"
