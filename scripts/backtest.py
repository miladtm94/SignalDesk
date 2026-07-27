"""Historical smoke test: run the full agent pipeline on past dates and score
each decision against what the stock actually did afterward.

There is no dedicated backtest engine in this repo (backtrader is an unused
dependency) — this script is the loop-over-dates approach the README's
"Reproducibility" section describes. Each date runs a full multi-agent LLM
pipeline, so keep the date list short against a free-tier provider quota.

Usage:
    python scripts/backtest.py NVDA 2025-01-15 2025-02-14 2025-03-17
"""

from __future__ import annotations

import sys
import time

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

# Pause between dates so back-to-back propagate() calls don't hit Reddit's
# RSS search within the same per-IP rate-limit window. Each date now makes a
# single combined multi-subreddit request (see dataflows/reddit.py #1142),
# but Reddit's bucket has been observed taking up to ~60s to refill, so this
# still needs to be a real gap rather than a token pause.
INTER_DATE_DELAY_SECONDS = 60

# Portfolio Manager rating -> expected direction of the subsequent move.
RATING_DIRECTION = {
    "Sell": -1,
    "Underweight": -1,
    "Hold": 0,
    "Overweight": 1,
    "Buy": 1,
}


def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/backtest.py TICKER DATE [DATE ...]")
        sys.exit(1)

    ticker = sys.argv[1]
    dates = sys.argv[2:]

    ta = TradingAgentsGraph(config=DEFAULT_CONFIG.copy())
    benchmark = ta._resolve_benchmark(ticker)

    rows = []
    for i, date in enumerate(dates):
        if i > 0:
            time.sleep(INTER_DATE_DELAY_SECONDS)
        print(f"\n=== {ticker} @ {date} ===")
        _, rating = ta.propagate(ticker, date)
        raw, alpha, days = ta._fetch_returns(ticker, date, benchmark=benchmark)
        direction = RATING_DIRECTION.get(rating, 0)
        correct = None
        if raw is not None:
            correct = (direction == 0 and abs(raw) < 0.01) or (
                direction != 0 and (direction > 0) == (raw > 0)
            )
        rows.append((date, rating, raw, alpha, days, correct))
        print(f"Rating: {rating}  |  {days}-day raw return: {raw}  alpha vs {benchmark}: {alpha}")

    print("\n=== Summary ===")
    header = f"{'Date':<12}{'Rating':<14}{'Raw Ret':>10}{'Alpha':>10}{'Days':>6}{'Correct':>10}"
    print(header)
    for date, rating, raw, alpha, days, correct in rows:
        raw_s = f"{raw:.2%}" if raw is not None else "n/a"
        alpha_s = f"{alpha:.2%}" if alpha is not None else "n/a"
        days_s = str(days) if days is not None else "n/a"
        correct_s = "n/a" if correct is None else ("yes" if correct else "no")
        print(f"{date:<12}{rating:<14}{raw_s:>10}{alpha_s:>10}{days_s:>6}{correct_s:>10}")


if __name__ == "__main__":
    main()
