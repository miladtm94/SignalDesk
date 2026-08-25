# Known upstream integration issues

## Typed decision objects are not retained in graph state (v0.3.1)

TradingAgents uses Pydantic schemas for the Research Manager, Trader, Portfolio
Manager, and Sentiment Analyst, but v0.3.1 immediately renders those objects to
markdown before returning node state. The original typed objects are therefore
not available to external callers. `process_signal()` safely exposes the final
five-tier portfolio rating; trader action, prices, time horizon, and sentiment
confidence cannot be denormalized without reparsing prose.

Signal Desk deliberately leaves those database fields null rather than regexing
markdown. The adapter already recognizes future `*_structured` companion state
keys or direct Pydantic values, so an upstream change that retains typed values
can be adopted in one module.
