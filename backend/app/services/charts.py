from __future__ import annotations

import re
from collections.abc import Iterable
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


def _number(value: str) -> float | None:
    match = re.search(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?", value)
    if not match:
        return None
    parsed = float(match.group(0).replace(",", ""))
    return parsed if parsed > 0 else None


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
