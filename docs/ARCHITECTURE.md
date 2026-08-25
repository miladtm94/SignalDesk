# Web console architecture

## Boundaries

`backend/adapters/tradingagents_adapter.py` is the only console module allowed
to import `tradingagents`. It owns construction of `TradingAgentsGraph`, the
semi-private accumulated-state streaming path, checkpoint setup, section
mapping, report saving, and compatibility fallback. No file inside the
upstream `tradingagents/` or `cli/` packages is changed by the console.

The FastAPI process runs blocking agent work in `asyncio.to_thread()`. Each
completed or expanded section is upserted in SQLite, and every progress event
is appended to `run_events` before it is broadcast to WebSocket subscribers.
A reconnecting client first receives the persisted event sequence, so live UI
state is reconstructible after refresh.

SQLite is accessed through SQLAlchemy. Indexes reflect the query paths used by
history (`ticker + created_at`, `status + created_at`, section lookup, event
sequence, and notes). A future PostgreSQL move is therefore an engine and
migration change rather than an application rewrite.

## Decision data

The adapter persists structured JSON when upstream state exposes a Pydantic
object or `*_structured` companion value. It never regexes decision markdown.
At v0.3.1 the portfolio rating is also available through the supported
`process_signal()` method; other denormalized fields remain nullable when the
upstream state carries only rendered markdown. See `KNOWN_UPSTREAM_ISSUES.md`.

## Deployment decision

The console is intentionally local-only, matching the engineering brief. The
frontend and backend have separate containers, both bound to loopback by the
provided Compose file. Redis, Celery, hosted auth, and multi-user ownership are
deferred until the operating model genuinely changes.
