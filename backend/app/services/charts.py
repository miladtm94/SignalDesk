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


# The Portfolio Manager's "what to do at today's price", in the order the
# structured decision lists them.
STANCES = ("Buy now", "Wait for pullback", "Wait for breakout", "Hold", "Take profit", "Exit", "Avoid")
_ACTION_NOW = re.compile(r"^[\s>*_#-]*action now[\s*_]*[:=-]\s*(.+)$", re.I | re.M)


def decision_stance(sections: Iterable[RunSection]) -> str | None:
    """The final decision's Action Now line, for display; nothing read here is stored."""
    for section in sections:
        if section.section_key != "portfolio_manager":
            continue
        found = _ACTION_NOW.search((section.content_md or "").replace("**", "").replace("__", ""))
        if found:
            value = found.group(1).strip().lower().replace(" a ", " ")
            return next((stance for stance in STANCES if value.startswith(stance.lower())), None)
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


# --- the scaled position plan ------------------------------------------------------

_SIDES = {
    "buy": ("buy", "add", "accumulate", "enter", "long"),
    "sell": ("sell", "trim", "take profit", "take-profit", "exit", "reduce", "cover"),
}
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _column(names: list[str], *prefixes: str) -> int | None:
    return next((i for i, name in enumerate(names) if name.startswith(prefixes)), None)


def _tranche_rows(markdown: str) -> list[dict[str, Any]]:
    """The first table with Side, Price and Size columns, one tranche per readable row."""
    plain = markdown.replace("**", "").replace("__", "").replace("`", "")
    columns: dict[str, int | None] | None = None
    rows: list[dict[str, Any]] = []
    for raw_line in plain.splitlines():
        line = raw_line.strip()
        if "|" not in line:
            if rows:
                break
            columns = None
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        names = [cell.lower() for cell in cells]
        if columns is None:
            found = {"side": _column(names, "side"), "price": _column(names, "price"),
                     "size": _column(names, "size", "allocation"),
                     "trigger": _column(names, "trigger", "rationale", "condition", "why", "level")}
            if None not in (found["side"], found["price"], found["size"]):
                columns = found
            continue
        wanted = max(index for index in columns.values() if index is not None)
        if all(set(cell) <= set("-: ") for cell in cells) or len(cells) <= wanted:
            continue
        side = next((key for key, words in _SIDES.items() if names[columns["side"]].startswith(words)), None)
        price = _number(cells[columns["price"]])
        size = _PERCENT.search(cells[columns["size"]])
        if side is None or price is None or size is None or not 0 < float(size.group(1)) <= 100:
            continue
        trigger = cells[columns["trigger"]] if columns["trigger"] is not None else ""
        rows.append({"side": side, "price": price, "size_pct": float(size.group(1)), "trigger": trigger})
    return rows


def plan_tranches(sections: Iterable[RunSection]) -> tuple[list[dict[str, Any]], str | None]:
    """The scaled plan the run states: the final decision's tranches, else the trader's.

    Read from the rendered table for display only; nothing parsed here is stored.
    """
    content = {section.section_key: section.content_md or "" for section in sections}
    for key in ("portfolio_manager", "trader"):
        rows = _tranche_rows(content.get(key, ""))
        if rows:
            return rows, SOURCE_LABELS[key]
    return [], None


def _touched(candle: dict[str, Any], price: float, reference: float) -> bool:
    """Whether a bar traded through a price, approached from the analysis close's side."""
    return candle["high"] >= price if price >= reference else candle["low"] <= price


def _replay(tranches: list[dict[str, Any]], stop: float | None, after: list[dict[str, Any]],
            reference: float, held: float) -> dict[str, Any]:
    """The plan traded on the bars since the analysis, in notional units.

    Tranches fill at their price once a bar trades through it. A Sell takes its
    share of the largest position held so far, so a Buy that never filled shrinks
    every Sell in proportion and the runner is kept. A protective stop
    exits everything held at the stop, or at the open on a gap through it, and
    ends the plan; a bar that touched both a tranche and the stop is read as the
    stop coming last, which is the cautious reading of an unknown order.
    """
    units, spent, proceeds, peak = held, 0.0, 0.0, held
    filled: dict[int, str] = {}
    stopped = None
    for candle in after:
        for number, tranche in enumerate(tranches, 1):
            if number in filled or not _touched(candle, tranche["price"], reference):
                continue
            if tranche["side"] == "buy":
                bought = tranche["size_pct"] / tranche["price"]
                units, spent = units + bought, spent + tranche["size_pct"]
                peak = max(peak, units)
            else:
                sold = min(units, tranche["size_pct"] / 100 * peak)
                if sold <= 0:
                    continue  # nothing held yet: the Sell waits for a Buy to fill
                units, proceeds = units - sold, proceeds + sold * tranche["price"]
            filled[number] = str(candle["time"])
        if stop is not None and units > 0 and candle["low"] <= stop:
            exit_price = min(float(candle["open"]), stop)
            proceeds, units = proceeds + units * exit_price, 0.0
            stopped = {"date": str(candle["time"]), "price": exit_price}
            break
    latest = float(after[-1]["close"])
    result = proceeds + units * latest - spent - held * reference
    return {"filled": [{"number": number, "date": day} for number, day in filled.items()],
            "stopped": stopped, "result_pct": result, "latest_close": latest,
            "sessions": len(after), "open_pct": units * latest}


