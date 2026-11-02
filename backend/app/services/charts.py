from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from typing import Any

from ..models import Decision, RunSection

LEVEL_LABELS = {
    "entry": (
        "entry",
        "entry price",
        "entry zone",
        "buy price",
        "buy zone",
    ),
    "take_profit": (
        "exit",
        "exit price",
        "exit target",
        "price target",
        "target",
        "target price",
        "take profit",
        "take-profit",
        "take profit price",
    ),
    "stop_loss": (
        "stop",
        "stop price",
        "stop loss",
        "stop-loss",
        "protective stop",
    ),
}
SOURCE_LABELS = {
    "portfolio_manager": "Final decision",
    "research_manager": "Research decision",
    "trader": "Trading plan",
}

# What a rating asks of a holder: add (long), keep (hold), or cut (reduce).
DIRECTIONS = {"buy": "long", "overweight": "long", "hold": "hold", "underweight": "reduce", "sell": "reduce"}
DIRECTION_NAMES = {"long": "Long setup", "hold": "Hold", "reduce": "Reduce / sell setup"}
# The same three numbers mean different things on each side: on an Underweight
# the "entry" is where to sell into a bounce and the "target" is the downside.
LEVEL_NAMES = {
    "long": {"entry": "Entry", "take_profit": "Take profit", "stop_loss": "Stop loss"},
    "reduce": {"entry": "Sell / trim at", "take_profit": "Downside target", "stop_loss": "Reassess above"},
    "hold": {"entry": "Add at", "take_profit": "Upside target", "stop_loss": "Exit below"},
}
_PROPOSAL = re.compile(r"FINAL TRANSACTION PROPOSAL:\W*(BUY|SELL|HOLD)", re.I)
# A number followed by one of these is a count, a ratio or a size, not a price.
_NOT_A_PRICE = re.compile(
    r"\s*(%|x\b|×|atr|r\b|:\d|[-/]\d|days?\b|weeks?\b|months?\b|years?\b|sessions?\b|bars?\b|"
    r"shares?\b|units?\b|lots?\b|m\b|b\b|bn\b|k\b|million|billion)",
    re.I,
)
# Levels further than this from the price are misreads ("2 ATR"), not plans.
_PLAUSIBLE = (0.3, 3.0)


_DATE = re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")


def _number(value: str) -> float | None:
    value = _DATE.sub(" ", value)
    for match in re.finditer(r"(?<![\w.])-?\$?\d[\d,]*(?:\.\d+)?", value):
        if _NOT_A_PRICE.match(value[match.end():]):
            continue
        parsed = float(match.group(0).replace(",", "").replace("$", ""))
        if parsed > 0:
            return parsed
    return None


def _label_kind(value: str) -> str | None:
    clean = re.sub(r"[^a-z -]", " ", value.lower())
    clean = re.sub(r"\s+", " ", clean).strip(" -")
    for kind, labels in LEVEL_LABELS.items():
        if clean in labels:
            return kind
    return None


def _explicit_levels(markdown: str) -> Iterable[tuple[str, float]]:
    """Yield prices only when a report explicitly labels their strategy role."""
    plain = markdown.replace("**", "").replace("__", "").replace("`", "")
    for raw_line in plain.splitlines():
        line = raw_line.strip().lstrip("-* ")
        if not line:
            continue
        if "|" in line:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) >= 2:
                kind = _label_kind(cells[0])
                price = _number(cells[1])
                if kind and price is not None:
                    yield kind, price
            continue
        match = re.match(r"^([^:=]{2,40})\s*[:=]\s*(.+)$", line)
        if not match:
            continue
        kind = _label_kind(match.group(1))
        price = _number(match.group(2))
        if kind and price is not None:
            yield kind, price


def strategy_levels(
    decision: Decision | None,
    sections: Iterable[RunSection],
) -> list[dict[str, Any]]:
    """Build display-only chart levels without changing the canonical decision."""
    levels: dict[str, dict[str, Any]] = {}
    if decision is not None:
        for kind, value in (
            ("entry", decision.entry_price),
            ("take_profit", decision.price_target),
            ("stop_loss", decision.stop_loss),
        ):
            if value is not None and value > 0:
                levels[kind] = {
                    "kind": kind,
                    "price": float(value),
                    "source": "Saved decision",
                }

    prioritized = sorted(
        sections,
        key=lambda section: ("portfolio_manager", "research_manager", "trader").index(
            section.section_key
        )
        if section.section_key in SOURCE_LABELS
        else 99,
    )
    for section in prioritized:
        if section.section_key not in SOURCE_LABELS:
            continue
        for kind, price in _explicit_levels(section.content_md):
            levels.setdefault(
                kind,
                {
                    "kind": kind,
                    "price": price,
                    "source": SOURCE_LABELS[section.section_key],
                },
            )
    return [levels[kind] for kind in ("entry", "take_profit", "stop_loss") if kind in levels]


