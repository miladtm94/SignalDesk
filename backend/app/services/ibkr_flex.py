"""Holdings from Interactive Brokers, through the Flex Web Service.

A Flex Query is a statement the account holder defines in IBKR's Client Portal
(Performance & Reports > Flex Queries); the Flex Web Service hands it to any
client holding the query's ID and the account's Flex token, with no TWS or
gateway running. Two sections of it make a book: Open Positions (units and the
cost basis per unit) and Cash Report (cash in the base currency). The book comes
back for the person to review in Holdings before it is saved; nothing here
writes it. The token can fetch statements and nothing else.
"""

from __future__ import annotations

import logging
import os
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
TOKEN_NAME, QUERY_NAME = "IBKR_FLEX_TOKEN", "IBKR_FLEX_QUERY_ID"
TIMEOUT = 30
# IBKR builds the statement after the request; these codes mean "not yet" or
# "slow down", the rest are final (a bad token, an expired one, a bad query).
RETRY_CODES = {"1001", "1004", "1005", "1006", "1007", "1008", "1009", "1018", "1019", "1021"}
WAITS = (2, 3, 5, 8, 10, 10, 10)

# IBKR's listing exchange to Yahoo's symbol suffix. US venues take none.
US_VENUES = {"NASDAQ", "NYSE", "ARCA", "AMEX", "BATS", "BYX", "IEX", "PINK", "NYSENAT", "ISLAND", "MEMX",
             "CBOE", "PHLX", "OTC", "NYSEARCA", "BEX", "EDGX", "CHX", "PSX", "SMART"}
SUFFIX = {
    "ASX": ".AX", "CHIXAU": ".AX", "NZE": ".NZ", "SEHK": ".HK", "TSEJ": ".T", "SGX": ".SI", "KSE": ".KS",
    "NSE": ".NS", "LSE": ".L", "LSEETF": ".L", "TSE": ".TO", "VENTURE": ".V", "IBIS": ".DE", "IBIS2": ".DE",
    "XETRA": ".DE", "FWB": ".F", "FWB2": ".F", "SWB": ".SG", "SBF": ".PA", "AEB": ".AS", "ENEXT.BE": ".BR",
    "EBS": ".SW", "BVME": ".MI", "BM": ".MC", "SFB": ".ST", "OSE": ".OL", "CPH": ".CO", "HEX": ".HE",
    "BVL": ".LS", "VSE": ".VI", "WSE": ".WA", "TASE": ".TA", "MEXI": ".MX", "BOVESPA": ".SA", "JSE": ".JO",
}
# Instruments SignalDesk analyses; options, futures, bonds and the like are left out.
KEPT = {"STK", "CRYPTO"}
CATEGORY_NAMES = {"OPT": "options", "FOP": "futures options", "FUT": "futures", "WAR": "warrants",
                  "BOND": "bonds", "BILL": "bills", "CASH": "currency pairs", "CFD": "CFDs",
                  "FUND": "mutual funds", "CMDTY": "metals", "IOPT": "structured products"}


class IbkrFlexError(RuntimeError):
    """IBKR refused or failed the request; the message says what to fix."""


def configured() -> bool:
    return bool(os.getenv(TOKEN_NAME) and os.getenv(QUERY_NAME))


def _get(url: str, params: dict[str, str], token: str) -> str:
    try:
        response = requests.get(url, params=params, timeout=TIMEOUT, headers={"User-Agent": "SignalDesk"})
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        # requests quotes the full URL, token included, in its errors.
        reason = str(exc).replace(token, "***") if token else str(exc)
    raise IbkrFlexError(f"IBKR's Flex Web Service could not be reached ({reason}).")


def _parse(text: str) -> ET.Element:
    try:
        return ET.fromstring(text.strip())
    except ET.ParseError as exc:
        raise IbkrFlexError("IBKR answered with something other than a Flex statement.") from exc


def _status(root: ET.Element) -> tuple[str, str, str]:
    return (root.findtext("Status") or "", root.findtext("ErrorCode") or "", root.findtext("ErrorMessage") or "")


