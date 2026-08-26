from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from backend.adapters.tradingagents_adapter import MarketChartData
from backend.app.api import runs as runs_api
from backend.app.database import SessionLocal
from backend.app.main import app
from backend.app.models import Decision, Run, RunSection
from backend.app.services.charts import strategy_levels


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


def test_chart_endpoint_returns_candles_and_analysis_levels(monkeypatch):
    run_id = "chart-test-run"
    monkeypatch.setattr(
        runs_api,
        "load_current_market_chart",
        lambda ticker: MarketChartData(
            symbol=ticker,
            as_of="2026-08-25",
            candles=[
                {"time": "2026-08-25", "open": 220.0, "high": 225.0, "low": 218.0, "close": 224.0}
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
            session.add(
                RunSection(
                    run_id=run_id,
                    section_key="portfolio_manager",
                    content_md="**Price Target**: 240.0",
                )
            )
        response = client.get(f"/api/runs/{run_id}/chart")

    assert response.status_code == 200
    assert response.json()["current_price"] == 224.0
    assert response.json()["levels"] == [
        {"kind": "take_profit", "price": 240.0, "source": "Final decision"}
    ]
