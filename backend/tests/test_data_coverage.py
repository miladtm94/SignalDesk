"""A run records what each data tool returned, so a decision made without its data shows it."""

from __future__ import annotations

import uuid

import pytest
from langchain_core.messages import ToolMessage

from backend.adapters import tradingagents_adapter as adapter
from backend.app.models import Run, Usage
from backend.app.services.orchestrator import RunOrchestrator
from backend.tests.test_adapter_graph import ScriptedModel, _run, offline  # noqa: F401 - fixture


@pytest.mark.parametrize("output, outcome", [
    ("## FCX News, from 2026-07-23 to 2026-07-30 (Google News search ...)", "ok"),
    ("DATA_UNAVAILABLE: no configured vendor could serve get_news right now (down).", "unavailable"),
    ("NO_DATA_AVAILABLE: No usable market data for 'XYZ' from any configured vendor.", "unavailable"),
    ("<Yahoo Finance news unavailable for 2026-07-23..2026-07-30: it only serves recent items>", "unavailable"),
    ("# Insider Transactions for FCX\n# Point-in-time as of: 2026-07-30\n\nInsider transactions are withheld for "
     "this date.", "withheld"),
    ("Prediction-market odds are withheld for 2026-07-30. Polymarket serves only live odds.", "withheld"),
    ("No headlines about Freeport-McMoRan (FCX) published between 2026-07-23 and 2026-07-30 were found.", "empty"),
    ("Error: ValueError('bad indicator')\n Please fix your mistakes.", "error"),
    ("start_date 'last week' is not a date; give it as YYYY-MM-DD", "error"),
    ("", "empty"),
    (ToolMessage(content="## rsi values from 2026-06-30 to 2026-07-30:", tool_call_id="1"), "ok"),
])
def test_a_tool_answer_is_read_as_data_or_as_why_there_is_none(output, outcome):
    assert adapter.data_outcome(output) == outcome


def test_the_stats_handler_tallies_each_tool_by_what_it_returned():
    stats = adapter.StatsCallbackHandler()
    calls = [("get_news", "No headlines about FCX were found."), ("get_news", "## FCX News"),
             ("get_insider_transactions", "Insider transactions are withheld for this date.")]
    for name, output in calls:
        run_id = uuid.uuid4()
        stats.on_tool_start({"name": name}, "{}", run_id=run_id)
        stats.on_tool_end(output, run_id=run_id)
    failed = uuid.uuid4()
    stats.on_tool_start({"name": "get_stock_data"}, "{}", run_id=failed)
    stats.on_tool_error(RuntimeError("down"), run_id=failed)

    data = stats.snapshot()["data"]
    assert stats.snapshot()["tool_calls"] == 4
    assert data["get_news"] == {"ok": 1, "empty": 1, "withheld": 0, "unavailable": 0, "error": 0}
    assert data["get_insider_transactions"]["withheld"] == 1
    assert data["get_stock_data"]["error"] == 1


def test_a_run_through_the_real_graph_records_every_tool_it_used(tmp_path, monkeypatch, offline):  # noqa: F811
    result, _ = _run(tmp_path, monkeypatch, ScriptedModel())
    data = result.usage["data"]

    for tool in ("get_stock_data", "get_indicators", "get_news", "get_global_news", "get_income_statement"):
        assert data[tool]["ok"] >= 1, tool
    assert not any(counts["unavailable"] or counts["error"] for counts in data.values())


def test_the_usage_row_keeps_the_coverage():
    usage, run = Usage(run_id="r"), Run(llm_provider="ollama", quick_think_llm="q", deep_think_llm="d")
    RunOrchestrator._apply_usage(usage, run, {"llm_calls": 3, "data": {"get_news": {"ok": 1}}})
    assert usage.data_coverage == {"get_news": {"ok": 1}}
    # A later snapshot without tool data (a stop before any tool ran) keeps what was recorded.
    RunOrchestrator._apply_usage(usage, run, {"llm_calls": 4})
    assert usage.data_coverage == {"get_news": {"ok": 1}}
