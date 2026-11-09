from __future__ import annotations

import os
import tempfile

# Keep test lifecycle recovery, database writes and report trees away from real
# console runs and the repository's reports/ folder.
os.environ["TRADING_CONSOLE_DATA_DIR"] = tempfile.mkdtemp(prefix="trading-console-tests-")
os.environ["TRADING_CONSOLE_REPORTS_DIR"] = tempfile.mkdtemp(prefix="trading-console-reports-")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_launch_limit():
    """The launch limiter counts per client across requests; each test starts with none."""
    from backend.app.api import runs

    runs._launches.clear()
    yield
    runs._launches.clear()


@pytest.fixture(autouse=True)
def _no_live_price_structure(monkeypatch):
    """The chart's weekly and monthly views read five years of prices from Yahoo;
    a test that wants them supplies its own bars."""
    from backend.app.api import runs

    monkeypatch.setattr(runs, "load_price_structure", lambda ticker, trade_date: None)
