from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.adapters import tradingagents_adapter as adapter


class Memory:
    def get_past_context(self, ticker):
        return "Prior lesson"

    def store_decision(self, **kwargs):
        self.stored = kwargs


class Propagator:
    def create_initial_state(self, ticker, trade_date, **kwargs):
        return {"company_of_interest": ticker, "trade_date": trade_date, **kwargs}

    def get_graph_args(self, callbacks=None):
        return {"stream_mode": "values", "config": {"callbacks": callbacks}}


class Stream:
    def stream(self, init_state, **kwargs):
        yield {"market_report": "Market evidence"}
        yield {
            "market_report": "Market evidence",
            "investment_debate_state": {
                "bull_history": "Bull case",
                "bear_history": "Bear case",
                "judge_decision": "Manager call",
            },
            "risk_debate_state": {"judge_decision": "**Rating**: Buy"},
            "final_trade_decision": "**Rating**: Buy",
        }


class MockGraph:
    def __init__(self, **kwargs):
        self.memory_log = Memory()
        self.propagator = Propagator()
        self.graph = Stream()
        self.workflow = SimpleNamespace(compile=lambda **kwargs: self.graph)
        self.config = kwargs["config"]

    def _resolve_pending_entries(self, ticker):
        pass

    def resolve_instrument_context(self, ticker, asset_type):
        return f"{ticker} ({asset_type})"

    def _log_state(self, trade_date, state):
        self.logged = state

    def process_signal(self, decision):
        return "Buy"

    def save_reports(self, state, ticker, save_path):
        target = Path(save_path) / "complete_report.md"
        target.write_text("# Report", encoding="utf-8")
        return target


def test_streaming_adapter_emits_each_completed_section(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "ConsoleTradingAgentsGraph", MockGraph)
    sections = []
    statuses = []
    result = adapter.run_streaming_analysis(
        run_id="run-1",
        ticker="NVDA",
        trade_date="2026-08-25",
        asset_type="stock",
        selected_analysts=["market"],
        config_overrides={"checkpoint_enabled": False, "llm_provider": "ollama"},
        report_dir=tmp_path,
        on_section=lambda event, usage: sections.append(event.section_key),
        on_status=lambda event, payload: statuses.append(event),
    )

    assert result.decision == "Buy"
    assert result.report_path.read_text() == "# Report"
    assert sections == ["market_report", "bull", "bear", "research_manager", "portfolio_manager"]
    assert statuses == []


def test_optional_overrides_do_not_erase_upstream_defaults(tmp_path):
    config = adapter._build_config(
        {"data_vendors": None, "temperature": None}, tmp_path / "report"
    )
    assert isinstance(config["data_vendors"], dict)
    assert config["data_vendors"]["core_stock_apis"]
    assert config["llm_timeout"] == adapter.LLM_REQUEST_TIMEOUT_SECONDS
    assert config["llm_max_retries"] == adapter.LLM_MAX_RETRIES


def test_console_graph_forwards_request_timeout():
    graph = adapter.ConsoleTradingAgentsGraph.__new__(adapter.ConsoleTradingAgentsGraph)
    graph.config = {"llm_provider": "google", "llm_timeout": 45}
    assert graph._get_provider_kwargs()["timeout"] == 45


def test_google_flash_lite_is_the_console_default():
    catalog = adapter.upstream_catalog()
    default_model = catalog["defaults"]["quick_think_llm"]
    assert catalog["defaults"]["llm_provider"] == "google"
    assert "flash-lite" in default_model
    assert catalog["defaults"]["deep_think_llm"] == default_model
    assert any(
        model["value"] == default_model
        for model in catalog["providers"]["google"]["deep"]
    )


@pytest.mark.integration
@pytest.mark.skipif(
    not __import__("os").getenv("TRADING_CONSOLE_RUN_INTEGRATION"),
    reason="Set TRADING_CONSOLE_RUN_INTEGRATION=1 with a configured cheap/local model.",
)
def test_real_local_streaming_path(tmp_path):
    events = []
    result = adapter.run_streaming_analysis(
        run_id="integration",
        ticker="NVDA",
        trade_date="2025-01-15",
        asset_type="stock",
        selected_analysts=["market"],
        config_overrides={
            "llm_provider": "ollama",
            "quick_think_llm": "qwen3:latest",
            "deep_think_llm": "qwen3:latest",
            "max_debate_rounds": 1,
            "max_risk_discuss_rounds": 1,
            "checkpoint_enabled": True,
        },
        report_dir=tmp_path,
        on_section=lambda event, usage: events.append(event),
        on_status=lambda event, payload: None,
    )
    assert result.report_path.exists()
    assert events
