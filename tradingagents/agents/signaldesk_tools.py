"""The data tools SignalDesk adds to the analysts (fork-owned; upstream has none of them).

Kept apart from ``tools.py`` so an upstream release that reworks its tools does
not collide with these. Each takes the run's ``trade_date`` from graph state and
never serves data past it, whatever date the model asks for.
"""

from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from tradingagents.dataflows.date_window import as_of
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.price_structure import build_price_structure
from tradingagents.dataflows.router import no_data_available, vendor_unavailable
from tradingagents.dataflows.vendors import stockanalysis
from tradingagents.dataflows.vendors.yahoo.street import (
    get_analyst_coverage as yahoo_analyst_coverage,
)


@tool
def get_price_structure(
    symbol: Annotated[str, InjectedState("company_of_interest")],
    curr_date: Annotated[str, "the current trading date, YYYY-mm-dd"],
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
) -> str:
    """Weekly and monthly price structure, for positions held weeks to months.

    Returns, for weekly bars (10/40-week SMAs, the swing view) and monthly bars
    (10/20-month SMAs, the investor view): the trend, the swing pattern, ATR and
    RSI, support and resistance zones clustered from swing highs and lows, and a
    plan read from them -- the buy zone, take-profit zones, the breakout or
    trend-turn trigger, the invalidation level, and a stance for new money and
    for holders (in the buy zone, wait for a pullback, wait for a breakout, hold,
    take profit, or avoid). Use these zones to set entry, take-profit and stop
    prices.
    """
    try:
        return build_price_structure(symbol, as_of(curr_date, trade_date))
    except VendorUnavailableError as exc:
        return vendor_unavailable("get_price_structure", exc)
    except NoMarketDataError as exc:
        return no_data_available(exc)


@tool
def get_analyst_coverage(
    ticker: Annotated[str, InjectedState("company_of_interest")],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
    trade_date: Annotated[str, InjectedState("trade_date")] = "",
) -> str:
    """What sell-side analysts think, and the company's record against them.

    Returns the rating upgrades, downgrades and price-target changes of recent
    months with each firm's latest target (a consensus dated to the analysis
    day), the earnings record (estimate, reported EPS, surprise) and the next
    report date, and, for a run dated today, the consensus target and rating,
    the recommendation trend, EPS and revenue estimates with their 30- and
    90-day revisions, short interest, ownership, and EV multiples.
    """
    day = as_of(curr_date, trade_date)
    try:
        report = yahoo_analyst_coverage(ticker, day)
    except VendorUnavailableError as exc:
        return _stockanalysis_coverage(ticker, day) or vendor_unavailable("get_analyst_coverage", exc)
    # Yahoo silent or partly down: StockAnalysis fills in, for a run dated today.
    if report.startswith("No analyst coverage") or "could not be fetched" in report:
        backup = _stockanalysis_coverage(ticker, day)
        if backup:
            return backup if report.startswith("No analyst coverage") else f"{report}\n{backup}"
    return report


def _stockanalysis_coverage(ticker: str, day: str | None) -> str | None:
    """StockAnalysis's forecast page as the backup, or None when it cannot help."""
    try:
        return stockanalysis.get_analyst_coverage(ticker, day)
    except (VendorUnavailableError, NoMarketDataError):
        return None
