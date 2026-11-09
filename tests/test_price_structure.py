"""Weekly and monthly price structure: zones, trend and the stance for new money.

The zones are what the market analyst is told to set levels against and what
the console's chart shades, so they must come from fixed rules, use only bars
up to the read's date, and turn trend and location into a stance that is not
always a buy or a sell.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from tradingagents.agents import signaldesk_tools
from tradingagents.dataflows import price_structure as ps


def _daily(slope: float, start="2021-11-01", end="2026-11-06", base=100.0, swing=8.0, period=60):
    """Business-day bars: a trend plus a regular swing, so pivots are known."""
    days = pd.bdate_range(start, end)
    rows = []
    for t, day in enumerate(days):
        mid = base + slope * t + swing * math.sin(2 * math.pi * t / period)
        rows.append({"Date": day, "Open": mid - 0.2, "High": mid + 1.0, "Low": mid - 1.0, "Close": mid + 0.2,
                     "Volume": 1e6})
    return pd.DataFrame(rows)


@pytest.mark.unit
def test_weekly_bars_are_labelled_by_their_first_session_and_span_the_week():
    daily = ps.daily_frame(_daily(0.1, start="2026-10-05", end="2026-10-16"))
    weekly = ps.resample(daily, "weekly")
    assert [t.strftime("%Y-%m-%d") for t in weekly["time"]] == ["2026-10-05", "2026-10-12"]
    first_week = daily[daily["Date"] <= "2026-10-09"]
    assert weekly.at[0, "high"] == first_week["High"].max()
    assert weekly.at[0, "close"] == first_week["Close"].iloc[-1]
    assert weekly.at[0, "open"] == first_week["Open"].iloc[0]


@pytest.mark.unit
def test_a_rising_market_reads_as_an_uptrend_with_zones_either_side():
    read = ps.read_structure(_daily(0.08))
    weekly = read["timeframes"]["weekly"]
    assert weekly["trend"] == "up"
    assert "higher highs and higher lows" in weekly["structure_text"]
    assert weekly["plan"]["stance"] != "avoid"
    for zone in read["zones"]:
        assert zone["low"] < zone["high"]
        assert (zone["mid"] < read["close"]) == (zone["kind"] == "support") or zone["contains_price"]
    # The invalidation sits under the buy zone it protects.
    plan = weekly["plan"]
    if plan["buy_zone"]:
        assert plan["invalidation"]["price"] < plan["buy_zone"]["low"]


@pytest.mark.unit
def test_a_falling_market_is_avoided_until_it_reclaims_the_slow_average():
    read = ps.read_structure(_daily(-0.03, base=200))
    weekly = read["timeframes"]["weekly"]
    assert weekly["trend"] == "down"
    plan = weekly["plan"]
    assert plan["stance"] == "avoid"
    assert plan["buy_zone"] is None and plan["invalidation"] is None
    assert plan["trigger"]["price"] == pytest.approx(weekly["sma_slow"])
    assert "40-week SMA" in plan["trigger"]["why"]


@pytest.mark.unit
def test_a_past_read_is_the_same_whatever_traded_after_it():
    full = _daily(0.08)
    cut = "2025-06-13"
    assert ps.read_structure(full, cut) == ps.read_structure(full[full["Date"] <= cut])


@pytest.mark.unit
def test_a_swing_counts_only_once_closed_bars_confirm_it():
    daily = ps.daily_frame(_daily(0.0, swing=10, period=40))
    weekly = ps.resample(daily, "weekly")
    points = ps.swing_points(weekly, "weekly")
    width = ps.TIMEFRAMES["weekly"]["width"]
    newest_confirmable = weekly.at[len(weekly) - 2 - width, "time"].strftime("%Y-%m-%d")
    assert points and max(p["date"] for p in points) <= newest_confirmable


def _zone(kind, low, high, close):
    middle = (low + high) / 2
    return {"kind": kind, "low": low, "high": high, "mid": middle, "touches": 2, "timeframes": ["weekly"],
            "last_date": "2026-09-01", "contains_price": low <= close <= high}


def _stance(trend, close, rsi=55.0, zones=None, fast=None, slow=None, fast_rising=None, atr=4.0):
    zones = zones if zones is not None else [_zone("support", 95, 99, close), _zone("resistance", 115, 119, close)]
    return ps._plan("weekly", close, atr, rsi, trend, fast, slow, zones, fast_rising=fast_rising)


@pytest.mark.unit
@pytest.mark.parametrize(("trend", "close", "rsi", "stance"), [
    ("up", 98, 55, "buy_zone"),          # at support in an uptrend
    ("up", 100.5, 55, "buy_zone"),       # just above the zone, within half an ATR
    ("up", 107, 55, "wait_pullback"),    # uptrend, between the zones
    ("up", 113.5, 60, "wait_breakout"),  # pressing resistance in an uptrend
    ("up", 114, 72, "take_profit"),      # at resistance and overbought
    ("up", 112, 80, "wait_pullback"),    # extended
    ("range", 116, 55, "take_profit"),   # top of a range
    ("range", 107, 50, "hold"),          # middle of a range: no edge
    ("range", 97, 45, "buy_zone"),       # bottom of a range, momentum turned (see below)
    ("down", 97, 40, "avoid"),           # a downtrend is not bought at support
])
def test_trend_and_location_give_the_stance(trend, close, rsi, stance):
    plan = _stance(trend, close, rsi, slow=125 if trend == "down" else 100, fast=96, fast_rising=True)
    assert plan["stance"] == stance
    assert plan["label"] == ps.STANCES[stance]
    assert plan["new_money"] and plan["holders"]


@pytest.mark.unit
def test_without_resistance_above_the_targets_are_projected_and_say_so():
    plan = _stance("up", 98, zones=[_zone("support", 95, 99, 98)])
    assert plan["targets_projected"]
    assert all("projected" in target["why"] for target in plan["targets"])
    assert plan["targets"][0]["low"] > 98
    assert plan["trigger"] is None  # no resistance, no breakout level


@pytest.mark.unit
def test_a_zone_the_price_fell_into_is_support_and_one_it_rose_into_resistance():
    points = [{"kind": "low", "price": 100.0, "date": "2026-01-05", "timeframe": "weekly"},
              {"kind": "high", "price": 101.0, "date": "2026-03-02", "timeframe": "weekly"}]
    fell = ps.cluster_zones(points, 100.8, 4.0, approach=110.0)
    rose = ps.cluster_zones(points, 100.8, 4.0, approach=90.0)
    assert [z["kind"] for z in fell] == ["support"] and fell[0]["contains_price"]
    assert [z["kind"] for z in rose] == ["resistance"]


@pytest.mark.unit
def test_the_report_names_the_stance_zones_and_rules():
    read = ps.read_structure(_daily(0.08))
    text = ps.render_price_structure("TEST", read)
    assert "### Swing view (weekly bars" in text and "### Investor view (monthly bars" in text
    assert "**Stance:" in text and "New money:" in text and "Holders:" in text
    assert "| Zone | Range |" in text
    assert "fixed rules" in text


@pytest.mark.unit
def test_the_tool_never_reads_past_the_run_date(monkeypatch):
    seen = {}
    monkeypatch.setattr(signaldesk_tools, "build_price_structure",
                        lambda symbol, day: seen.setdefault("call", (symbol, day)) and "ok")
    signaldesk_tools.get_price_structure.func("NVDA", "2026-11-06", trade_date="2026-08-04")
    assert seen["call"] == ("NVDA", "2026-08-04")


@pytest.mark.unit
def test_bars_for_the_chart_carry_each_timeframe_s_averages():
    bars = ps.chart_bars(_daily(0.08))
    weekly, monthly = bars["weekly"], bars["monthly"]
    assert weekly["slow"] == 40 and monthly["slow"] == 20
    assert len(weekly["candles"]) > 200 and len(monthly["candles"]) > 50
    assert weekly["series"]["sma_slow"][0]["time"] == weekly["candles"][39]["time"]


@pytest.mark.unit
def test_support_in_a_correction_waits_for_momentum_to_turn():
    # Below a still-rising 40-week line with the 10-week falling: a stock still being sold.
    falling = _stance("range", 97, 40, fast=104, slow=110, fast_rising=False)
    assert falling["stance"] == "wait_breakout"
    assert falling["trigger"]["price"] == 104 and "10-week SMA" in falling["trigger"]["why"]
    assert "do not add" in falling["holders"]
    # Rising again but still under it: not yet.
    assert _stance("range", 97, 45, fast=98, fast_rising=True)["stance"] == "wait_breakout"
    # In an uptrend a pullback to support is bought whatever the 10-week does.
    assert _stance("up", 97, 45, fast=104, slow=90, fast_rising=False)["stance"] == "buy_zone"


@pytest.mark.unit
def test_just_above_the_zone_is_at_most_three_percent_of_the_price():
    # A weekly ATR of 16 on a ~100 stock: half an ATR would call 107 "in" a zone topping at 99.
    zones = [_zone("support", 95, 99, 104), _zone("resistance", 125, 130, 104)]
    assert _stance("up", 104, zones=zones, slow=90, atr=16)["stance"] != "buy_zone"
    assert _stance("up", 101.5, zones=zones, slow=90, atr=16)["stance"] == "buy_zone"
