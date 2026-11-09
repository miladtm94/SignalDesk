"""Backtests through the API, with the analysis and settlement faked offline.

The fakes write to the sweep's memory log exactly as upstream does (a pending
entry per decision, then its outcome), so these tests pin the console's part:
the grid, one run per cell on the sweep's own log, stop and resume, settlement,
and the scores read back from the log.
"""

from __future__ import annotations

import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.adapters import tradingagents_adapter as adapter
from backend.app.database import SessionLocal
from backend.app.main import app
from backend.app.models import Run
from backend.app.services import backtests as backtest_service, orchestrator as orchestrator_module

RATINGS = {"NVDA": "Buy", "AAPL": "Sell"}
# What each ticker did over its holding window, by its first date: NVDA beat its
# benchmark (a hit for Buy), AAPL beat it too (a miss for Sell).
ALPHA = {"NVDA": 0.021, "AAPL": 0.013}


def _payload(**overrides):
    start = date.today() - timedelta(days=60)
    return {
        "tickers": ["NVDA", "AAPL"],
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=14)).isoformat(),
        "every_n_days": 7,
        "selected_analysts": ["market"],
        "llm_provider": "ollama",
        "deep_think_llm": "qwen3:latest",
        "quick_think_llm": "qwen3:latest",
        **overrides,
    }


def fake_analysis(**kwargs):
    stop = kwargs.get("should_stop")
    if stop is not None and stop():
        raise adapter.RunStopped()
    log = adapter.TradingMemoryLog({"memory_log_path": str(kwargs["memory_log_path"])})
    rating = RATINGS[kwargs["ticker"]]
    decision = f"**Rating**: {rating}"
    log.store_decision(ticker=kwargs["ticker"], trade_date=kwargs["trade_date"],
                       final_trade_decision=decision, rating=rating)
    kwargs["on_section"](adapter.SectionEvent("portfolio_manager", decision),
                         {"llm_calls": 3, "tool_calls": 1, "tokens_in": 100, "tokens_out": 20})
    kwargs["report_dir"].mkdir(parents=True, exist_ok=True)
    report = kwargs["report_dir"] / "complete_report.md"
    report.write_text(decision, encoding="utf-8")
    return adapter.AdapterResult({}, rating, {"llm_calls": 3, "tool_calls": 1, "tokens_in": 100,
                                              "tokens_out": 20}, report)


def fake_settlement(*, memory_log_path, tickers, **kwargs):
    log = adapter.TradingMemoryLog({"memory_log_path": str(memory_log_path)})
    settled = []
    for entry in log.get_pending_entries():
        alpha = ALPHA[entry["ticker"]]
        log.update_with_outcome(entry["ticker"], entry["date"], alpha + 0.01, alpha, 5,
                                "A short lesson.", resolution_date=entry["date"])
        settled.append((entry["ticker"], entry["date"]))
    # Shaped as the adapter's stats snapshot, the per-tool data tallies included:
    # a settlement that summed every value failed on them.
    return {"settled": settled, "failed": [], "usage": {"llm_calls": len(settled), "tokens_in": 10,
                                                        "tokens_out": 5, "api_equivalent_usd": 0,
                                                        "data": {"get_stock_data": {"ok": 1}}}}


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(orchestrator_module, "run_streaming_analysis", fake_analysis)
    monkeypatch.setattr(backtest_service, "settle_backtest", fake_settlement)


