"""Holdings from an IBKR Flex Query: request, wait, read, and never leak the token."""
from __future__ import annotations

import pytest
import requests
from fastapi.testclient import TestClient

from backend.app.api import portfolio as portfolio_api
from backend.app.main import app
from backend.app.services import ibkr_flex

SENT = """<FlexStatementResponse timestamp="09 November, 2026 10:00 AM EST">
<Status>Success</Status><ReferenceCode>8123</ReferenceCode>
<Url>https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement</Url>
</FlexStatementResponse>"""
BUILDING = """<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode>
<ErrorMessage>Statement generation in progress. Please try again shortly.</ErrorMessage></FlexStatementResponse>"""
STATEMENT = """<FlexQueryResponse queryName="SignalDesk" type="AF"><FlexStatements count="1">
<FlexStatement accountId="U1234567" fromDate="20261106" toDate="20261106" period="LastBusinessDay">
<AccountInformation accountId="U1234567" currency="AUD" />
<OpenPositions>
<OpenPosition assetCategory="STK" symbol="NXT" listingExchange="ASX" currency="AUD" fxRateToBase="1"
  position="250" costBasisPrice="12.40" levelOfDetail="SUMMARY" />
<OpenPosition assetCategory="STK" symbol="NXT" listingExchange="ASX" currency="AUD" position="250"
  costBasisPrice="12.40" levelOfDetail="LOT" />
<OpenPosition assetCategory="STK" symbol="BRK B" listingExchange="NYSE" currency="USD" fxRateToBase="1.53"
  position="4" costBasisPrice="455.10" levelOfDetail="SUMMARY" />
<OpenPosition assetCategory="STK" symbol="700" listingExchange="SEHK" currency="HKD" position="100"
  costBasisPrice="380" levelOfDetail="SUMMARY" />
<OpenPosition assetCategory="CRYPTO" symbol="BTC" listingExchange="PAXOS" currency="USD" position="0.05"
  costBasisPrice="61000" levelOfDetail="SUMMARY" />
<OpenPosition assetCategory="OPT" symbol="NVDA  261218C00200000" listingExchange="CBOE" currency="USD"
  position="1" costBasisPrice="12" levelOfDetail="SUMMARY" />
</OpenPositions>
<CashReport>
<CashReportCurrency currency="BASE_SUMMARY" endingCash="8421.55" />
<CashReportCurrency currency="AUD" endingCash="6000" />
</CashReport>
</FlexStatement></FlexStatements></FlexQueryResponse>"""


def _ibkr(answers):
    calls = []

    def get(url, params, token):
        calls.append((url.rsplit("/", 1)[-1], dict(params)))
        return answers.pop(0)

    return get, calls


def test_the_statement_is_requested_then_collected_once_built():
    get, calls = _ibkr([SENT, BUILDING, STATEMENT])
    waits = []
    root = ibkr_flex.fetch_statement("tok", "99", get=get, sleep=waits.append)
    assert root.tag == "FlexQueryResponse"
    assert calls[0] == ("SendRequest", {"t": "tok", "q": "99", "v": "3"})
    assert calls[1:] == [("GetStatement", {"t": "tok", "q": "8123", "v": "3"})] * 2
    assert waits == [2, 3]


def test_a_refused_token_says_why():
    refused = "<FlexStatementResponse><Status>Fail</Status><ErrorCode>1015</ErrorCode><ErrorMessage>Token is invalid.</ErrorMessage></FlexStatementResponse>"
    get, _ = _ibkr([refused])
    with pytest.raises(ibkr_flex.IbkrFlexError, match="1015: Token is invalid"):
        ibkr_flex.fetch_statement("tok", "99", get=get, sleep=lambda s: None)


def test_the_book_reads_positions_in_yahoo_symbols_and_cash_in_the_base_currency():
    get, _ = _ibkr([SENT, STATEMENT])
    book = ibkr_flex.book_from_statement(ibkr_flex.fetch_statement("tok", "99", get=get, sleep=lambda s: None))
    assert book["cash"] == 8421.55 and book["currency"] == "AUD"
    assert book["account"] == "U•••567" and book["as_of"] == "2026-11-06"
    assert book["positions"] == [
        {"ticker": "0700.HK", "quantity": 100.0, "average_price": 380.0, "currency": "HKD", "account": "IBKR"},
        {"ticker": "BRK-B", "quantity": 4.0, "average_price": 455.1, "currency": "USD", "account": "IBKR"},
        {"ticker": "BTC-USD", "quantity": 0.05, "average_price": 61000.0, "currency": "USD", "account": "IBKR"},
        # The summary, not lot + summary.
        {"ticker": "NXT.AX", "quantity": 250.0, "average_price": 12.4, "currency": "AUD", "account": "IBKR"},
    ]
    # Marked as IBKR's, so a later sync replaces these and leaves other accounts alone.
    assert book["cash_balances"] == [{"account": "IBKR", "amount": 8421.55, "currency": "AUD"}]
    assert any("Left out 1 options position" in note for note in book["notes"])


