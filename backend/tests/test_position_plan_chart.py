from __future__ import annotations

import math
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.adapters.tradingagents_adapter import MarketChartData, book_position
from backend.app.api import runs as runs_api
from backend.app.database import SessionLocal
from backend.app.main import app
from backend.app.models import Decision, Run, RunSection
from backend.app.services.charts import plan_levels, plan_tranches, position_plan, strategy_levels
from backend.app.services.technicals import (
    _frame,
    chart_technicals,
    compute_indicators,
    swing_points,
)

# The table the Portfolio Manager's structured decision renders.
FINAL = """**Rating**: Overweight

**Price Target**: 72.0

**Stop Loss**: 58.0

**Position Plan**:

| Tranche | Side | Price | Size | Trigger |
| --- | --- | --- | --- | --- |
| 1 | Buy | 63.3 | 4% of available cash | at the close |
| 2 | Buy | 61.4 | 3% of available cash | pivot support |
| 3 | Sell | 67.8 | 50% of position | resistance |
| 4 | Sell | 72 | 30% of position | target |

Buys commit 7% of available cash."""


def _bar(day: str, open_: float, high: float, low: float, close: float) -> dict:
    return {"time": day, "open": open_, "high": high, "low": low, "close": close, "volume": 1e6}


BARS = [
    _bar("2026-07-30", 63.0, 63.5, 62.9, 63.3),
    _bar("2026-07-31", 63.0, 63.4, 61.2, 62.0),
    _bar("2026-08-03", 62.0, 68.0, 61.9, 67.5),
    _bar("2026-08-04", 67.5, 73.0, 67.0, 72.5),
]


def _plan(markdown: str, bars=BARS, direction="long", book=None):
    sections = [RunSection(section_key="portfolio_manager", content_md=markdown)]
    levels, _ = plan_levels(strategy_levels(None, sections), direction, bars[0]["close"])
    tranches, source = plan_tranches(sections)
    return position_plan(tranches, source, levels, direction, bars, date(2026, 7, 30), book)


def test_the_final_decision_s_table_is_read_as_tranches_before_the_trader_s():
    sections = [RunSection(section_key="trader", content_md="| Side | Price | Size |\n|--|--|--|\n| Buy | 60 | 5% |"),
                RunSection(section_key="portfolio_manager", content_md=FINAL)]
    tranches, source = plan_tranches(sections)
    assert source == "Final decision"
    assert [(t["side"], t["price"], t["size_pct"], t["trigger"]) for t in tranches] == [
        ("buy", 63.3, 4.0, "at the close"), ("buy", 61.4, 3.0, "pivot support"),
        ("sell", 67.8, 50.0, "resistance"), ("sell", 72.0, 30.0, "target")]


def test_a_free_text_table_with_other_words_still_reads():
    text = ("| # | Side (action) | Price ($) | Size (% of cash) | Rationale |\n|---|---|---|---|---|\n"
            "| 1 | Add | $1,250.50 | 40% | breakout |\n| 2 | Trim | 1,400 | 25% of position | prior high |\n"
            "| 3 | Hold | 1,300 | 10% | not a side |\n| 4 | Buy | around | 10% | no price |")
    tranches, _ = plan_tranches([RunSection(section_key="trader", content_md=text)])
    assert [(t["side"], t["price"], t["size_pct"]) for t in tranches] == [("buy", 1250.5, 40.0), ("sell", 1400.0, 25.0)]


