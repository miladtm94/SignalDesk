"""Score past decisions for one ticker on the dates you name, with upstream's backtest.

Each date is a full multi-agent run. Decisions go to a memory log of the sweep's
own (``results_dir/backtest/<run_id>``), never your live one, and each is scored
on its alpha against the instrument's regional benchmark over the holding window,
from the first close after its date. ``tradingagents backtest`` runs the same
engine over a regular date grid, and Signal Desk's Backtests view in a browser.

Usage:
    python scripts/backtest.py NVDA 2025-01-15 2025-02-14 2025-03-17
    python scripts/backtest.py NVDA 2025-01-15 2025-02-14 --run-id 20250320_101500   # continue / settle
"""

from __future__ import annotations

import sys
from datetime import datetime

from tradingagents.backtest import run_backtest, summarize
from tradingagents.default_config import DEFAULT_CONFIG


def main() -> None:
    args, run_id = sys.argv[1:], None
    if "--run-id" in args:
        at = args.index("--run-id")
        if at + 1 >= len(args):
            print("--run-id needs the id an earlier sweep printed")
            sys.exit(1)
        run_id = args[at + 1]
        args = args[:at] + args[at + 2:]
    if len(args) < 2:
        print("Usage: python scripts/backtest.py TICKER DATE [DATE ...] [--run-id ID]")
        sys.exit(1)

    ticker, dates = args[0].strip().upper(), args[1:]
    for date in dates:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            print(f"Dates must be YYYY-MM-DD; got {date!r}")
            sys.exit(1)

    result = run_backtest(
        [ticker], dates, DEFAULT_CONFIG.copy(), run_id=run_id,
        progress=lambda done, total, name, date: print(f"[{done}/{total}] {name} {date}", flush=True),
    )
    print()
    print(summarize(result).render())
    print(f"\nRan {result.cells_run} cells, skipped {result.skipped}. Log: {result.log_path}")
    print(f"Continue it, or settle recent dates once they have traded: add --run-id {result.run_id}")
    for name, date, reason in result.failures:
        print(f"failed: {name} {date}: {reason}")
    for name, reason in result.settlement_failures:
        print(f"unsettled: {name}: {reason}")


if __name__ == "__main__":
    main()
