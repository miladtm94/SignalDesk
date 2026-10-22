from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from backend.adapters import tradingagents_adapter as adapter
from backend.app import database
from backend.app.main import app
from backend.app.services import orchestrator as orchestrator_module, portfolio as portfolio_service
from backend.app.services.costs import actual_usage_cost, estimate_run_cost

BOOK = {"cash": 25000, "currency": "usd", "positions": [
    {"ticker": "nvda", "quantity": 120, "average_price": 150},
    {"ticker": "AAPL", "quantity": -10},
]}
RUN = {
    "ticker": "NVDA", "trade_date": "2026-08-25", "selected_analysts": ["market"],
    "llm_provider": "ollama", "deep_think_llm": "qwen3:latest", "quick_think_llm": "qwen3:latest",
}


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        test_client.delete("/api/portfolio")
        yield test_client
        test_client.delete("/api/portfolio")


def test_no_saved_book_is_not_a_flat_book(client):
    assert client.get("/api/portfolio").json() == {
        "configured": False, "cash": None, "currency": None, "positions": [], "updated_at": None}
    saved = client.put("/api/portfolio", json={"cash": 500, "positions": []}).json()
    assert saved["configured"] is True and saved["positions"] == []
    preview = client.get("/api/portfolio/preview", params={"ticker": "NVDA"}).json()
    assert "No current position in NVDA" in preview["context"]


def test_a_saved_book_is_normalized_and_previewed_as_the_agents_read_it(client):
    saved = client.put("/api/portfolio", json=BOOK)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["currency"] == "USD" and [p["ticker"] for p in body["positions"]] == ["NVDA", "AAPL"]

    preview = client.get("/api/portfolio/preview", params={"ticker": "nvda"}).json()
    assert preview["held"]["quantity"] == 120
    assert "Current position in NVDA: 120 units, average price 150.00" in preview["context"]
    assert "Cash available: 25,000.00 USD" in preview["context"]
    assert "Other positions: AAPL -10" in preview["context"]


@pytest.mark.parametrize("book, problem", [
    ({"positions": [{"ticker": "NVDA", "quantity": 1}, {"ticker": "nvda", "quantity": 2}]}, "listed twice"),
    ({"positions": [{"ticker": "NVDA", "quantity": 0}]}, "non-zero"),
    ({"positions": [{"ticker": "NV DA", "quantity": 1}]}, "compact"),
    ({"cash": -5, "positions": []}, "greater than or equal"),
])
def test_an_unusable_book_is_refused_with_the_reason(client, book, problem):
    response = client.put("/api/portfolio", json=book)
    assert response.status_code == 422 and problem in response.text


def test_quotes_tolerate_an_instrument_without_a_price(client, monkeypatch):
    client.put("/api/portfolio", json=BOOK)

    def chart(symbol, limit=180):
        if symbol == "AAPL":
            raise RuntimeError("no candles")
        return adapter.MarketChartData(symbol, "2026-10-22", [{"time": "2026-10-22", "open": 1, "high": 2,
                                                                "low": 1, "close": 181.5}])

    monkeypatch.setattr(portfolio_service, "load_current_market_chart", chart)
    quotes = {q["ticker"]: q for q in client.get("/api/portfolio/quotes").json()}
    assert quotes["NVDA"]["last_close"] == 181.5 and quotes["NVDA"]["as_of"] == "2026-10-22"
    assert quotes["AAPL"]["last_close"] is None and "No current price" in quotes["AAPL"]["error"]


def test_a_run_decides_against_the_book_it_was_launched_with(client, monkeypatch):
    monkeypatch.setattr(orchestrator_module.orchestrator, "start", lambda run_id: None)
    assert client.post("/api/runs", json={**RUN, "use_portfolio": True}).status_code == 400

    client.put("/api/portfolio", json=BOOK)
    run_id = client.post("/api/runs", json={**RUN, "use_portfolio": True}).json()["run_id"]
    # Editing the book later does not change what the run was given.
    client.put("/api/portfolio", json={"cash": 1, "positions": []})
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["used_portfolio"] is True
    assert detail["portfolio_snapshot"]["positions"][0] == {"ticker": "NVDA", "quantity": 120.0, "average_price": 150.0}
    assert "Current position in NVDA: 120 units" in detail["portfolio_context"]

    plain = client.post("/api/runs", json=RUN).json()["run_id"]
    detail = client.get(f"/api/runs/{plain}").json()
    assert detail["used_portfolio"] is False and detail["portfolio_context"] is None
    for item in (run_id, plain):
        client.delete(f"/api/runs/{item}")


