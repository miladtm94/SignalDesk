"""Pydantic schemas used by agents that produce structured output.

The framework's primary artifact is still prose: each agent's natural-language
reasoning is what users read in the saved markdown reports and what the
downstream agents read as context.  Structured output is layered onto the
three decision-making agents (Research Manager, Trader, Portfolio Manager)
so that:

- Their outputs follow consistent section headers across runs and providers
- Each provider's native structured-output mode is used (json_schema for
  OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic)
- Schema field descriptions become the model's output instructions, freeing
  the prompt body to focus on context and the rating-scale guidance
- A render helper turns the parsed Pydantic instance back into the same
  markdown shape the rest of the system already consumes, so display,
  memory log, and saved reports keep working unchanged
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

# LLMs sometimes write a placeholder string ("None", "N/A", ...) into an optional
# numeric field instead of omitting it. Coerce those to None so the structured
# call validates instead of erroring (#1058). Pydantic still parses real numeric
# strings ("189.5") to float.
_NULLISH_FLOAT = {"", "none", "n/a", "na", "null", "nil", "-", "tbd", "unknown"}


def _coerce_optional_float(value):
    """Normalise an LLM-written optional numeric field before validation.

    Three shapes show up in practice: a placeholder string ("None", "N/A") in
    place of an omitted value (#1058); a percentage where a price was asked for
    ("15%", #1288); and a human-formatted price ("$1,234.50"). A percentage
    cannot be salvaged into an absolute level -- reading "15%" as 15 would put a
    stop at $15 on a $600 stock -- so it is dropped like a placeholder, leaving
    one bad field to null out instead of failing the whole proposal. A formatted
    price is reduced to its number.

    Anything that is not a single number is dropped the same way. A range
    ("150-160") or a hedge ("around 150") would otherwise reach pydantic, fail
    validation, and discard the whole decision, losing every field the model got
    right along with the price.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.lower() in _NULLISH_FLOAT or text.endswith("%"):
        return None
    cleaned = text.replace(",", "").lstrip("$€£¥").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Shared rating types
# ---------------------------------------------------------------------------


class PortfolioRating(StrEnum):
    """5-tier rating used by the Research Manager and Portfolio Manager."""

    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class TraderAction(StrEnum):
    """3-tier transaction direction used by the Trader.

    The Trader's job is to translate the Research Manager's investment plan
    into a concrete transaction proposal: should the desk execute a Buy, a
    Sell, or sit on Hold this round.  Position sizing and the nuanced
    Overweight / Underweight calls happen later at the Portfolio Manager.
    """

    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


class ExecutionStance(StrEnum):
    """What to do at today's price, apart from the rating's direction.

    A rating says which way the evidence points; the stance says whether the
    price is where to act on it. A Buy whose price is extended above its buy
    zone is still a Wait for pullback, so the call and its timing stay apart.
    """

    BUY_NOW = "Buy now"
    WAIT_FOR_PULLBACK = "Wait for pullback"
    WAIT_FOR_BREAKOUT = "Wait for breakout"
    HOLD = "Hold"
    TAKE_PROFIT = "Take profit"
    EXIT = "Exit"
    AVOID = "Avoid"


# How models write a stance in its own words, read onto the enum.
_STANCE_WORDS = {
    "buy": ExecutionStance.BUY_NOW, "buy now": ExecutionStance.BUY_NOW, "enter now": ExecutionStance.BUY_NOW,
    "add now": ExecutionStance.BUY_NOW, "wait for a pullback": ExecutionStance.WAIT_FOR_PULLBACK,
    "wait for pullback": ExecutionStance.WAIT_FOR_PULLBACK, "wait for a breakout": ExecutionStance.WAIT_FOR_BREAKOUT,
    "wait for breakout": ExecutionStance.WAIT_FOR_BREAKOUT, "hold": ExecutionStance.HOLD,
    "take profit": ExecutionStance.TAKE_PROFIT, "take profits": ExecutionStance.TAKE_PROFIT,
    "trim": ExecutionStance.TAKE_PROFIT, "exit": ExecutionStance.EXIT, "sell": ExecutionStance.EXIT,
    "avoid": ExecutionStance.AVOID,
}


def _read_stance(value):
    """A stance in any case or common wording, else None: a stance nobody can
    read must not fail the decision that carries the rating."""
    if value is None or isinstance(value, ExecutionStance):
        return value
    text = " ".join(str(value).replace("-", " ").replace("_", " ").split()).strip(" .").lower()
    return _STANCE_WORDS.get(text) or next((stance for stance in ExecutionStance if stance.value.lower() == text), None)


STANCE_DESCRIPTION = (
    "What to do at today's price, separate from the rating: Buy now when the price "
    "is in the buy zone; Wait for pullback when the case is good but the price is "
    "extended above the buy zone; Wait for breakout when entry needs a weekly close "
    "above resistance; Hold to keep a position without adding; Take profit to trim "
    "into resistance; Exit to sell the position; Avoid when there is no setup for "
    "new money. A Buy or Overweight can still be Wait for pullback, and not every "
    "chart is a buy or a sell. Read it from the weekly and monthly price structure "
    "in the technical report."
)


class TrancheSide(StrEnum):
    """Which way one step of a scaled plan trades."""

    BUY = "Buy"
    SELL = "Sell"


class Tranche(BaseModel):
    """One step of a scaled entry or exit: a price and how much trades there.

    Scaling in and out spreads the timing risk of a single fill: part of the
    size is committed where the setup is confirmed, more on a pullback to
    support, and profit is taken in steps so a reversal cannot take it all back.
    """

    side: TrancheSide = Field(description="Buy adds units at this price; Sell takes units off.")
    price: float = Field(
        description=(
            "The tranche's price as an absolute number in the instrument's quote "
            "currency (e.g. 182.5), never a percentage or a range."
        ),
    )
    # Bounds are checked by the validator, not the JSON schema: not every
    # provider's structured mode accepts exclusive bounds.
    size_pct: float = Field(
        description=(
            "How much trades at this price, as a percentage. For a Buy, the share "
            "of the available cash (the portfolio context's cash when given, else "
            "the capital the caller has available) committed here, so the Buy "
            "tranches together are the whole allocation (e.g. 4, 3 and 3 for a 10% "
            "position). For a Sell, the share of the full position (the units "
            "already held plus every Buy in this plan) sold here; the Sell "
            "tranches total at most 100."
        ),
    )
    trigger: str = Field(
        description=(
            "Why this price: the level or condition it is tied to, in a few words, "
            "e.g. 'pullback to the 50-day SMA support' or 'daily close above the "
            "195 resistance'."
        ),
    )

    @field_validator("price", mode="before")
    @classmethod
    def _price_is_a_number(cls, v):
        return _coerce_optional_float(v)

    @field_validator("size_pct", mode="before")
    @classmethod
    def _size_is_a_number(cls, v):
        # "40%" is the natural way to write this field, unlike a price.
        if isinstance(v, str):
            v = _coerce_optional_float(v.strip().rstrip("%"))
        if isinstance(v, (int, float)) and not 0 < v <= 100:
            raise ValueError("a tranche's size is a percentage above 0 and at most 100")
        return v


def _usable_tranches(value):
    """Drop a tranche whose price or size cannot be read, keeping the rest.

    One "around 150" in a list of three tranches would otherwise fail the
    whole decision, losing the rating and the levels the model got right.
    """
    if not isinstance(value, list):
        return value
    kept = []
    for item in value:
        try:
            kept.append(Tranche.model_validate(item))
        except (ValidationError, TypeError):
            continue
    return kept


TRANCHES_DESCRIPTION = (
    "The position plan as scaled tranches, ordered by when they would trade. "
    "Buy tranches scale in (each a share of the available cash) and Sell "
    "tranches scale out (each a share of the full position). For a long, "
    "stagger two or three Buys at support or confirmation levels from the "
    "technical report and two or three Sells at targets above them. To reduce "
    "or exit, list Sells into strength (and any re-entry Buy below). For a Hold, "
    "add-on Buys and trim Sells may both appear, or none. Size each tranche so "
    "that a fill of every Buy followed by the stop costs a loss the caller can "
    "afford. Leave the list empty if no price levels can be stated."
)


# The prompt text the Trader and Portfolio Manager share, so the free-text
# fallback writes the same table the structured render does.
SCALING_GUIDANCE = (
    "Plan the position in tranches rather than one fill: scale in with two or "
    "three Buys at the confirmation and support levels the technical report "
    "names, and scale out with Sells at successive targets, so neither the "
    "timing of one fill nor one reversal decides the result. Size each Buy as a "
    "percentage of the available cash (the portfolio context's cash when given) "
    "and each Sell as a percentage of the position, and keep the loss if every "
    "Buy fills and the stop is then hit to what the conviction justifies. "
)
POSITION_PLAN_OUTPUT = (
    "**Position Plan**: the tranches as a table with the columns "
    "Tranche | Side | Price | Size | Trigger -- Side is Buy or Sell, Price an "
    "absolute price, Size a Buy's percentage of available cash or a Sell's "
    "percentage of the position (e.g. '4% of available cash', '50% of "
    "position'), Trigger the level or condition behind it"
)


def render_position_plan(tranches: list[Tranche]) -> str:
    """The tranches as a table, with what they add up to.

    The table is what readers, the downstream agents and the console's chart
    read, so its columns stay fixed: Tranche | Side | Price | Size | Trigger.
    """
    if not tranches:
        return "**Position Plan**: not provided"
    rows = [
        "**Position Plan**:",
        "",
        "| Tranche | Side | Price | Size | Trigger |",
        "| --- | --- | --- | --- | --- |",
    ]
    for number, tranche in enumerate(tranches, 1):
        basis = "of available cash" if tranche.side is TrancheSide.BUY else "of position"
        trigger = " ".join(tranche.trigger.split()).replace("|", "/")
        # .10g keeps a crypto price's cents and a currency pair's pips.
        rows.append(f"| {number} | {tranche.side.value} | {tranche.price:.10g} | "
                    f"{tranche.size_pct:.4g}% {basis} | {trigger} |")
    bought = sum(t.size_pct for t in tranches if t.side is TrancheSide.BUY)
    sold = sum(t.size_pct for t in tranches if t.side is TrancheSide.SELL)
    totals = []
    if bought:
        totals.append(f"Buys commit {bought:.4g}% of available cash.")
    if sold:
        rest = max(0.0, 100 - sold)
        totals.append(f"Sells take off {sold:.4g}% of the position"
                      + (f"; the remaining {rest:.4g}% is left to the stop or the time horizon." if rest else "."))
    if totals:
        rows.extend(["", " ".join(totals)])
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Research Manager
# ---------------------------------------------------------------------------


class ResearchPlan(BaseModel):
    """Structured investment plan produced by the Research Manager.

    Hand-off to the Trader: the recommendation pins the directional view,
    the rationale captures which side of the bull/bear debate carried the
    argument, and the strategic actions translate that into concrete
    instructions the trader can execute against.
    """

    recommendation: PortfolioRating = Field(
        description=(
            "The investment recommendation. Exactly one of Buy / Overweight / "
            "Hold / Underweight / Sell. Conflicting arguments alone are not a "
            "reason to Hold: commit to the stronger side, sized by how "
            "decisively it wins. Choose Hold only when the evidence is still "
            "balanced after weighing, or too thin to support a call."
        ),
    )
    rationale: str = Field(
        description=(
            "Conversational summary of the key points from both sides of the "
            "debate, ending with which arguments led to the recommendation. "
            "Speak naturally, as if to a teammate."
        ),
    )
    strategic_actions: str = Field(
        description=(
            "Concrete steps for the trader to implement the recommendation, "
            "including sizing guidance relative to a standard allocation. The "
            "research team does not see the caller's holdings; the trader and "
            "portfolio manager apply the actual position."
        ),
    )


def render_research_plan(plan: ResearchPlan) -> str:
    """Render a ResearchPlan to markdown for storage and the trader's prompt context."""
    return "\n".join([
        f"**Recommendation**: {plan.recommendation.value}",
        "",
        f"**Rationale**: {plan.rationale}",
        "",
        f"**Strategic Actions**: {plan.strategic_actions}",
    ])


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------


class TraderProposal(BaseModel):
    """Structured transaction proposal produced by the Trader.

    The trader reads the Research Manager's investment plan and the analyst
    reports, then turns them into a concrete transaction: what action to
    take, the reasoning that justifies it, and the practical levels for
    entry, stop-loss, and sizing.
    """

    action: TraderAction = Field(
        description="The transaction direction. Exactly one of Buy / Hold / Sell.",
    )
    reasoning: str = Field(
        description=(
            "The case for this action, anchored in the analysts' reports and "
            "the research plan. Two to four sentences."
        ),
    )
    entry_price: float | None = Field(
        default=None,
        description=(
            "Optional entry price target as an absolute number in the instrument's "
            "quote currency (e.g. 189.5), never a percentage or a range. Omit it "
            "if you cannot state a specific level."
        ),
    )
    stop_loss: float | None = Field(
        default=None,
        description=(
            "Optional stop-loss as an absolute price in the instrument's quote "
            "currency (e.g. 172.0), never a percentage. Convert a percentage "
            "distance to the price level it implies, or omit it."
        ),
    )
    position_sizing: str | None = Field(
        default=None,
        description="Optional sizing guidance, e.g. '5% of portfolio'.",
    )
    tranches: list[Tranche] = Field(default_factory=list, description=TRANCHES_DESCRIPTION)

    @field_validator("entry_price", "stop_loss", mode="before")
    @classmethod
    def _nullish_float_to_none(cls, v):
        return _coerce_optional_float(v)

    @field_validator("tranches", mode="before")
    @classmethod
    def _readable_tranches(cls, v):
        return _usable_tranches(v)


def render_trader_proposal(proposal: TraderProposal) -> str:
    """Render a TraderProposal to markdown.

    The trailing ``FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`` line is
    preserved for backward compatibility with the analyst stop-signal text
    and any external code that greps for it.
    """
    parts = [
        f"**Action**: {proposal.action.value}",
        "",
        f"**Reasoning**: {proposal.reasoning}",
    ]
    # Named even when absent, so a reader can tell a level the trader chose not
    # to give from one the schema never asked for.
    for label, value in (("Entry Price", proposal.entry_price),
                         ("Stop Loss", proposal.stop_loss),
                         ("Position Sizing", proposal.position_sizing)):
        parts.extend(["", f"**{label}**: {value if value is not None and value != '' else 'not provided'}"])
    parts.extend([
        "",
        render_position_plan(proposal.tranches),
        "",
        f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**",
    ])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Portfolio Manager
# ---------------------------------------------------------------------------


class PortfolioDecision(BaseModel):
    """Structured output produced by the Portfolio Manager.

    The model fills every field as part of its primary LLM call; no separate
    extraction pass is required. Field descriptions double as the model's
    output instructions, so the prompt body only needs to convey context and
    the rating-scale guidance.
    """

    rating: PortfolioRating = Field(
        description=(
            "The final position rating. Exactly one of Buy / Overweight / Hold / "
            "Underweight / Sell, picked based on the analysts' debate. "
            "Conflicting arguments alone are not a reason to Hold: commit to the "
            "stronger side, sized by how decisively it wins. Choose Hold only "
            "when the evidence is still balanced after weighing, or too thin to "
            "support a call."
        ),
    )
    executive_summary: str = Field(
        description=(
            "A concise action plan covering entry strategy, position sizing, "
            "key risk levels, and time horizon. Two to four sentences."
        ),
    )
    investment_thesis: str = Field(
        description=(
            "Detailed reasoning anchored in specific evidence from the analysts' "
            "debate. If prior lessons are referenced in the prompt context, "
            "incorporate them; otherwise rely solely on the current analysis."
        ),
    )
    price_target: float | None = Field(
        default=None,
        description="Optional target price in the instrument's quote currency.",
    )
    time_horizon: str | None = Field(
        default=None,
        description="Optional recommended holding period, e.g. '3-6 months'.",
    )
    stop_loss: float | None = Field(
        default=None,
        description=(
            "Optional price at which whatever the plan holds is exited, as an "
            "absolute number in the quote currency, never a percentage. For a "
            "long it sits below every Buy tranche; to reduce, it is the level "
            "above which the case for selling is reassessed."
        ),
    )
    tranches: list[Tranche] = Field(default_factory=list, description=TRANCHES_DESCRIPTION)
    action_now: ExecutionStance | None = Field(default=None, description=STANCE_DESCRIPTION)

    @field_validator("action_now", mode="before")
    @classmethod
    def _readable_stance(cls, v):
        return _read_stance(v)

    @field_validator("price_target", "stop_loss", mode="before")
    @classmethod
    def _nullish_float_to_none(cls, v):
        return _coerce_optional_float(v)

    @field_validator("tranches", mode="before")
    @classmethod
    def _readable_tranches(cls, v):
        return _usable_tranches(v)


def render_pm_decision(decision: PortfolioDecision) -> str:
    """Render a PortfolioDecision back to the markdown shape the rest of the system expects.

    Memory log, CLI display, and saved report files all read this markdown,
    so the rendered output preserves the exact section headers (``**Rating**``,
    ``**Executive Summary**``, ``**Investment Thesis**``) that downstream
    parsers and the report writers already handle.
    """
    parts = [
        f"**Rating**: {decision.rating.value}",
        "",
        f"**Action Now**: {decision.action_now.value if decision.action_now else 'not provided'}",
        "",
        f"**Executive Summary**: {decision.executive_summary}",
        "",
        f"**Investment Thesis**: {decision.investment_thesis}",
    ]
    # Named even when absent: a missing line reads as a field nobody asked for,
    # so a reader cannot tell "no target" from "target not reported".
    target = decision.price_target if decision.price_target is not None else "not provided"
    parts.extend(["", f"**Price Target**: {target}"])
    stop = decision.stop_loss if decision.stop_loss is not None else "not provided"
    parts.extend(["", f"**Stop Loss**: {stop}"])
    parts.extend(["", f"**Time Horizon**: {decision.time_horizon or 'not provided'}"])
    parts.extend(["", render_position_plan(decision.tranches)])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sentiment Analyst
# ---------------------------------------------------------------------------


class SentimentBand(StrEnum):
    """Discrete sentiment direction produced by the Sentiment Analyst.

    Six tiers keep the signal granular enough to be actionable while remaining
    small enough for every provider to map reliably from its JSON output.
    """

    BULLISH = "Bullish"
    MILDLY_BULLISH = "Mildly Bullish"
    NEUTRAL = "Neutral"
    MIXED = "Mixed"
    MILDLY_BEARISH = "Mildly Bearish"
    BEARISH = "Bearish"


class SentimentReport(BaseModel):
    """Structured sentiment report produced by the Sentiment Analyst.

    Replaces the previous free-form prose output so downstream consumers
    (dashboards, audit logs, PDF renderers, other agents) can read
    ``overall_band`` and ``overall_score`` without maintaining fragile regex
    fallbacks that drift with every model release. ``narrative`` preserves the
    rich source-by-source analysis; ``render_sentiment_report`` prepends a
    deterministic header so the saved report stays human-readable.
    """

    overall_band: SentimentBand = Field(
        description=(
            "Overall sentiment direction. Exactly one of: "
            "Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. "
            "Use Mixed when sources point in clearly different directions. "
            "Use Neutral only when all sources are genuinely silent or non-committal."
        ),
    )
    overall_score: float = Field(
        ge=0.0,
        le=10.0,
        description=(
            "Numeric sentiment intensity on a 0–10 scale. "
            "0 = maximally bearish, 5 = neutral, 10 = maximally bullish. "
            "Guideline for consistency with overall_band: "
            "Bullish ~6.5–10, Mildly Bullish ~5.5–6.4, Neutral/Mixed ~4.5–5.5, "
            "Mildly Bearish ~3.5–4.4, Bearish ~0–3.4. "
            "Only the 0–10 bounds are enforced."
        ),
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description=(
            "Confidence in the assessment based on data quality and sample size. "
            "Use 'low' when one or more sources returned a placeholder or fewer "
            "than 5 data points; 'medium' when data is present but sparse; "
            "'high' when all three sources returned substantive data."
        ),
    )
    narrative: str = Field(
        description=(
            "Full sentiment report covering, in order: "
            "(1) source-by-source breakdown with specific evidence (cite message "
            "counts, ratios, notable posts); "
            "(2) cross-source divergences and alignments; "
            "(3) dominant narrative themes; "
            "(4) catalysts and risks surfaced by the data; "
            "(5) a markdown table summarising key sentiment signals, their "
            "direction, source, and supporting evidence. "
            "Keep it informative and substantive: develop each section thoroughly "
            "with concrete evidence so every point adds new signal for the trader."
        ),
    )


def render_sentiment_report(report: SentimentReport) -> str:
    """Render a SentimentReport to the markdown shape the rest of the system expects.

    The structured header (band + score + confidence) is prepended to the
    narrative so the saved report is both human-readable and machine-parseable
    without regex.
    """
    return "\n".join([
        f"**Overall Sentiment:** **{report.overall_band.value}** "
        f"(Score: {report.overall_score:.1f}/10)",
        f"**Confidence:** {report.confidence.capitalize()}",
        "",
        report.narrative,
    ])