def fetch_statement(token: str, query_id: str, get: Callable[[str, dict, str], str] | None = None,
                    sleep: Callable[[float], None] | None = None) -> ET.Element:
    """Ask IBKR to build the query's statement, then collect it once it is ready."""
    get, sleep = get or _get, sleep or time.sleep
    root = _parse(get(f"{BASE_URL}/SendRequest", {"t": token, "q": query_id, "v": "3"}, token))
    status, code, message = _status(root)
    if status != "Success":
        raise IbkrFlexError(f"IBKR refused the Flex request ({code or status}: {message or 'no reason given'}).")
    reference = root.findtext("ReferenceCode") or ""
    url = root.findtext("Url") or f"{BASE_URL}/GetStatement"
    for wait in WAITS:
        sleep(wait)
        root = _parse(get(url, {"t": token, "q": reference, "v": "3"}, token))
        if root.tag == "FlexQueryResponse":
            return root
        status, code, message = _status(root)
        if code not in RETRY_CODES:
            raise IbkrFlexError(f"IBKR could not return the statement ({code or status}: {message or 'no reason'}).")
        logger.info("IBKR Flex statement not ready (%s); retrying in %ss", code, wait)
    raise IbkrFlexError("IBKR is still building the statement. Try again in a minute.")


def yahoo_symbol(symbol: str, exchange: str, category: str, currency: str) -> tuple[str, str | None]:
    """IBKR's symbol and listing exchange as Yahoo writes it, with a note when it is a guess."""
    symbol, exchange = symbol.strip().upper(), exchange.strip().upper()
    if category == "CRYPTO":
        return f"{symbol}-{currency or 'USD'}", None
    if exchange in US_VENUES or (not exchange and currency == "USD"):
        return symbol.replace(" ", "-").replace(".", "-"), None
    suffix = SUFFIX.get(exchange)
    if suffix is None:
        guess = symbol.replace(" ", "-")
        return guess, f"{symbol} lists on {exchange or 'an unnamed exchange'}, which SignalDesk does not map; check its Yahoo symbol."
    if suffix == ".HK" and symbol.isdigit():
        symbol = symbol.zfill(4)
    # IBKR writes share classes with a space or a dot (RCI.B, BT.A); Yahoo with a dash.
    return symbol.rstrip(".").replace(" ", "-").replace(".", "-") + suffix, None


def _number(element: ET.Element, *names: str) -> float | None:
    for name in names:
        value = element.get(name)
        if value not in (None, ""):
            try:
                return float(value.replace(",", ""))
            except ValueError:
                continue
    return None


def _positions(statement: ET.Element, notes: list[str]) -> list[dict[str, Any]]:
    rows = statement.findall("./OpenPositions/OpenPosition")
    # A query with lots lists each lot and a summary of them; the summary is the position.
    summaries = [row for row in rows if (row.get("levelOfDetail") or "SUMMARY").upper() == "SUMMARY"]
    rows = summaries or rows
    skipped: dict[str, int] = {}
    held: dict[str, dict[str, Any]] = {}
    for row in rows:
        category = (row.get("assetCategory") or "STK").upper()
        if category not in KEPT:
            skipped[category] = skipped.get(category, 0) + 1
            continue
        quantity = _number(row, "position", "quantity")
        if not quantity:
            continue
        currency = (row.get("currency") or "").upper() or None
        ticker, note = yahoo_symbol(row.get("symbol") or "", row.get("listingExchange") or "", category, currency or "")
        if not ticker:
            continue
        if note:
            notes.append(note)
        cost = _number(row, "costBasisPrice", "openPrice")
        entry = held.setdefault(ticker, {"ticker": ticker, "quantity": 0.0, "cost": 0.0, "costed": 0.0,
                                         "currency": currency})
        entry["quantity"] += quantity
        if cost and cost > 0:
            entry["cost"] += cost * abs(quantity)
            entry["costed"] += abs(quantity)
    for category, count in sorted(skipped.items()):
        notes.append(f"Left out {count} {CATEGORY_NAMES.get(category, category)} position(s): "
                     "SignalDesk analyses stocks, ETFs and crypto.")
    positions = []
    for entry in held.values():
        if not entry["quantity"]:
            continue
        average = entry["cost"] / entry["costed"] if entry["costed"] else None
        positions.append({"ticker": entry["ticker"], "quantity": round(entry["quantity"], 8),
                          "average_price": round(average, 6) if average else None, "currency": entry["currency"]})
    return sorted(positions, key=lambda p: p["ticker"])


