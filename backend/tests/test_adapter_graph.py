"""The real upstream graph through the console adapter, offline.

A scripted model stands in for the provider and every data vendor answers
locally, so this pins what the mocked adapter tests cannot: that the streaming
surface, state names and lifecycle helpers of the installed TradingAgents
release still produce every console section, a rating and a report tree.
"""

from __future__ import annotations

import time

import pandas as pd
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field

from backend.adapters import tradingagents_adapter as adapter
from tradingagents.agents import context, schemas
from tradingagents.agents.analysts import sentiment_analyst
from tradingagents.dataflows import router
from tradingagents.dataflows.vendors.yahoo import market as yahoo_market, snapshot

TRADE_DATE = "2026-01-09"
TEXT = "Report.\n\n**Rating**: Overweight\n\nFINAL TRANSACTION PROPOSAL: **BUY**"
STRUCTURED = {
    schemas.ResearchPlan: schemas.ResearchPlan(
        recommendation=schemas.PortfolioRating.OVERWEIGHT, rationale="r", strategic_actions="a"),
    schemas.TraderProposal: schemas.TraderProposal(action=schemas.TraderAction.BUY, reasoning="r"),
    schemas.PortfolioDecision: schemas.PortfolioDecision(
        rating=schemas.PortfolioRating.OVERWEIGHT, executive_summary="s", investment_thesis="t"),
    schemas.SentimentReport: schemas.SentimentReport(
        overall_band=schemas.SentimentBand.NEUTRAL, overall_score=5.0, confidence="low", narrative="n"),
}
ARGS = {"symbol": "NVDA", "ticker": "NVDA", "curr_date": TRADE_DATE, "start_date": "2026-01-02",
        "end_date": TRADE_DATE, "indicator": "rsi", "topic": "Fed rate cut", "freq": "quarterly"}
ALL_SECTIONS = [
    "market_report", "sentiment_report", "news_report", "fundamentals_report", "bull", "bear",
    "research_manager", "trader", "risk_aggressive", "risk_conservative", "risk_neutral",
    "portfolio_manager",
]


class ScriptedModel(BaseChatModel):
    """Calls every bound tool once, then answers with TEXT."""

    tools: tuple = ()
    prompts: list = Field(default_factory=list)   # shared across bound copies

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self.model_copy(update={"tools": tuple(tools)})

    def with_structured_output(self, schema, **kwargs):
        return RunnableLambda(lambda prompt: self.prompts.append(str(prompt)) or STRUCTURED[schema])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.prompts.append("\n".join(str(message.content) for message in messages))
        if self.tools:
            time.sleep(0.02)
        if self.tools and not isinstance(messages[-1], ToolMessage):
            calls = [{"name": tool.name, "id": f"call_{index}",
                      "args": {key: value for key, value in ARGS.items()
                               if key in tool.tool_call_schema.model_json_schema()["properties"]}}
                     for index, tool in enumerate(self.tools)]
            message = AIMessage(content="", tool_calls=calls)
        else:
            message = AIMessage(content=TEXT)
        return ChatResult(generations=[ChatGeneration(message=message)])


class _Client:
    def __init__(self, model):
        self.model = model

    def get_llm(self):
        return self.model


@pytest.fixture
def offline(monkeypatch):
    for method, vendors in router.VENDOR_METHODS.items():
        for vendor in vendors:
            monkeypatch.setitem(vendors, vendor, lambda *a, _m=method, **k: f"{_m} data")
    prices = pd.DataFrame({
        "Date": pd.bdate_range(end=TRADE_DATE, periods=60),
        "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 1_000_000,
    })
    monkeypatch.setattr(snapshot, "load_ohlcv", lambda *a, **k: prices.copy())
    monkeypatch.setattr(sentiment_analyst, "fetch_stocktwits_messages", lambda *a, **k: "no posts")
    monkeypatch.setattr(sentiment_analyst, "fetch_reddit_posts", lambda *a, **k: "no posts")
    monkeypatch.setattr(yahoo_market.yf, "Ticker",
                        lambda s: type("T", (), {"info": {"longName": "NVIDIA"}})())
    context._identity.cache_clear()


def _run(tmp_path, monkeypatch, model, **overrides):
    monkeypatch.setattr(adapter, "_create_tier_client", lambda config, tier, **k: _Client(model))
    sections: list[str] = []
    result = adapter.run_streaming_analysis(
        run_id="offline",
        ticker="NVDA",
        trade_date=TRADE_DATE,
        asset_type="stock",
        selected_analysts=["market", "social", "news", "fundamentals"],
        # Pinned: the package loads a developer's .env, which may set the depth.
        config_overrides={"llm_provider": "openai", "checkpoint_enabled": False,
                          "max_debate_rounds": 1, "max_risk_discuss_rounds": 1, **overrides},
        report_dir=tmp_path / "report",
        on_section=lambda event, usage: sections.append(event.section_key),
        on_status=lambda event, payload: None,
    )
    return result, sections


@pytest.mark.unit
def test_every_section_streams_from_the_real_graph(tmp_path, monkeypatch, offline):
    result, sections = _run(tmp_path, monkeypatch, ScriptedModel())

    assert result.decision == "Overweight"
    assert not result.degraded_streaming
    assert set(sections) == set(ALL_SECTIONS)
    # Each section is emitted when it changes, not once per streamed chunk.
    assert len(sections) == len(set(sections))
    assert result.report_path.is_file()
    assert (result.report_path.parent / "complete_report.html").is_file()


@pytest.mark.unit
def test_a_longer_debate_updates_its_sections_as_it_grows(tmp_path, monkeypatch, offline):
    _, sections = _run(tmp_path, monkeypatch, ScriptedModel(),
                       max_debate_rounds=2, max_risk_discuss_rounds=2)

    assert sections.count("bull") == sections.count("bear") == 2
    assert sections.count("risk_neutral") == 2
    assert sections.count("portfolio_manager") == 1


@pytest.mark.unit
def test_an_analyst_report_arrives_before_the_debate(tmp_path, monkeypatch, offline):
    _, sections = _run(tmp_path, monkeypatch, ScriptedModel())

    first_debate = sections.index("bull")
    assert {"market_report", "news_report", "fundamentals_report"} <= set(sections[:first_debate])


@pytest.mark.unit
def test_the_run_records_its_decision_in_the_memory_log(tmp_path, monkeypatch, offline):
    from tradingagents.memory import TradingMemoryLog

    result, _ = _run(tmp_path, monkeypatch, ScriptedModel())
    entries = TradingMemoryLog({"memory_log_path": adapter.DEFAULT_CONFIG["memory_log_path"]}).load_entries()

    assert any(entry["ticker"] == "NVDA" and entry["date"] == TRADE_DATE for entry in entries)
    assert result.final_state["final_rating"] == "Overweight"