def test_a_running_analysis_can_be_stopped_and_resumed(client, monkeypatch):
    seen = []

    def waits_for_stop(**kwargs):
        seen.append(kwargs["portfolio"])
        while not kwargs["should_stop"]():
            time.sleep(0.01)
        raise adapter.RunStopped()

    monkeypatch.setattr(orchestrator_module, "run_streaming_analysis", waits_for_stop)
    run_id = client.post("/api/runs", json=RUN).json()["run_id"]
    deadline = time.monotonic() + 5
    while client.get(f"/api/runs/{run_id}").json()["status"] != "running":
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert client.post(f"/api/runs/{run_id}/stop").status_code == 200
    while client.get(f"/api/runs/{run_id}").json()["status"] == "running":
        assert time.monotonic() < deadline
        time.sleep(0.02)
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "stopped" and "checkpoint" in detail["error_message"]
    assert detail["events"][-1]["event_type"] == "stopped"
    assert client.post(f"/api/runs/{run_id}/stop").status_code == 409
    assert seen == [None]

    with client.websocket_connect(f"/api/runs/{run_id}/stream") as websocket:
        events = [websocket.receive_json() for _ in detail["events"]]
    assert events[-1]["event_type"] == "stopped"
    client.delete(f"/api/runs/{run_id}")


def test_an_older_database_gains_the_new_columns(tmp_path):
    # A console database from before this release: today's schema without the
    # columns this release added, holding a run.
    engine = create_engine(f"sqlite:///{tmp_path / 'old.sqlite3'}")
    database.Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for table, column in (("runs", "portfolio_snapshot"), ("usage", "api_equivalent_usd")):
            connection.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {column}")
        connection.exec_driver_sql(
            "INSERT INTO runs (id, ticker, trade_date, asset_type, selected_analysts, llm_provider, "
            "deep_think_llm, quick_think_llm, max_debate_rounds, max_risk_discuss_rounds, checkpoint_enabled, "
            "output_language, config_snapshot, estimated_cost_usd, status, starred, created_at) VALUES "
            "('old', 'NVDA', '2026-08-25', 'stock', '[]', 'ollama', 'q', 'q', 1, 1, 1, 'English', '{}', 0, "
            "'completed', 0, '2026-08-25 00:00:00')")

    with engine.begin() as connection:
        database._add_missing_columns(connection)
    columns = {table: {c["name"] for c in inspect(engine).get_columns(table)} for table in ("runs", "usage")}
    assert "portfolio_snapshot" in columns["runs"]
    assert "api_equivalent_usd" in columns["usage"]
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT portfolio_snapshot FROM runs WHERE id='old'").scalar() is None
    # Running it again changes nothing.
    with engine.begin() as connection:
        database._add_missing_columns(connection)


def test_a_signed_in_claude_plan_is_a_configured_provider(client, monkeypatch):
    status = {"ready": True, "installed": True, "signed_in": True, "plan": "max", "account": "a•••@example.com",
              "message": "Signed in to a Claude Max plan", "login_command": "tradingagents login claude"}
    monkeypatch.setattr("backend.app.services.providers.claude_plan_status", lambda fresh=False: status)
    providers = {p["id"]: p for p in client.get("/api/config/providers").json()["providers"]}
    claude = providers["claude"]
    assert claude["configured"] is True and claude["availability"] == "subscription"
    assert claude["subscription"]["plan"] == "max" and claude["key_env"] is None
    assert [m["value"] for m in claude["deep_models"]][:2] == ["claude-opus-5-5", "claude-fable-5-1"]
    assert claude["reasoning"]["config_key"] == "anthropic_effort"
    assert client.get("/api/config/subscriptions/claude").json()["plan"] == "max"
    assert client.get("/api/config/subscriptions/openai").status_code == 404


def test_a_claude_plan_that_is_not_signed_in_cannot_be_made_active(client, monkeypatch):
    monkeypatch.setattr("backend.app.services.providers.claude_plan_status",
                        lambda fresh=False: {"ready": False, "installed": False, "message": "not installed"})
    response = client.put("/api/config/preferences", json={
        "llm_provider": "claude", "quick_think_llm": "claude-sonnet-5-5", "deep_think_llm": "claude-opus-5-5"})
    assert response.status_code == 400


def test_plan_billed_runs_cost_nothing_per_token():
    assert estimate_run_cost(provider="claude", quick_model="claude-sonnet-5-5", deep_model="claude-opus-5-5",
                             analysts=4, debate_rounds=2, risk_rounds=2) == 0
    assert actual_usage_cost(provider="chatgpt", quick_model="gpt-5.5", deep_model="gpt-5.5",
                             tokens_in=10**6, tokens_out=10**5) == 0