def _base_currency(statement: ET.Element) -> str | None:
    info = statement.find("./AccountInformation")
    if info is not None and info.get("currency"):
        return info.get("currency").upper()
    for row in statement.findall("./OpenPositions/OpenPosition"):
        if row.get("fxRateToBase") in ("1", "1.0") and row.get("currency"):
            return row.get("currency").upper()
    return None


def _cash(statement: ET.Element) -> tuple[float | None, bool]:
    report = statement.find("./CashReport")
    if report is None:
        return None, False
    for row in report.findall("./CashReportCurrency"):
        if (row.get("currency") or "").upper() == "BASE_SUMMARY":
            return _number(row, "endingCash", "endingSettledCash"), True
    return None, True


def book_from_statement(root: ET.Element) -> dict[str, Any]:
    """The statement's accounts as one book: positions summed by ticker, cash in the base currency."""
    statements = root.findall(".//FlexStatement")
    if not statements:
        raise IbkrFlexError("The Flex statement holds no account.")
    if all(statement.find("./OpenPositions") is None for statement in statements):
        raise IbkrFlexError("This Flex Query has no Open Positions section. In IBKR's Client Portal, open "
                            "Performance & Reports > Flex Queries, edit the query, add Open Positions (and "
                            "Cash Report for your cash), save it and try again.")
    notes: list[str] = []
    positions: list[dict[str, Any]] = []
    cash_total, any_cash, cash_section = 0.0, False, False
    currencies = set()
    for statement in statements:
        positions += _positions(statement, notes)
        amount, present = _cash(statement)
        cash_section |= present
        if amount is not None:
            cash_total, any_cash = cash_total + amount, True
        currencies.add(_base_currency(statement))
    if len(statements) > 1:
        merged: dict[str, dict[str, Any]] = {}
        for position in positions:
            entry = merged.setdefault(position["ticker"], {**position, "quantity": 0.0, "_cost": 0.0, "_n": 0.0})
            entry["quantity"] += position["quantity"]
            if position["average_price"]:
                entry["_cost"] += position["average_price"] * abs(position["quantity"])
                entry["_n"] += abs(position["quantity"])
        positions = [{"ticker": e["ticker"], "quantity": round(e["quantity"], 8), "currency": e["currency"],
                      "average_price": round(e["_cost"] / e["_n"], 6) if e["_n"] else None}
                     for e in merged.values() if e["quantity"]]
        notes.append(f"Combined {len(statements)} accounts into one book.")
    currencies.discard(None)
    currency = next(iter(currencies)) if len(currencies) == 1 else None
    if len(currencies) > 1:
        notes.append(f"The accounts report in different base currencies ({', '.join(sorted(currencies))}); "
                     "the cash is their sum, so set the book's currency yourself.")
    cash = None
    if any_cash:
        cash = cash_total
        if cash < 0:
            notes.append(f"Cash is negative ({cash:,.2f}: a margin loan), so it is saved as 0; "
                         "the agents then size new positions from no spare cash.")
            cash = 0.0
    elif not cash_section:
        notes.append("The query has no Cash Report section, so cash is left blank; add it to bring in your cash.")
    if currency is None and cash is not None and len(currencies) <= 1:
        notes.append("The statement does not name its base currency; add Account Information to the query, "
                     "or set the currency yourself.")
    first = statements[0]
    account = first.get("accountId") or ""
    return {
        "cash": round(cash, 2) if cash is not None else None, "currency": currency,
        "positions": sorted(positions, key=lambda p: p["ticker"]), "notes": list(dict.fromkeys(notes)),
        "account": f"{account[:1]}•••{account[-3:]}" if len(account) > 4 else account or None,
        "as_of": _iso(first.get("toDate")),
    }


def _iso(day: str | None) -> str | None:
    day = (day or "").strip()
    return f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 and day.isdigit() else day or None


def import_book() -> dict[str, Any]:
    """The book in the configured Flex Query, for review before it is saved."""
    token, query_id = os.getenv(TOKEN_NAME, "").strip(), os.getenv(QUERY_NAME, "").strip()
    if not token or not query_id:
        raise IbkrFlexError("Add your IBKR Flex token and query ID in Settings > Provider credentials "
                            f"({TOKEN_NAME}, {QUERY_NAME}) first.")
    return book_from_statement(fetch_statement(token, query_id))
