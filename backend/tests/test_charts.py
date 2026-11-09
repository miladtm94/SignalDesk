from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.adapters.tradingagents_adapter import MarketChartData
from backend.app.api import runs as runs_api
from backend.app.database import SessionLocal
from backend.app.main import app
from backend.app.models import Decision, Run, RunSection
from backend.app.services.charts import (
    _number,
    analysis_outcome,
    decision_direction,
    plan_levels,
    strategy_levels,
)


def test_strategy_levels_use_saved_fields_then_explicit_report_labels():
    decision = Decision(entry_price=101.5)
    sections = [
        RunSection(
            section_key="trader",
            content_md=(
                "The 50-day average is 88.\n"
                "**Price Target**: $125.00\n"
                "**Stop Loss**: $94.50"
            ),
        )
    ]

    assert strategy_levels(decision, sections) == [
        {"kind": "entry", "price": 101.5, "source": "Saved decision"},
        {"kind": "take_profit", "price": 125.0, "source": "Trading plan"},
        {"kind": "stop_loss", "price": 94.5, "source": "Trading plan"},
    ]


# The FCX Underweight of 2026-11-02: the trader's trim price and stop, the manager's downside.
UNDERWEIGHT = [
    RunSection(section_key="portfolio_manager", content_md="**Rating**: Underweight\n\n**Price Target**: 57.0"),
    RunSection(section_key="trader", content_md=(
        "**Action**: Sell\n\n**Entry Price**: 65.5\n\n**Stop Loss**: 67.6\n\nFINAL TRANSACTION PROPOSAL: **SELL**")),
]


def test_an_underweight_reads_as_a_sale_into_strength_not_a_long_trade():
    decision = Decision(rating="Underweight")
    direction = decision_direction(decision, UNDERWEIGHT)
    drawn, notes = plan_levels(strategy_levels(decision, UNDERWEIGHT), direction, 62.47)

    assert direction == "reduce"
    assert [(level["label"], level["price"]) for level in drawn] == [
        ("Sell / trim at", 65.5), ("Downside target", 57.0), ("Reassess above", 67.6)]
    assert notes == []


def test_a_level_on_the_wrong_side_of_a_long_entry_is_not_drawn():
    sections = [RunSection(section_key="trader", content_md="**Entry Price**: 65\n**Price Target**: 57\n**Stop Loss**: 60")]
    drawn, notes = plan_levels(strategy_levels(None, sections), "long", 63.3)

    assert [level["label"] for level in drawn] == ["Entry", "Stop loss"]
    assert notes == ["The take profit of 57.00 (trading plan) is below the 65.00 entry, which contradicts a "
                     "long setup, so it is not drawn."]


def test_without_an_entry_the_analysis_close_anchors_the_check():
    sections = [RunSection(section_key="portfolio_manager", content_md="**Price Target**: 57.0\n**Stop Loss**: 70")]
    drawn, notes = plan_levels(strategy_levels(None, sections), "long", 63.3)
    assert drawn == [] and len(notes) == 2 and "63.30 analysis-day close" in notes[0]


def test_a_hold_names_its_levels_by_where_they_sit():
    sections = [RunSection(section_key="trader", content_md="**Entry Price**: not provided\n**Stop Loss**: 57.2")]
    decision = Decision(rating="Hold")
    drawn, notes = plan_levels(strategy_levels(decision, sections), decision_direction(decision, sections), 63.3)
    assert [(level["label"], level["price"]) for level in drawn] == [("Exit below", 57.2)] and notes == []


def test_the_trader_s_proposal_gives_the_side_before_the_manager_rates():
    assert decision_direction(None, UNDERWEIGHT) == "reduce"
    assert decision_direction(Decision(rating=None), [RunSection(section_key="trader", content_md="x")]) is None


@pytest.mark.parametrize("text, price", [
    ("65.5", 65.5),
    ("$1,250.75 on a close", 1250.75),
    ("2 ATR below entry (~59.1)", 59.1),
    ("1.5x ATR", None),
    ("through 2026-09-30", None),
    ("close below 57.24 (set 7/29)", 57.24),
    ("(the 7/29 low) 55.74", 55.74),
    ("$1.2B buyback", None),
    ("64.00–64.94", 64.0),
    ("not provided", None),
    ("20% above", None),
])
def test_a_number_that_is_a_count_ratio_or_date_is_not_read_as_a_price(text, price):
    assert _number(text) == price


