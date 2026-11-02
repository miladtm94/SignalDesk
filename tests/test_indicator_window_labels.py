"""A day after the newest bar reads as a session not yet closed, not as a holiday.

A run dated by a calendar ahead of the exchange (Sydney's Monday is New York's
Sunday) asks for a day the market has not traded yet; calling it a weekend or
holiday told the model a trading day was a market closure.
"""

import pytest

from tradingagents.dataflows.vendors.yahoo import market


@pytest.mark.unit
def test_days_after_the_latest_close_are_not_called_holidays(monkeypatch):
    bars = {"2026-10-23": "41.0", "2026-10-26": "42.0", "2026-10-30": "44.5"}
    monkeypatch.setattr(market, "_get_stock_stats_bulk", lambda symbol, indicator, as_of: bars)

    out = market.get_stock_stats_indicators_window("FCX", "rsi", "2026-11-02", 10)

    assert "2026-11-02: N/A: no closed session yet (latest close is 2026-10-30)" in out
    assert "2026-10-30: 44.5" in out
    assert "2026-10-25: N/A: Not a trading day (weekend or holiday)" in out