def test_the_plan_adds_up_its_risk_and_reward_per_100_of_cash():
    plan = _plan(FINAL, book={"cash": 10_000.0, "currency": "AUD", "units": 0.0})
    summary = plan["summary"]
    units = 4 / 63.3 + 3 / 61.4
    average = 7 / units
    assert summary["committed_pct"] == 7 and summary["sold_pct"] == 80 and summary["remaining_pct"] == 20
    assert math.isclose(summary["average_entry"], average)
    # Every Buy fills, then the stop: the loss on the whole position.
    assert math.isclose(summary["risk_pct"], units * (average - 58))
    assert math.isclose(summary["reward_pct"], 0.5 * units * (67.8 - average) + 0.3 * units * (72 - average))
    assert math.isclose(summary["reward_risk"], summary["reward_pct"] / summary["risk_pct"])
    # Cash amounts come from the book; units would need the instrument's currency.
    assert [t["amount"] for t in plan["tranches"]] == [400.0, 300.0, None, None]
    assert [t["reached"] for t in plan["tranches"]] == ["2026-07-31", "2026-07-31", "2026-08-03", "2026-08-04"]
    assert plan["notes"] == []


def test_the_replay_fills_each_tranche_and_compares_buying_it_all_at_the_close():
    replay = _plan(FINAL)["replay"]
    units = 4 / 63.3 + 3 / 61.4
    left = units * 0.2
    expected = 0.5 * units * 67.8 + 0.3 * units * 72 + left * 72.5 - 7
    assert [fill["number"] for fill in replay["filled"]] == [1, 2, 3, 4] and replay["stopped"] is None
    assert math.isclose(replay["result_pct"], expected)
    assert math.isclose(replay["all_at_close_pct"], 7 * (72.5 / 63.3 - 1))


def test_the_stop_exits_what_is_held_and_ends_the_plan():
    bars = [BARS[0], BARS[1], _bar("2026-08-03", 59.0, 59.5, 56.0, 57.0), _bar("2026-08-04", 57, 70, 57, 69)]
    replay = _plan(FINAL, bars)["replay"]
    units = 4 / 63.3 + 3 / 61.4
    # The 59 open is above the 58 stop, so the stop fills at 58.
    assert replay["stopped"] == {"date": "2026-08-03", "price": 58.0}
    assert [fill["number"] for fill in replay["filled"]] == [1, 2]
    assert math.isclose(replay["result_pct"], units * 58 - 7)


def test_a_buy_that_never_fills_shrinks_the_sells_and_keeps_the_runner():
    replay = _plan(FINAL.replace("| 2 | Buy | 61.4 |", "| 2 | Buy | 60 |"))["replay"]
    units = 4 / 63.3
    assert [fill["number"] for fill in replay["filled"]] == [1, 3, 4]
    assert math.isclose(replay["open_pct"], 0.2 * units * 72.5)
    assert math.isclose(replay["result_pct"], 0.5 * units * 67.8 + 0.3 * units * 72 + 0.2 * units * 72.5 - 4)


def test_a_sale_waits_until_a_buy_has_filled():
    text = FINAL.replace("| 1 | Buy | 63.3 |", "| 1 | Buy | 60 |").replace("| 2 | Buy | 61.4 |", "| 2 | Buy | 59 |")
    replay = _plan(text)["replay"]
    assert replay["filled"] == [] and replay["result_pct"] == 0


def test_a_reduce_plan_is_measured_against_the_position_it_holds():
    text = ("**Stop Loss**: 67.6\n\n| Tranche | Side | Price | Size | Trigger |\n| --- | --- | --- | --- | --- |\n"
            "| 1 | Sell | 65.5 | 50% of position | bounce into resistance |\n| 2 | Sell | 64 | 25% of position | x |")
    plan = _plan(text, direction="reduce", book={"cash": None, "currency": None, "units": 200.0})
    assert plan["summary"]["basis"] == "position" and plan["summary"]["protective_stop"] is False
    assert [t["units"] for t in plan["tranches"]] == [100.0, 50.0]
    assert math.isclose(plan["summary"]["reward_pct"], 50 * (65.5 / 63.3 - 1) + 25 * (64 / 63.3 - 1))
    assert math.isclose(plan["replay"]["all_at_close_pct"], 100 * (72.5 / 63.3 - 1))


