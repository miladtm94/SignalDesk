# Known upstream integration issues

## Typed decision objects are not retained in graph state (v0.7.0)

TradingAgents uses Pydantic schemas for the Research Manager, Trader, Portfolio
Manager, and Sentiment Analyst, but still renders those objects to markdown
before returning node state. Since v0.5.2 the Portfolio Manager's rating is kept
as `final_rating`, which the console reads with `run_rating()`; trader action,
prices, time horizon, and sentiment confidence cannot be denormalized without
reparsing prose.

Signal Desk deliberately leaves those database fields null rather than regexing
markdown. The adapter already recognizes future `*_structured` companion state
keys or direct Pydantic values, so an upstream change that retains typed values
can be adopted in one module.

## Windows lock tests fail on macOS (v0.7.0)

Three tests in `tests/test_cache_writes.py` simulate Windows file locking with
`errno.EDEADLOCK`, which macOS's `errno` module does not define, so they fail
there (they pass on Linux and Windows). The code path they cover only runs on
Windows. Run the suite as `pytest --deselect tests/test_cache_writes.py` on a
Mac, or ignore those three failures.