def _wait(client, backtest_id, statuses, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/backtests/{backtest_id}").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.05)
    raise AssertionError(f"backtest stayed {body['status']}")


def test_the_grid_preview_matches_upstream_dates():
    with TestClient(app) as client:
        start = date.today() - timedelta(days=21)
        body = client.get("/api/backtests/grid", params={
            "start_date": start.isoformat(), "end_date": (start + timedelta(days=14)).isoformat(),
            "every_n_days": 7, "tickers": 2}).json()
    assert body["dates"] == [(start + timedelta(days=d)).isoformat() for d in (0, 7, 14)]
    assert body["cells"] == 6


def test_a_grid_past_today_stops_at_today():
    with TestClient(app) as client:
        body = client.get("/api/backtests/grid", params={
            "start_date": date.today().isoformat(),
            "end_date": (date.today() + timedelta(days=30)).isoformat(), "every_n_days": 7}).json()
    assert body["dates"] == [date.today().isoformat()]


def test_a_backtest_runs_each_cell_settles_and_scores_by_rating(offline):
    with TestClient(app) as client:
        created = client.post("/api/backtests", json=_payload())
        assert created.status_code == 202, created.text
        assert created.json()["cells"] == 6
        backtest_id = created.json()["backtest_id"]
        body = _wait(client, backtest_id, {"completed", "failed"})

        assert body["status"] == "completed", body["error_message"]
        assert body["progress"]["completed"] == 6 and body["progress"]["scored"] == 6
        assert [cell["ticker"] for cell in body["cells"]] == ["NVDA"] * 3 + ["AAPL"] * 3
        assert {cell["rating"] for cell in body["cells"]} == {"Buy", "Sell"}
        nvda = body["cells"][0]
        assert nvda["pending"] is False and nvda["alpha_return"] == pytest.approx(0.021)
        assert nvda["benchmark"] == "SPY" and nvda["holding_days"] == 5

        upstream = body["scores"]["upstream"]
        assert upstream["resolved"] == 6 and upstream["pending"] == 0
        assert upstream["by_rating"]["Buy"]["count"] == 3
        assert upstream["by_rating"]["Buy"]["hit_rate"] == 1.0
        assert upstream["by_rating"]["Sell"]["hit_rate"] == 0.0
        console = body["scores"]["console"]
        assert console["directional"] == 6 and console["hit_rate"] == pytest.approx(0.5)
        assert console["per_ticker"]["NVDA"]["hit_rate"] == 1.0
        assert body["usage"]["llm_calls"] == 6 * 3 + 6

        # Every cell is a run of its own, kept out of the analysis history.
        run_id = nvda["run_id"]
        run = client.get(f"/api/runs/{run_id}").json()
        assert run["backtest_id"] == backtest_id and run["status"] == "completed"
        assert run["checkpoint_enabled"] is False
        assert all(item["id"] != run_id for item in client.get("/api/runs").json())
        assert any(item["id"] == run_id for item in client.get("/api/runs?include_backtests=true").json())
        assert client.post(f"/api/runs/{run_id}/resume").status_code == 409
        assert client.delete(f"/api/runs/{run_id}").status_code == 409

        assert client.delete(f"/api/backtests/{backtest_id}").status_code == 204
        assert client.get(f"/api/runs/{run_id}").status_code == 404


def test_the_live_memory_log_is_never_written(offline):
    live = Path(adapter.DEFAULT_CONFIG["memory_log_path"])
    before = live.read_text(encoding="utf-8") if live.exists() else None
    with TestClient(app) as client:
        backtest_id = client.post("/api/backtests", json=_payload(tickers=["NVDA"])).json()["backtest_id"]
        _wait(client, backtest_id, {"completed", "failed"})
        client.delete(f"/api/backtests/{backtest_id}")
    after = live.read_text(encoding="utf-8") if live.exists() else None
    assert before == after


def test_a_stopped_backtest_resumes_with_the_cells_left(monkeypatch, offline):
    gate = threading.Event()

    def slow_analysis(**kwargs):
        # The first cell waits until it is told to stop, as a long run would.
        if kwargs["ticker"] == "NVDA" and not gate.is_set():
            while not kwargs["should_stop"]():
                time.sleep(0.01)
            gate.set()
            raise adapter.RunStopped()
        return fake_analysis(**kwargs)

    monkeypatch.setattr(orchestrator_module, "run_streaming_analysis", slow_analysis)
    with TestClient(app) as client:
        backtest_id = client.post("/api/backtests", json=_payload()).json()["backtest_id"]
        deadline = time.monotonic() + 5
        while client.get(f"/api/backtests/{backtest_id}").json()["progress"]["running"] == 0:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert client.post(f"/api/backtests/{backtest_id}/stop").status_code == 200
        body = _wait(client, backtest_id, {"stopped"})
        assert body["cells"][0]["status"] == "stopped"
        assert body["progress"]["queued"] == 5

        assert client.post(f"/api/backtests/{backtest_id}/resume").status_code == 202
        body = _wait(client, backtest_id, {"completed", "failed"})
        assert body["status"] == "completed" and body["progress"]["completed"] == 6
        assert client.post(f"/api/backtests/{backtest_id}/resume").status_code == 409
        client.delete(f"/api/backtests/{backtest_id}")


def test_an_oversized_grid_is_refused(offline):
    with TestClient(app) as client:
        # 61 daily dates for three tickers: 183 cells.
        response = client.post("/api/backtests", json=_payload(
            every_n_days=1, end_date=date.today().isoformat(), tickers=["NVDA", "AAPL", "MSFT"]))
    assert response.status_code == 400 and "limit" in response.json()["detail"]


def test_a_backtest_against_the_book_needs_a_saved_book(offline):
    with TestClient(app) as client:
        client.delete("/api/portfolio")
        response = client.post("/api/backtests", json=_payload(use_portfolio=True))
        assert response.status_code == 400 and "holdings" in response.json()["detail"]

        client.put("/api/portfolio", json={"cash": 1000, "currency": "usd", "positions": [
            {"ticker": "nvda", "quantity": 10, "average_price": 100}]})
        created = client.post("/api/backtests", json=_payload(use_portfolio=True, tickers=["NVDA"]))
        backtest_id = created.json()["backtest_id"]
        body = _wait(client, backtest_id, {"completed", "failed"})
        assert body["used_portfolio"] is True
        with SessionLocal() as session:
            run = session.get(Run, body["cells"][0]["run_id"])
            assert run.portfolio_snapshot["positions"][0]["ticker"] == "NVDA"
        client.delete(f"/api/backtests/{backtest_id}")
        client.delete("/api/portfolio")


def test_an_interrupted_backtest_becomes_resumable(offline, monkeypatch):
    monkeypatch.setattr(backtest_service.BacktestService, "_launch", lambda self, backtest_id, job: job.close())
    with TestClient(app) as client:
        backtest_id = client.post("/api/backtests", json=_payload(tickers=["NVDA"])).json()["backtest_id"]
        from backend.app.api.backtests import backtests

        assert backtests.recover_interrupted() == 1
        body = client.get(f"/api/backtests/{backtest_id}").json()
        assert body["status"] == "stopped" and "Resume" in body["error_message"]
        client.delete(f"/api/backtests/{backtest_id}")


def test_a_reached_plan_limit_stops_the_sweep_instead_of_failing_every_cell(monkeypatch, offline):
    from tradingagents.llm_clients import claude_code

    calls = []

    def limited(**kwargs):
        calls.append(kwargs["trade_date"])
        if len(calls) == 2:
            raise claude_code.ClaudeUsageLimit("Your Claude plan's usage limit is reached (resets 5pm). Continue once it resets.")
        return fake_analysis(**kwargs)

    monkeypatch.setattr(orchestrator_module, "run_streaming_analysis", limited)
    with TestClient(app) as client:
        backtest_id = client.post("/api/backtests", json=_payload(tickers=["NVDA"])).json()["backtest_id"]
        body = _wait(client, backtest_id, {"stopped", "completed", "failed"})

        assert body["status"] == "stopped" and "usage limit" in body["error_message"]
        assert len(calls) == 2  # the third cell never ran
        assert [cell["status"] for cell in body["cells"]] == ["completed", "stopped", "queued"]

        monkeypatch.setattr(orchestrator_module, "run_streaming_analysis", fake_analysis)
        assert client.post(f"/api/backtests/{backtest_id}/resume").status_code == 202
        body = _wait(client, backtest_id, {"completed", "failed"})
        assert body["progress"]["completed"] == 3
        client.delete(f"/api/backtests/{backtest_id}")


def test_a_sweep_that_failed_only_while_settling_completes_when_settled_again(monkeypatch, offline):
    attempts = []

    def flaky_settlement(**kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("benchmark prices did not load")
        return fake_settlement(**kwargs)

    monkeypatch.setattr(backtest_service, "settle_backtest", flaky_settlement)
    with TestClient(app) as client:
        backtest_id = client.post("/api/backtests", json=_payload(tickers=["NVDA"])).json()["backtest_id"]
        body = _wait(client, backtest_id, {"completed", "failed"})
        assert body["status"] == "failed" and "benchmark prices" in body["error_message"]
        assert all(cell["status"] == "completed" for cell in body["cells"])

        assert client.post(f"/api/backtests/{backtest_id}/settle").status_code == 202
        body = _wait(client, backtest_id, {"completed"})
        assert body["error_message"] is None and body["progress"]["scored"] == 3
        client.delete(f"/api/backtests/{backtest_id}")
