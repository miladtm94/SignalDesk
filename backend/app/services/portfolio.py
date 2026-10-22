"""The saved book: current holdings and cash the decision agents can work against.

Upstream keeps three states apart, and so does the console: a book with
positions, a flat book (saved, with no positions), and no book at all. A run
uses the book only when asked to, and records the exact book it was given.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.adapters.tradingagents_adapter import load_current_market_chart, normalize_portfolio

from ..database import SessionLocal
from ..models import AppPreference
from ..schemas import PortfolioWrite

PREFERENCE_KEY = "portfolio"


def load_book() -> tuple[dict[str, Any] | None, datetime | None]:
    """The saved book and when it was saved, or (None, None) when there is none."""
    with SessionLocal() as session:
        row = session.get(AppPreference, PREFERENCE_KEY)
        if row is None or not isinstance(row.value, dict) or "book" not in row.value:
            return None, None
        return row.value["book"], row.updated_at


def save_book(payload: PortfolioWrite) -> tuple[dict[str, Any], datetime]:
    """Validate the book as upstream reads it, then keep it; raises ValueError when unusable."""
    book = normalize_portfolio(payload.model_dump(mode="json"))
    with SessionLocal.begin() as session:
        row = session.get(AppPreference, PREFERENCE_KEY)
        if row is None:
            row = AppPreference(key=PREFERENCE_KEY, value={"book": book})
            session.add(row)
        else:
            row.value = {"book": book}
        session.flush()
        session.refresh(row)
        return book, row.updated_at


def clear_book() -> bool:
    """Forget the saved book, so runs have no portfolio context; True when there was one."""
    with SessionLocal.begin() as session:
        row = session.get(AppPreference, PREFERENCE_KEY)
        if row is None:
            return False
        session.delete(row)
        return True


def quotes(book: dict[str, Any]) -> list[dict[str, Any]]:
    """The latest daily close of every held instrument, each in its own quote currency."""
    result = []
    for position in book.get("positions", []):
        ticker = position["ticker"]
        try:
            chart = load_current_market_chart(ticker, limit=5)
            result.append({"ticker": ticker, "last_close": chart.candles[-1]["close"], "as_of": chart.as_of})
        except Exception as exc:
            result.append({"ticker": ticker, "last_close": None, "as_of": None,
                           "error": f"No current price ({type(exc).__name__})"})
    return result