def test_contradictions_and_misreads_are_noted():
    text = FINAL.replace("| 2 | Buy | 61.4 |", "| 2 | Buy | 57 |").replace("| 3 | Sell | 67.8 |", "| 3 | Sell | 60 |")
    text = text.replace("| 4 | Sell | 72 |", "| 4 | Sell | 7 |")
    plan = _plan(text)
    assert [t["price"] for t in plan["tranches"]] == [63.3, 57.0, 60.0]
    assert any("too far from the 63.30 close" in note for note in plan["notes"])
    assert any("Buy 2 at 57.00 is at or below the 58.00 stop" in note for note in plan["notes"])
    assert any("Sell 3 at 60.00 is below" in note for note in plan["notes"])


def test_no_table_no_plan():
    assert _plan("**Rating**: Hold\n\n**Position Plan**: not provided") is None


def _series(closes: list[float]) -> list[dict]:
    start = date(2025, 1, 1)
    return [_bar((start + timedelta(days=i)).isoformat(), c, c * 1.01, c * 0.99, c) for i, c in enumerate(closes)]


def test_indicators_match_their_definitions():
    bars = _series([100 + i for i in range(260)])
    indicators = compute_indicators(_frame(bars))
    assert math.isclose(indicators["sma50"].iloc[-1], sum(100 + i for i in range(210, 260)) / 50)
    assert math.isclose(indicators["sma200"].iloc[-1], sum(100 + i for i in range(60, 260)) / 200)
    assert indicators["rsi14"].iloc[-1] == 100  # only gains
    assert indicators["sma200"].iloc[:199].isna().all()


def test_a_steady_rise_reads_as_an_uptrend_and_draws_from_the_first_bar():
    closes = [100 + i * 0.5 + 6 * math.sin(i / 6) for i in range(400)]
    bars = _series(closes)
    result = chart_technicals(bars[:220], bars[220:], date.fromisoformat(bars[300]["time"]))
    assert len(result["series"]["sma200"]) == 180  # defined from the chart's first day
    analysis, latest = result["analysis"], result["latest"]
    assert analysis["date"] == bars[300]["time"] and latest["date"] == bars[-1]["time"]
    trend = next(s for s in analysis["signals"] if s["name"] == "Trend")
    assert trend["tone"] == "bullish"
    assert next(s for s in analysis["signals"] if s["name"] == "Structure")["tone"] == "bullish"
    assert all(level["price"] < analysis["close"] for level in analysis["supports"])
    assert all(level["price"] > analysis["close"] for level in analysis["resistances"])


def test_a_read_sees_no_pivot_that_later_bars_confirm():
    closes = [100 + 6 * math.sin(i / 4) for i in range(120)]
    frame = _frame(_series(closes))
    upto = 80
    assert all(point["index"] <= upto - 5 for point in swing_points(frame, upto))


def test_the_book_gives_cash_and_the_units_held_under_any_spelling():
    snapshot = {"cash": 5000, "currency": "AUD", "positions": [
        {"ticker": "BTCUSDT", "quantity": 0.5}, {"ticker": "BTC-USD", "quantity": 0.25}, {"ticker": "NVDA", "quantity": 3}]}
    assert book_position(snapshot, "BTC-USD") == {"cash": 5000, "currency": "AUD", "units": 0.75}
    assert book_position(None, "NVDA") is None


@pytest.fixture()
def chart_run(monkeypatch):
    run_id = "plan-chart-run"
    bars = _series([60 + 3 * math.sin(i / 5) + i * 0.01 for i in range(300)])
    bars[-4:] = [{**bar, "time": day} for bar, day in zip(BARS, ("2026-07-30", "2026-07-31", "2026-08-03", "2026-08-04"),
                                                          strict=True)]
    monkeypatch.setattr(runs_api, "load_current_market_chart", lambda ticker, since=None, warmup=0: MarketChartData(
        symbol=ticker, as_of="2026-08-04", candles=bars[-60:], history=bars[-60 - warmup:-60]))
    with TestClient(app) as client:
        with SessionLocal.begin() as session:
            session.add(Run(id=run_id, ticker="FCX", trade_date=date(2026, 7, 30), asset_type="stock",
                            selected_analysts=["market"], llm_provider="claude", deep_think_llm="m",
                            quick_think_llm="m", max_debate_rounds=1, max_risk_discuss_rounds=1,
                            output_language="English", config_snapshot={}, status="completed",
                            portfolio_snapshot={"cash": 10_000, "currency": "USD", "positions": []}))
            session.add(Decision(run_id=run_id, rating="Overweight"))
            session.add(RunSection(run_id=run_id, section_key="portfolio_manager", content_md=FINAL))
        yield client, run_id
        client.delete(f"/api/runs/{run_id}")