def decision_direction(decision: Decision | None, sections: Iterable[RunSection]) -> str | None:
    """Long, hold or reduce: the final rating's, else the trader's closing proposal."""
    if decision is not None and decision.rating:
        direction = DIRECTIONS.get(decision.rating.strip().lower())
        if direction:
            return direction
    for section in sections:
        if section.section_key == "trader":
            found = _PROPOSAL.findall(section.content_md or "")
            if found:
                return {"buy": "long", "sell": "reduce", "hold": "hold"}[found[-1].lower()]
    return None


def _price(value: float) -> str:
    return f"{value:,.2f}" if value >= 1 else f"{value:.6g}"


def plan_levels(
    levels: list[dict[str, Any]],
    direction: str | None,
    reference: float | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Name each level for the decision's side and drop the ones that contradict it.

    The levels come from different agents (the trader's entry, the manager's
    target), so their geometry is checked before they are drawn: a long setup
    needs stop < entry < target, a reduce setup target < entry < stop. Without an
    entry the analysis-day close stands in for it. Returns the levels to draw and
    a note for each one left out.
    """
    notes: list[str] = []
    kept: dict[str, dict[str, Any]] = {}
    for level in levels:
        low, high = _PLAUSIBLE
        if reference and not low * reference <= level["price"] <= high * reference:
            notes.append(f"{_price(level['price'])} ({level['source'].lower()}) is too far from the "
                         f"{_price(reference)} close to be a level, so it is not drawn.")
            continue
        kept[level["kind"]] = level
    side = direction or "hold"
    names = LEVEL_NAMES[side]
    anchor = kept["entry"]["price"] if "entry" in kept else reference
    anchor_name = "entry" if "entry" in kept else "analysis-day close"
    if anchor is not None and side in ("long", "reduce"):
        sign = 1 if side == "long" else -1
        for kind, wanted in (("take_profit", 1), ("stop_loss", -1)):
            level = kept.get(kind)
            if level is None or (level["price"] - anchor) * sign * wanted > 0:
                continue
            where = "below" if level["price"] < anchor else "above"
            notes.append(f"The {names[kind].lower()} of {_price(level['price'])} ({level['source'].lower()}) "
                         f"is {where} the {_price(anchor)} {anchor_name}, which contradicts a "
                         f"{DIRECTION_NAMES[side].lower()}, so it is not drawn.")
            del kept[kind]
    drawn = []
    for kind in ("entry", "take_profit", "stop_loss"):
        if kind not in kept:
            continue
        label = names[kind]
        if side == "hold" and reference is not None:
            above = kept[kind]["price"] >= reference
            label = {"take_profit": "Upside target" if above else "Downside reference",
                     "stop_loss": "Exit below" if not above else "Reassess above",
                     "entry": "Add at"}[kind]
        drawn.append({**kept[kind], "label": label})
    return drawn, notes


def analysis_outcome(
    candles: list[dict[str, Any]],
    trade_date: date,
    levels: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """What the price did after the analysis date: the facts to judge the call by.

    None when the chart starts after the analysis date. The reference is the last
    close on or before that date (a weekend run uses Friday's close).
    """
    day = trade_date.isoformat()
    upto = [i for i, candle in enumerate(candles) if str(candle["time"]) <= day]
    if not upto:
        return None
    base = candles[upto[-1]]
    after = candles[upto[-1] + 1:]
    reference = float(base["close"])
    latest = candles[-1]
    outcome: dict[str, Any] = {
        "date": str(base["time"]),
        "close": reference,
        "latest_date": str(latest["time"]),
        "latest_close": float(latest["close"]),
        "change_pct": (float(latest["close"]) / reference - 1) * 100,
        "sessions": len(after),
        "high": None, "high_date": None, "low": None, "low_date": None,
        "touches": [],
    }
    if after:
        top = max(after, key=lambda candle: candle["high"])
        bottom = min(after, key=lambda candle: candle["low"])
        outcome.update(high=float(top["high"]), high_date=str(top["time"]),
                       low=float(bottom["low"]), low_date=str(bottom["time"]))
    for level in levels:
        price = level["price"]
        above = price >= reference
        hit = next((candle for candle in after
                    if (candle["high"] >= price if above else candle["low"] <= price)), None)
        outcome["touches"].append({"kind": level["kind"], "label": level.get("label"), "price": price,
                                   "date": str(hit["time"]) if hit else None})
    return outcome