def test_a_query_without_open_positions_says_how_to_add_them():
    bare = STATEMENT.split("<OpenPositions>")[0] + "</FlexStatement></FlexStatements></FlexQueryResponse>"
    with pytest.raises(ibkr_flex.IbkrFlexError, match="add Open Positions"):
        ibkr_flex.book_from_statement(ibkr_flex._parse(bare))


def test_margin_debt_is_saved_as_no_cash_and_said_so():
    margin = STATEMENT.replace('endingCash="8421.55"', 'endingCash="-1500"')
    book = ibkr_flex.book_from_statement(ibkr_flex._parse(margin))
    assert book["cash"] == 0.0 and any("margin loan" in note for note in book["notes"])


def test_a_network_error_never_carries_the_token(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("Max retries exceeded with url: /SendRequest?t=SECRET-TOKEN&q=99")

    monkeypatch.setattr(ibkr_flex.requests, "get", boom)
    with pytest.raises(ibkr_flex.IbkrFlexError) as caught:
        ibkr_flex._get("https://x/SendRequest", {"t": "SECRET-TOKEN"}, "SECRET-TOKEN")
    assert "SECRET-TOKEN" not in str(caught.value) and caught.value.__cause__ is None


@pytest.mark.parametrize(("symbol", "exchange", "category", "currency", "ticker"), [
    ("NXT", "ASX", "STK", "AUD", "NXT.AX"), ("BP.", "LSE", "STK", "GBP", "BP.L"), ("RCI.B", "TSE", "STK", "CAD", "RCI-B.TO"),
    ("ETH", "PAXOS", "CRYPTO", "USD", "ETH-USD"), ("AAPL", "NASDAQ", "STK", "USD", "AAPL"),
])
def test_ibkr_symbols_map_to_yahoo(symbol, exchange, category, currency, ticker):
    assert ibkr_flex.yahoo_symbol(symbol, exchange, category, currency) == (ticker, None)


def test_the_endpoint_returns_the_book_for_review_and_saves_nothing(monkeypatch):
    monkeypatch.setenv(ibkr_flex.TOKEN_NAME, "tok")
    monkeypatch.setenv(ibkr_flex.QUERY_NAME, "99")
    get, _ = _ibkr([SENT, STATEMENT])
    monkeypatch.setattr(ibkr_flex, "_get", get)
    monkeypatch.setattr(ibkr_flex.time, "sleep", lambda s: None)
    with TestClient(app) as client:
        before = client.get("/api/portfolio").json()
        body = client.post("/api/portfolio/ibkr").json()
        assert [p["ticker"] for p in body["positions"]] == ["0700.HK", "BRK-B", "BTC-USD", "NXT.AX"]
        assert client.get("/api/portfolio").json() == before
    monkeypatch.delenv(ibkr_flex.TOKEN_NAME)
    with TestClient(app) as client:
        response = client.post("/api/portfolio/ibkr")
    assert response.status_code == 400 and "IBKR_FLEX_TOKEN" in response.json()["detail"]
    assert portfolio_api.import_book is ibkr_flex.import_book


@pytest.mark.parametrize(("written", "iso"), [
    ("20261106", "2026-11-06"), ("2026-11-06", "2026-11-06"), ("06-Nov-26", "2026-11-06"),
    ("11/16/2026", "2026-11-16"), ("16/11/2026", "2026-11-16"), ("16/11/26", "2026-11-16"),
    ("11/06/2026", None),  # 6 Nov or 11 Jun: the statement does not say which, so no date beats a wrong one
    ("20261106;101500", "2026-11-06"), ("", None), ("soon", None),
])
def test_the_statement_date_is_read_in_any_of_ibkr_s_date_formats(written, iso):
    assert ibkr_flex._iso(written) == iso


def _cash_only(report: str) -> dict:
    statement = (f'<FlexQueryResponse><FlexStatements count="1"><FlexStatement accountId="U1234567" toDate="20261106">'
                 f'<AccountInformation currency="AUD" /><OpenPositions /><CashReport>{report}</CashReport>'
                 f'</FlexStatement></FlexStatements></FlexQueryResponse>')
    return ibkr_flex.book_from_statement(ibkr_flex._parse(statement))


def test_cash_in_the_base_currency_alone_needs_no_summary_row():
    assert _cash_only('<CashReportCurrency currency="AUD" endingCash="500" />')["cash"] == 500.0
    # Two currencies with no base total cannot be added up here.
    book = _cash_only('<CashReportCurrency currency="AUD" endingCash="500" />'
                      '<CashReportCurrency currency="USD" endingCash="70" />')
    assert book["cash"] is None and any("Ending Cash" in note for note in book["notes"])


def test_a_cash_report_without_ending_cash_says_which_fields_to_add():
    book = _cash_only('<CashReportCurrency currency="BASE_SUMMARY" />')
    assert book["cash"] is None
    assert any("select all fields" in note for note in book["notes"])