def test_a_level_far_from_the_price_is_a_misread_and_is_not_drawn():
    sections = [RunSection(section_key="trader", content_md="**Stop Loss**: 2\n**Price Target**: 80")]
    drawn, notes = plan_levels(strategy_levels(None, sections), "long", 63.3)
    assert [level["price"] for level in drawn] == [80.0]
    assert notes == ["2.00 (trading plan) is too far from the 63.30 close to be a level, so it is not drawn."]


def _candle(day: str, close: float, high: float | None = None, low: float | None = None) -> dict:
    return {"time": day, "open": close, "high": high or close, "low": low or close, "close": close}


CANDLES = [
    _candle("2026-07-29", 59.86),
    _candle("2026-07-30", 63.30),
    _candle("2026-08-04", 67.16, high=67.85),
    _candle("2026-08-25", 79.91, high=80.24),
    _candle("2026-09-14", 69.34, low=57.0),
    _candle("2026-10-30", 62.47),
]


def test_the_outcome_is_measured_from_the_close_the_analysis_saw():
    levels = [{"kind": "take_profit", "price": 75.0, "label": "Upside target"},
              {"kind": "stop_loss", "price": 57.2, "label": "Exit below"},
              {"kind": "entry", "price": 90.0, "label": "Add at"}]
    outcome = analysis_outcome(CANDLES, date(2026, 8, 1), levels)   # a Saturday: Friday's bar is 07-30

    assert outcome["date"] == "2026-07-30" and outcome["close"] == 63.30
    assert outcome["latest_close"] == 62.47 and round(outcome["change_pct"], 2) == -1.31
    assert (outcome["high"], outcome["high_date"]) == (80.24, "2026-08-25")
    assert (outcome["low"], outcome["low_date"]) == (57.0, "2026-09-14")
    assert [touch["date"] for touch in outcome["touches"]] == ["2026-08-25", "2026-09-14", None]
    assert outcome["sessions"] == 4


def test_no_outcome_when_the_chart_starts_after_the_analysis():
    assert analysis_outcome(CANDLES, date(2026, 1, 5), []) is None


def test_chart_endpoint_returns_candles_and_analysis_levels(monkeypatch):
    run_id = "chart-test-run"
    asked = {}
    monkeypatch.setattr(
        runs_api,
        "load_current_market_chart",
        lambda ticker, since=None, warmup=0: asked.update(since=since, warmup=warmup) or MarketChartData(
            symbol=ticker,
            as_of="2026-08-27",
            candles=[
                {"time": "2026-08-25", "open": 220.0, "high": 225.0, "low": 218.0, "close": 224.0},
                {"time": "2026-08-26", "open": 224.0, "high": 241.0, "low": 223.0, "close": 236.0},
                {"time": "2026-08-27", "open": 236.0, "high": 238.0, "low": 230.0, "close": 233.0},
            ],
        ),
    )

    with TestClient(app) as client:
        with SessionLocal.begin() as session:
            session.add(
                Run(
                    id=run_id,
                    ticker="SPCX",
                    trade_date=date(2026, 8, 25),
                    asset_type="stock",
                    selected_analysts=["market"],
                    llm_provider="google",
                    deep_think_llm="gemini-2.5-flash-lite",
                    quick_think_llm="gemini-2.5-flash-lite",
                    max_debate_rounds=1,
                    max_risk_discuss_rounds=1,
                    output_language="English",
                    config_snapshot={},
                    status="completed",
                )
            )
            session.add(Decision(run_id=run_id, rating="Overweight"))
            session.add(
                RunSection(
                    run_id=run_id,
                    section_key="portfolio_manager",
                    content_md="**Price Target**: 240.0\n**Stop Loss**: 230",
                )
            )
        response = client.get(f"/api/runs/{run_id}/chart")

    body = response.json()
    assert response.status_code == 200
    assert asked["since"] == date(2026, 8, 25) and asked["warmup"] > 200
    assert body["current_price"] == 233.0
    assert body["direction"] == "long" and body["analysis_date"] == "2026-08-25"
    # 230 is above the 224 close the analysis saw, so it cannot be a long's stop.
    assert body["levels"] == [
        {"kind": "take_profit", "price": 240.0, "source": "Final decision", "label": "Take profit"}
    ]
    assert body["notes"] and "230.00" in body["notes"][0]
    assert body["outcome"]["close"] == 224.0
    assert body["outcome"]["touches"] == [
        {"kind": "take_profit", "label": "Take profit", "price": 240.0, "date": "2026-08-26"}]
