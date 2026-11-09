"""Holdings across accounts: an IBKR sync beside positions and cash kept elsewhere.

A broker sync replaces what came from that broker; a fund platform without a
sync (Betashares) is entered by hand and must survive it. Cash held in several
currencies is totalled in the book's currency at the day's rate, and the agents
read each account's part.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.adapters import tradingagents_adapter as adapter
from backend.app.main import app
from backend.app.services import portfolio as portfolio_service

MIXED = {
    "currency": "AUD",
    "positions": [
        {"ticker": "NVDA", "quantity": 20, "average_price": 180.07, "currency": "USD", "account": "IBKR"},
        {"ticker": "A200.AX", "quantity": 300, "average_price": 140.5, "account": " Betashares "},
        # The same instrument at two brokers is two holdings, not a duplicate.
        {"ticker": "NVDA", "quantity": 5, "average_price": 150, "currency": "USD", "account": "Betashares"},
    ],
    "cash_balances": [
        {"account": "IBKR", "amount": 168.38, "currency": "USD"},
        {"account": "Betashares", "amount": 100000, "currency": "AUD"},
    ],
}


class _Chart:
    def __init__(self, close):
        self.candles, self.as_of = [{"close": close}], "2026-11-07"


@pytest.fixture
def client(monkeypatch):
    asked = []

    def chart(ticker, limit=5):
        asked.append(ticker)
        if ticker == "USDAUD=X":
            return _Chart(1.5)
        raise LookupError(ticker)

    monkeypatch.setattr(portfolio_service, "load_current_market_chart", chart)
    with TestClient(app) as test_client:
        test_client.delete("/api/portfolio")
        test_client.asked = asked
        yield test_client
        test_client.delete("/api/portfolio")


def test_cash_in_two_currencies_is_totalled_in_the_book_s_currency(client):
    saved = client.put("/api/portfolio", json=MIXED)
    assert saved.status_code == 200, saved.text
    book = saved.json()
    assert book["cash"] == 100252.57 and book["currency"] == "AUD"  # 168.38 USD at 1.5 + 100,000 AUD
    assert book["cash_balances"] == [
        {"account": "IBKR", "amount": 168.38, "currency": "USD", "rate": 1.5},
        {"account": "Betashares", "amount": 100000.0, "currency": "AUD", "rate": None},
    ]
    assert [p["account"] for p in book["positions"]] == ["IBKR", "Betashares", "Betashares"]
    assert client.asked == ["USDAUD=X"]


def test_the_agents_read_where_each_holding_and_each_cash_balance_is(client):
    client.put("/api/portfolio", json=MIXED)
    context = client.get("/api/portfolio/preview", params={"ticker": "NVDA"}).json()["context"]
    assert "- Current position in NVDA: 20 units, average price 180.07 USD, held at IBKR" in context
    assert "- Current position in NVDA: 5 units, average price 150.00 USD, held at Betashares" in context
    assert "- Cash available: 100,252.57 AUD" in context
    assert ("- Cash by account: IBKR 168.38 USD (about 252.57 AUD); Betashares 100,000.00 AUD") in context


def test_a_ticker_twice_in_one_account_is_refused(client):
    twice = {**MIXED, "positions": MIXED["positions"] + [{"ticker": "A200.AX", "quantity": 1, "account": "betashares"}]}
    answer = client.put("/api/portfolio", json=twice)
    assert answer.status_code == 422 and "A200.AX is listed twice in betashares" in answer.text


def test_a_missing_exchange_rate_stops_the_save_and_says_what_to_do(client):
    hkd = {**MIXED, "cash_balances": [{"account": "Moomoo", "amount": 50, "currency": "HKD"}]}
    answer = client.put("/api/portfolio", json=hkd)
    assert answer.status_code == 400
    assert "HKD cash could not be converted to AUD" in answer.json()["detail"]
    assert "enter that cash in AUD" in answer.json()["detail"]


def test_mixed_cash_needs_a_book_currency(client):
    answer = client.put("/api/portfolio", json={**MIXED, "currency": None})
    assert answer.status_code == 400 and "name the book's currency" in answer.json()["detail"]
    # One currency is the book's currency without being named.
    single = client.put("/api/portfolio", json={"positions": [], "cash_balances": [{"amount": 10, "currency": "usd"}]})
    assert single.json()["currency"] == "USD" and single.json()["cash"] == 10


def test_a_book_without_accounts_is_stored_and_fingerprinted_as_before():
    plain = {"cash": 5000.0, "currency": "AUD", "positions": [
        {"ticker": "NVDA", "quantity": 20.0, "average_price": 180.07, "currency": "USD"}]}
    assert adapter.normalize_portfolio(plain) == plain
    # The fingerprints such books had before accounts existed, so their stopped runs resume.
    assert adapter.ConsolePortfolio.model_validate(plain).fingerprint() == "17c4c1c9b634"
    bare = {**plain, "positions": [{"ticker": "NVDA", "quantity": 20.0, "average_price": 180.07}]}
    assert adapter.ConsolePortfolio.model_validate(bare).fingerprint() == "f34b334ddaf1"
    with_account = {**plain, "positions": [{**plain["positions"][0], "account": "IBKR"}]}
    assert adapter.ConsolePortfolio.model_validate(with_account).fingerprint() != "17c4c1c9b634"


def test_a_ticker_held_twice_is_priced_once(client, monkeypatch):
    asked = []
    monkeypatch.setattr(portfolio_service, "load_current_market_chart",
                        lambda ticker, limit=5: asked.append(ticker) or _Chart(1.5))
    client.put("/api/portfolio", json=MIXED)
    asked.clear()
    quotes = client.get("/api/portfolio/quotes").json()
    assert [q["ticker"] for q in quotes] == ["NVDA", "A200.AX"] and asked == ["NVDA", "A200.AX"]