def position_plan(
    tranches: list[dict[str, Any]],
    source: str | None,
    levels: list[dict[str, Any]],
    direction: str | None,
    candles: list[dict[str, Any]],
    trade_date: date,
    book: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The tranches checked against the price the analysis saw, with what they add up to.

    Figures are percentages: a plan that buys is measured per 100 of available
    cash, one that only sells per 100 of the position's value at the analysis
    close. Amounts come from the run's book only where they cannot mix
    currencies: the cash a Buy commits, and the units a Sell takes off a held
    position.
    """
    if not tranches:
        return None
    day = trade_date.isoformat()
    upto = [i for i, candle in enumerate(candles) if str(candle["time"]) <= day]
    reference = float(candles[upto[-1]]["close"]) if upto else float(candles[0]["close"])
    after = candles[upto[-1] + 1:] if upto else []
    notes: list[str] = []
    kept: list[dict[str, Any]] = []
    low, high = _PLAUSIBLE
    for tranche in tranches:
        if not low * reference <= tranche["price"] <= high * reference:
            notes.append(f"A {tranche['side']} at {_price(tranche['price'])} is too far from the "
                         f"{_price(reference)} close to be a level, so it is left out.")
            continue
        kept.append(tranche)
    if not kept:
        return {"source": source, "reference": reference, "tranches": [], "summary": None,
                "replay": None, "notes": notes}

    stop = next((level["price"] for level in levels if level["kind"] == "stop_loss"), None)
    # A stop below the price protects a holding; above it, on a reduce, it only
    # marks where the case for selling is reassessed, and nothing trades there.
    protective = stop if stop is not None and stop < reference and direction != "reduce" else None
    buys = [t for t in kept if t["side"] == "buy"]
    sells = [t for t in kept if t["side"] == "sell"]
    committed = sum(t["size_pct"] for t in buys)
    sold = sum(t["size_pct"] for t in sells)
    # A reduce plan is about a position already held: its Sells are measured
    # against that position, and a re-entry or breakout Buy (a share of cash,
    # not of the position) is shown but left out of the figures.
    reducing = direction == "reduce" and bool(sells)
    measured = sells if reducing else kept
    # Notional: 100 of cash for a plan that buys, else a position worth 100 at the close.
    held = 100 / reference if reducing or not buys else 0.0
    bought = 0.0 if reducing else sum(t["size_pct"] / t["price"] for t in buys)
    full = held + bought
    average = committed / bought if bought else None
    cost = held * reference + committed
    basis = cost / full if full else None

    cash = (book or {}).get("cash")
    held_units = (book or {}).get("units") or 0.0
    rows = []
    for number, tranche in enumerate(kept, 1):
        reached = next((str(c["time"]) for c in after if _touched(c, tranche["price"], reference)), None)
        row = {**tranche, "number": number, "distance_pct": (tranche["price"] / reference - 1) * 100,
               "reached": reached, "amount": None, "units": None}
        if tranche["side"] == "buy" and cash is not None:
            row["amount"] = cash * tranche["size_pct"] / 100
        if tranche["side"] == "sell" and (reducing or not buys) and held_units > 0:
            row["units"] = held_units * tranche["size_pct"] / 100
        rows.append(row)
        if protective is not None and tranche["side"] == "buy" and tranche["price"] <= protective:
            notes.append(f"Buy {number} at {_price(tranche['price'])} is at or below the "
                         f"{_price(protective)} stop: it fills only after the stop has exited.")
        if average is not None and tranche["side"] == "sell" and tranche["price"] < average:
            notes.append(f"Sell {number} at {_price(tranche['price'])} is below the "
                         f"{_price(average)} average entry, so it would lock in a loss.")
    if reducing and buys:
        names = ", ".join(str(row["number"]) for row in rows if row["side"] == "buy")
        notes.append(f"Buy {names} is left out of the figures and the replay: they measure the reduction "
                     "against the position held.")
    if committed > 100:
        notes.append(f"The Buys add up to {committed:g}% of available cash, more than there is.")
    if sold > 100:
        notes.append(f"The Sells add up to {sold:g}% of the position, more than it holds.")

    risk = full * (basis - protective) if protective is not None and basis and basis > protective else None
    reward = sum(t["size_pct"] / 100 * full * (t["price"] - basis) for t in sells) if basis and sells else None
    summary = {
        "basis": "position" if reducing or not buys else "cash", "committed_pct": committed, "sold_pct": sold,
        "remaining_pct": max(0.0, 100 - sold) if sells else None, "average_entry": average,
        "stop": stop, "protective_stop": protective is not None,
        "risk_pct": risk, "reward_pct": reward,
        "reward_risk": reward / risk if risk and reward and reward > 0 else None,
        "cash": cash, "currency": (book or {}).get("currency"), "held_units": held_units or None,
    }
    replay = _replay(measured, protective, after, reference, held) if after else None
    if replay is not None:
        # What putting the whole allocation on at the analysis close, and keeping
        # it, would have made over the same sessions (for a reduce, holding the
        # whole position): the plan's comparison.
        replay["all_at_close_pct"] = (100 if held else committed) * (replay["latest_close"] / reference - 1)
        if reducing:
            # Fills are numbered as the table shows them.
            numbers = [row["number"] for row in rows if row["side"] == "sell"]
            replay["filled"] = [{**fill, "number": numbers[fill["number"] - 1]} for fill in replay["filled"]]
    return {"source": source, "reference": reference, "tranches": rows, "summary": summary,
            "replay": replay, "notes": notes}
