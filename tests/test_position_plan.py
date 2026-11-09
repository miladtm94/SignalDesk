"""The Trader's and Portfolio Manager's scaled position plan.

Entries and exits come in tranches: Buys sized as a share of the available cash,
Sells as a share of the position. One unreadable tranche must not cost the whole
decision, and the rendered table keeps fixed columns, since readers and the
console's chart both read it.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.schemas import (
    POSITION_PLAN_OUTPUT,
    PortfolioDecision,
    PortfolioRating,
    TraderAction,
    TraderProposal,
    TrancheSide,
    render_pm_decision,
    render_trader_proposal,
)
from tradingagents.agents.trader.trader import create_trader

PLAN = [
    {"side": "Buy", "price": 182.5, "size_pct": "4%", "trigger": "pullback to the 10 EMA"},
    {"side": "Buy", "price": 176, "size_pct": 3, "trigger": "50-day SMA | support"},
    {"side": "Sell", "price": 205, "size_pct": 50, "trigger": "prior swing high"},
    {"side": "Sell", "price": "$104,523.45", "size_pct": 25, "trigger": "measured move"},
]


@pytest.mark.unit
def test_an_unreadable_tranche_is_dropped_and_the_decision_kept():
    decision = PortfolioDecision(
        rating="Overweight", executive_summary="s", investment_thesis="t",
        tranches=[*PLAN, {"side": "Buy", "price": "around 170", "size_pct": 3, "trigger": "x"},
                  {"side": "Buy", "price": 170, "size_pct": 0, "trigger": "x"},
                  {"side": "Sell", "price": 210, "size_pct": 150, "trigger": "x"}],
    )
    assert [(t.side, t.price, t.size_pct) for t in decision.tranches] == [
        (TrancheSide.BUY, 182.5, 4.0), (TrancheSide.BUY, 176.0, 3.0),
        (TrancheSide.SELL, 205.0, 50.0), (TrancheSide.SELL, 104523.45, 25.0)]


@pytest.mark.unit
def test_the_plan_renders_as_a_table_with_what_it_adds_up_to():
    rendered = render_pm_decision(PortfolioDecision(
        rating="Overweight", executive_summary="s", investment_thesis="t",
        price_target=205, stop_loss="168", tranches=PLAN))

    assert "**Stop Loss**: 168.0" in rendered
    assert "| Tranche | Side | Price | Size | Trigger |" in rendered
    assert "| 1 | Buy | 182.5 | 4% of available cash | pullback to the 10 EMA |" in rendered
    # A pipe in the trigger would split the row into an extra column.
    assert "| 2 | Buy | 176 | 3% of available cash | 50-day SMA / support |" in rendered
    assert "| 4 | Sell | 104523.45 | 25% of position | measured move |" in rendered
    assert ("Buys commit 7% of available cash. Sells take off 75% of the position; "
            "the remaining 25% is left to the stop or the time horizon.") in rendered


@pytest.mark.unit
def test_a_proposal_without_tranches_says_so():
    rendered = render_trader_proposal(TraderProposal(action=TraderAction.HOLD, reasoning="r"))
    assert "**Position Plan**: not provided" in rendered
    assert rendered.rstrip().endswith("FINAL TRANSACTION PROPOSAL: **HOLD**")


@pytest.mark.unit
def test_the_schema_asks_for_tranches_without_exclusive_bounds():
    schema = convert_to_openai_tool(PortfolioDecision)["function"]["parameters"]
    tranche = schema["properties"]["tranches"]["items"]
    assert set(tranche["properties"]) == {"side", "price", "size_pct", "trigger"}
    assert "exclusiveMinimum" not in str(schema) and "exclusiveMaximum" not in str(schema)


def _capturing_llm(captured: dict, result):
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: captured.__setitem__("prompt", prompt) or result
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


@pytest.mark.unit
def test_trader_and_manager_are_asked_for_the_same_table():
    captured = {}
    create_trader(_capturing_llm(captured, TraderProposal(action="Buy", reasoning="x", tranches=PLAN)))({
        "company_of_interest": "NVDA", "investment_plan": "**Recommendation**: Buy",
        "market_report": "Current price $189.5; ATR 4.2.",
    })
    trader_prompt = "\n".join(m["content"] for m in captured["prompt"])
    assert POSITION_PLAN_OUTPUT in trader_prompt and "percentage of the available cash" in trader_prompt

    risk = {"history": "h", "aggressive_history": "a", "conservative_history": "c", "neutral_history": "n",
            "current_aggressive_response": "", "current_conservative_response": "",
            "current_neutral_response": "", "latest_speaker": "Neutral", "count": 1}
    result = create_portfolio_manager(_capturing_llm(captured, PortfolioDecision(
        rating=PortfolioRating.OVERWEIGHT, executive_summary="x", investment_thesis="y", tranches=PLAN)))({
        "company_of_interest": "NVDA", "risk_debate_state": risk,
        "investment_plan": "plan", "trader_investment_plan": "trader plan",
    })
    assert POSITION_PLAN_OUTPUT in captured["prompt"]
    assert "| 3 | Sell | 205 | 50% of position | prior swing high |" in result["final_trade_decision"]


@pytest.mark.unit
@pytest.mark.parametrize(("written", "stance"), [
    ("Wait for pullback", "Wait for pullback"), ("wait for a pullback", "Wait for pullback"),
    ("WAIT_FOR_BREAKOUT", "Wait for breakout"), ("buy now", "Buy now"), ("Take profits.", "Take profit"),
    ("avoid", "Avoid"), ("something else", None), (None, None),
])
def test_the_action_now_is_read_in_the_words_models_write_it(written, stance):
    decision = PortfolioDecision(rating="Buy", executive_summary="s", investment_thesis="t", action_now=written)
    assert (decision.action_now.value if decision.action_now else None) == stance


@pytest.mark.unit
def test_a_buy_can_wait_for_its_entry_and_the_render_says_so():
    decision = PortfolioDecision(rating="Buy", executive_summary="s", investment_thesis="t",
                                 action_now="Wait for pullback")
    lines = render_pm_decision(decision).splitlines()
    assert lines[0] == "**Rating**: Buy"
    assert "**Action Now**: Wait for pullback" in lines
    unset = render_pm_decision(PortfolioDecision(rating="Hold", executive_summary="s", investment_thesis="t"))
    assert "**Action Now**: not provided" in unset