def test_the_chart_endpoint_carries_the_plan_and_the_technical_read(chart_run):
    client, run_id = chart_run
    body = client.get(f"/api/runs/{run_id}/chart").json()
    assert body["candles"][-1]["volume"] == 1e6
    assert body["plan"]["source"] == "Final decision"
    assert [t["amount"] for t in body["plan"]["tranches"]] == [400.0, 300.0, None, None]
    assert body["plan"]["summary"]["currency"] == "USD" and body["plan"]["replay"]["sessions"] == 3
    assert {"ema10", "sma50", "sma200", "bb_upper", "rsi14", "macd"} <= set(body["technicals"]["series"])
    assert body["technicals"]["analysis"]["date"] == "2026-07-30"
    assert body["technicals"]["latest"]["date"] == "2026-08-04"


def test_the_chart_carries_the_weekly_and_monthly_structure_and_the_action_now(chart_run, monkeypatch):
    import pandas as pd

    from backend.adapters import tradingagents_adapter as adapter

    client, run_id = chart_run
    days = pd.bdate_range("2022-01-03", "2026-08-04")
    prices = pd.DataFrame({"Date": days, "Close": [50 + 0.02 * i + 5 * math.sin(i / 9) for i in range(len(days))]})
    prices["Open"], prices["High"], prices["Low"], prices["Volume"] = (
        prices["Close"] - 0.1, prices["Close"] + 0.8, prices["Close"] - 0.8, 1e6)
    monkeypatch.setattr(adapter, "load_ohlcv", lambda *a, **k: prices.copy())
    monkeypatch.setattr(runs_api, "load_price_structure", adapter.load_price_structure)
    with SessionLocal.begin() as session:
        section = session.query(RunSection).filter_by(run_id=run_id, section_key="portfolio_manager").one()
        section.content_md = FINAL.replace("**Price Target**", "**Action Now**: Wait for a pullback\n\n**Price Target**")
    body = client.get(f"/api/runs/{run_id}/chart").json()
    structure = body["structure"]
    assert body["action_now"] == "Wait for pullback"
    assert {"weekly", "monthly"} <= set(structure["frames"])
    assert structure["frames"]["weekly"]["slow"] == 40 and structure["frames"]["weekly"]["series"]["sma_slow"]
    # Read at the analysis date, as the market analyst's tool was, and again at the latest bar.
    assert structure["analysis"]["as_of"] == "2026-07-30" and structure["latest"]["as_of"] == "2026-08-04"
    weekly = structure["analysis"]["timeframes"]["weekly"]
    assert weekly["plan"]["stance"] in structure["stances"]
    assert all(zone["low"] < zone["high"] for zone in structure["analysis"]["zones"])


def test_the_action_now_is_read_only_from_the_final_decision():
    from backend.app.services.charts import decision_stance

    final = [RunSection(section_key="portfolio_manager", content_md="**Rating**: Buy\n\n**Action Now**: Buy now")]
    trader = [RunSection(section_key="trader", content_md="**Action Now**: Exit")]
    assert decision_stance(final) == "Buy now"
    assert decision_stance(trader) is None
    assert decision_stance([RunSection(section_key="portfolio_manager", content_md="**Action Now**: not provided")]) is None
