"""What the Street thinks of a stock: rating and price-target changes, consensus,
estimates and their revisions, the earnings record, short interest and ownership.

The data research sites show on a stock's "forecast" and "statistics" pages,
from Yahoo Finance. Dated records (each rating change, each reported quarter)
are served up to the analysis date, so a past run sees the ones made by then,
and the price targets of the firms that acted in the six months before it give
that date its own consensus. Snapshots Yahoo keeps only for today (consensus
targets, estimates and revisions, short interest, ownership) are withheld for a
past date, as the company profile is (#1300). Fork-owned (SignalDesk).
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import pandas as pd
import yfinance as yf

from tradingagents.dataflows.date_window import get_current_date, is_historical
from tradingagents.dataflows.errors import VendorUnavailableError
from tradingagents.dataflows.symbols import normalize_symbol
from tradingagents.dataflows.vendors.yahoo.common import price_text, yf_retry

logger = logging.getLogger(__name__)

RATING_WINDOW_DAYS = 120  # rating changes listed
TARGET_WINDOW_DAYS = 180  # a firm's last target counts toward the dated consensus this long
MAX_RATING_ROWS = 15
ACTIONS = {"up": "Upgrade", "down": "Downgrade", "init": "Initiated", "main": "Maintained", "reit": "Reiterated"}
_LIVE_ONLY = ("the consensus target, the recommendation trend, EPS and revenue estimates and their "
              "revisions, short interest and ownership")


def _fetch(canonical: str, attribute: str):
    # A Ticker of its own per request: a failed fetch stays cached on the Ticker.
    return yf_retry(lambda: getattr(yf.Ticker(canonical), attribute))


def _num(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= size:
            return f"{value / size:,.2f}{suffix}"
    return f"{value:,.0f}"


def _price(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.2f}" if abs(value) >= 1 else price_text(value)


def _eps(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}" if abs(value) >= 0.1 else f"{value:.3f}"


def _count(value: Any) -> str:
    value = _num(value)
    return "n/a" if value is None else str(int(value))


def _growth(row) -> str:
    # Growth on a loss-making year-ago figure is a sign flip, not a rate.
    base = _num(row.get("yearAgoEps"))
    return "n/a" if base is not None and base <= 0 else _pct(_num(row.get("growth")))


def _pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:+.{digits}f}%"


def _naive_day(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_localize(None)  # the exchange's own calendar day
    return stamp.normalize()


def _rating_changes(canonical: str, as_of: pd.Timestamp) -> list[str]:
    data = _fetch(canonical, "upgrades_downgrades")
    if data is None or getattr(data, "empty", True):
        return []
    frame = data.copy()
    frame.index = [_naive_day(day) for day in frame.index]
    frame = frame[frame.index <= as_of].sort_index(ascending=False)
    if frame.empty:
        return []
    lines: list[str] = []

    # The dated consensus: each firm's latest target in the six months to the date.
    recent = frame[frame.index > as_of - timedelta(days=TARGET_WINDOW_DAYS)]
    targets = recent[pd.to_numeric(recent.get("currentPriceTarget"), errors="coerce") > 0] \
        if "currentPriceTarget" in recent else recent.iloc[0:0]
    latest = targets[~targets["Firm"].duplicated()] if "Firm" in targets else targets
    values = pd.to_numeric(latest.get("currentPriceTarget"), errors="coerce").dropna() if len(latest) else []
    if len(values):
        lines += [f"### Price targets set in the {TARGET_WINDOW_DAYS} days to {as_of.date()}", "",
                  f"- {len(values)} firm(s), each at its latest target: median {_price(values.median())}, "
                  f"mean {_price(values.mean())}, range {_price(values.min())}–{_price(values.max())}",
                  ""]

    window = frame[frame.index > as_of - timedelta(days=RATING_WINDOW_DAYS)]
    if window.empty:
        return lines
    counts = window["Action"].map(ACTIONS).fillna(window["Action"]).value_counts() if "Action" in window else None
    tally = ", ".join(f"{name} {count}" for name, count in counts.items()) if counts is not None else ""
    lines += [f"### Rating and price-target changes in the {RATING_WINDOW_DAYS} days to {as_of.date()}", "",
              f"- {len(window)} change(s): {tally}" if tally else f"- {len(window)} change(s)", "",
              "| Date | Firm | Action | Rating | Price target |", "|---|---|---|---|---|"]
    for day, row in window.head(MAX_RATING_ROWS).iterrows():
        before, after = str(row.get("FromGrade") or "").strip(), str(row.get("ToGrade") or "").strip()
        grade = f"{before} → {after}" if before and before != after else after or "—"
        prior, current = _num(row.get("priorPriceTarget")), _num(row.get("currentPriceTarget"))
        target = "—"
        if current:
            target = f"{_price(prior)} → {_price(current)}" if prior and prior != current else _price(current)
        action = ACTIONS.get(str(row.get("Action")), str(row.get("Action") or "—"))
        lines.append(f"| {day.date()} | {row.get('Firm', '—')} | {action} | {grade} | {target} |")
    if len(window) > MAX_RATING_ROWS:
        lines.append(f"| … | {len(window) - MAX_RATING_ROWS} earlier change(s) not listed | | | |")
    lines.append("")
    return lines


def _earnings_record(canonical: str, as_of: pd.Timestamp, live: bool) -> list[str]:
    data = _fetch(canonical, "earnings_dates")
    if data is None or getattr(data, "empty", True):
        return []
    frame = data.copy()
    # Exchange-local release times: a report after the 16:00 close is news for
    # the next session, not for a run dated that day.
    released = [pd.Timestamp(day).tz_localize(None) if pd.Timestamp(day).tzinfo else pd.Timestamp(day)
                for day in frame.index]
    frame.index = [day.normalize() for day in released]
    known = pd.Series([day < as_of + timedelta(hours=16) for day in released], index=frame.index)
    order = frame.index.argsort(kind="stable")
    frame, known = frame.iloc[order], known.iloc[order]
    estimate, reported = frame.get("EPS Estimate"), frame.get("Reported EPS")
    if reported is None:
        return []
    done = frame[known.to_numpy() & reported.notna().to_numpy()].tail(8)
    lines: list[str] = []
    if not done.empty:
        surprise = done.get("Surprise(%)")
        beats = int((done["Reported EPS"] > done["EPS Estimate"]).sum()) if estimate is not None else 0
        lines += [f"### Earnings record (reported by {as_of.date()})", "",
                  f"- Beat the EPS estimate in {beats} of the last {len(done)} quarters", "",
                  "| Reported | EPS estimate | Reported EPS | Surprise |", "|---|---:|---:|---:|"]
        for day, row in done.iloc[::-1].iterrows():
            shock = _num(surprise.loc[day]) if surprise is not None else None
            if isinstance(shock, pd.Series):
                shock = _num(shock.iloc[0])
            lines.append(f"| {day.date()} | {_eps(_num(row.get('EPS Estimate')))} | "
                         f"{_eps(_num(row.get('Reported EPS')))} | {'n/a' if shock is None else f'{shock:+.1f}%'} |")
        lines.append("")
    if live:
        upcoming = frame[(frame.index > as_of) & reported.isna()]
        if not upcoming.empty:
            day = upcoming.index[0]
            guess = _num(upcoming.iloc[0].get("EPS Estimate"))
            days = (day - as_of).days
            lines += [f"- **Next earnings: {day.date()}** (in {days} days)"
                      + (f", EPS estimate {_eps(guess)}" if guess is not None else "")
                      + ". A position held through it carries the earnings gap risk.", ""]
    return lines


def _consensus(canonical: str, info: dict) -> list[str]:
    lines: list[str] = []
    targets = _fetch(canonical, "analyst_price_targets") or {}
    current, mean = _num(targets.get("current")), _num(targets.get("mean"))
    if mean:
        upside = f" ({_pct(mean / current - 1)} from {_price(current)})" if current else ""
        lines += ["### Consensus today", "",
                  f"- Price target: mean {_price(mean)}{upside}, median {_price(_num(targets.get('median')))}, "
                  f"low {_price(_num(targets.get('low')))}, high {_price(_num(targets.get('high')))}; "
                  f"{info.get('numberOfAnalystOpinions') or 'n/a'} analysts"]
    if info.get("averageAnalystRating") or info.get("recommendationKey"):
        lines.append(f"- Rating: {info.get('averageAnalystRating') or info.get('recommendationKey')} "
                     "(1 = Strong Buy … 5 = Strong Sell)")
    trend = _fetch(canonical, "recommendations_summary")
    if trend is not None and not getattr(trend, "empty", True):
        months = {"0m": "This month", "-1m": "1 month ago", "-2m": "2 months ago", "-3m": "3 months ago"}
        lines += ["", "| Recommendations | Strong buy | Buy | Hold | Sell | Strong sell |", "|---|---:|---:|---:|---:|---:|"]
        for _, row in trend.iterrows():
            lines.append(f"| {months.get(row.get('period'), row.get('period'))} | {row.get('strongBuy', 0)} | "
                         f"{row.get('buy', 0)} | {row.get('hold', 0)} | {row.get('sell', 0)} | {row.get('strongSell', 0)} |")
    return [*lines, ""] if lines else []


def _estimates(canonical: str) -> list[str]:
    periods = {"0q": "Current quarter", "+1q": "Next quarter", "0y": "Current year", "+1y": "Next year"}
    lines: list[str] = []
    eps, revenue = _fetch(canonical, "earnings_estimate"), _fetch(canonical, "revenue_estimate")
    if eps is not None and not getattr(eps, "empty", True):
        lines += ["### Estimates today", "", "| Period | EPS (avg, low–high) | EPS growth | Revenue (avg) | Revenue growth | Analysts |",
                  "|---|---|---:|---:|---:|---:|"]
        for period, row in eps.iterrows():
            if _num(row.get("avg")) is None:
                continue  # no analyst covers this period
            rev = revenue.loc[period] if revenue is not None and period in getattr(revenue, "index", []) else None
            lines.append(
                f"| {periods.get(period, period)} | {_eps(_num(row.get('avg')))} ({_eps(_num(row.get('low')))}–{_eps(_num(row.get('high')))}) | "
                f"{_growth(row)} | {_money(_num(rev.get('avg'))) if rev is not None else 'n/a'} | "
                f"{_pct(_num(rev.get('growth'))) if rev is not None else 'n/a'} | {_count(row.get('numberOfAnalysts'))} |")
        lines.append("")
    trend, revisions = _fetch(canonical, "eps_trend"), _fetch(canonical, "eps_revisions")
    if trend is not None and not getattr(trend, "empty", True):
        lines += ["| EPS estimate drift | Now | 30 days ago | 90 days ago | Change over 90 days | Revisions up / down (30 days) |",
                  "|---|---:|---:|---:|---:|---|"]
        for period, row in trend.iterrows():
            if _num(row.get("current")) is None:
                continue
            now, then, old = _num(row.get("current")), _num(row.get("30daysAgo")), _num(row.get("90daysAgo"))
            moved = _pct(now / old - 1) if now is not None and old else "n/a"
            ups = downs = "n/a"
            if revisions is not None and period in getattr(revisions, "index", []):
                ups = _count(revisions.loc[period].get("upLast30days"))
                downs = _count(revisions.loc[period].get("downLast30days"))
            lines.append(f"| {periods.get(period, period)} | {_eps(now)} | {_eps(then)} | {_eps(old)} | {moved} | {ups} / {downs} |")
        lines.append("")
    return lines


def _positioning(info: dict) -> list[str]:
    rows = []
    short, prior = _num(info.get("sharesShort")), _num(info.get("sharesShortPriorMonth"))
    if _num(info.get("shortPercentOfFloat")) is not None:
        change = f", {_pct(short / prior - 1)} on the month before" if short and prior else ""
        when = info.get("dateShortInterest")
        dated = f" (settled {datetime.fromtimestamp(when, UTC).date()})" if isinstance(when, (int, float)) else ""
        rows.append(f"- Short interest: {_num(info['shortPercentOfFloat']) * 100:.2f}% of the float, "
                    f"{_money(short)} shares{change}; {info.get('shortRatio', 'n/a')} days to cover{dated}")
    if _num(info.get("heldPercentInstitutions")) is not None:
        rows.append(f"- Held by institutions {_num(info['heldPercentInstitutions']) * 100:.1f}%, insiders "
                    f"{(_num(info.get('heldPercentInsiders')) or 0) * 100:.1f}%; float {_money(_num(info.get('floatShares')))} shares")
    multiples = [(label, _num(info.get(key))) for label, key in (
        ("EV/EBITDA", "enterpriseToEbitda"), ("EV/Revenue", "enterpriseToRevenue"),
        ("Price/Sales", "priceToSalesTrailing12Months"))]
    shown = [f"{label} {value:.1f}" for label, value in multiples if value is not None]
    if shown:
        rows.append(f"- Enterprise value {_money(_num(info.get('enterpriseValue')))}; " + ", ".join(shown))
    growth = [(label, _num(info.get(key))) for label, key in (
        ("revenue", "revenueGrowth"), ("earnings", "earningsGrowth"))]
    shown = [f"{label} {_pct(value)}" for label, value in growth if value is not None]
    if shown:
        rows.append("- Latest quarter, year on year: " + ", ".join(shown))
    return ["### Positioning and valuation today", "", *rows, ""] if rows else []


def get_analyst_coverage(
    ticker: Annotated[str, "ticker symbol of the company"],
    as_of_date: Annotated[str | None, "analysis date, yyyy-mm-dd"] = None,
) -> str:
    """The analyst view of ``ticker`` known on ``as_of_date``, as markdown."""
    canonical = normalize_symbol(ticker)
    day = as_of_date or get_current_date()
    as_of = pd.Timestamp(day).normalize()
    live = not is_historical(day)
    sections: list[list[str]] = []
    failures = 0
    readers: list = [lambda: _rating_changes(canonical, as_of), lambda: _earnings_record(canonical, as_of, live)]
    if live:
        info_box: dict = {}

        def info() -> dict:
            if "info" not in info_box:
                info_box["info"] = _fetch(canonical, "info") or {}
            return info_box["info"]

        readers += [lambda: _consensus(canonical, info()), lambda: _estimates(canonical),
                    lambda: _positioning(info())]
    for read in readers:
        try:
            sections.append(read())
        except VendorUnavailableError as exc:
            failures += 1
            logger.warning("Analyst coverage for %s: one section failed: %s", canonical, exc)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            # A changed Yahoo shape loses one section, not the report.
            logger.warning("Analyst coverage for %s: unreadable section: %s", canonical, exc)
    body = [line for section in sections for line in section]
    if not body:
        if failures == len(readers):
            raise VendorUnavailableError("Yahoo Finance did not answer any analyst-coverage request")
        return (f"No analyst coverage found for {canonical} as of {day}: Yahoo lists no rating changes, "
                "reported earnings or consensus for it (common for ETFs, funds and small listings).")
    header = [f"# Analyst coverage for {canonical}", f"# As of: {day}", ""]
    if not live:
        header += [f"Withheld for {day}: {_LIVE_ONLY} are today's snapshot, so serving them would leak "
                   "post-decision information. Rating changes, price targets and earnings are served as dated.", ""]
    if failures:
        header += [f"Note: {failures} section(s) could not be fetched from Yahoo Finance and are missing.", ""]
    return "\n".join(header + body).rstrip() + "\n"
