# Web console architecture

## Boundaries

`backend/adapters/tradingagents_adapter.py` is the only console module allowed
to import `tradingagents`. It owns construction of `TradingAgentsGraph`, the
streaming path (`stream_run`, which reports each parallel analyst as it files),
checkpoint setup, section mapping, report saving, the book (`PortfolioContext`),
backtest dates, outcomes and scores, plan sign-in status, and compatibility
fallback. The console itself changes no upstream behaviour: its timeout is
applied by wrapping upstream's tier-client factory, and everything else goes
through upstream's public lifecycle helpers.

The fork does extend the core package in one place, deliberately: the `claude`
provider (`tradingagents/llm_clients/claude_code.py` and `claude_client.py`),
registered beside upstream's `chatgpt` provider so the CLI, Python API and
console share it. `docs/UPSTREAM_UPGRADE.md` lists every upstream file the fork
touches.

The FastAPI process runs blocking agent work in `asyncio.to_thread()`. Each
completed or expanded section is upserted in SQLite, and every progress event
is appended to `run_events` before it is broadcast to WebSocket subscribers.
A reconnecting client first receives the persisted event sequence, so live UI
state is reconstructible after refresh. A Stop request is a flag the adapter
polls between graph steps; the run then ends as `stopped`, keeping its
checkpoint.

SQLite is accessed through SQLAlchemy. Indexes reflect the query paths used by
history (`ticker + created_at`, `status + created_at`, section lookup, event
sequence, notes, backtest cells). `init_database()` adds columns a newer release
introduced to an existing database (they must be nullable). A future
PostgreSQL move is therefore an engine and migration change rather than an
application rewrite.

## The Claude plan provider

Anthropic keeps Claude plan credentials inside Claude Code and claude.ai, so the
provider never authenticates to Anthropic itself. Each LangChain call becomes one
headless Claude Code process:

- `claude -p --output-format json` with the agent's system prompt in a file,
  `--tools ""`, `--strict-mcp-config`, `--setting-sources ""`,
  `--disable-slash-commands`, `--no-session-persistence`, and an empty private
  working directory: no tool, MCP server, plugin, hook, project setting or
  CLAUDE.md of the person's coding setup reaches the prompt, and no session is
  saved.
- The environment loses every `ANTHROPIC_*` variable (an API key would bill the
  API instead of the plan) and the variables a parent Claude Code session sets
  for its children; `CLAUDE_CODE_OAUTH_TOKEN` is kept for containers.
- A conversation with tool results is written out as a transcript. Tool calls
  come back through `--json-schema` in a checked envelope (`tool_calls` +
  `content`); the tools themselves still run in the graph's tool nodes, exactly
  as for every other provider. `with_structured_output` asks for the Pydantic
  schema directly and validates the answer into it.
- Claude Code attaches the machine's environment, today's date included, to
  every call. A session note tells the model that context describes the machine,
  not the task, so a historical or backtest run keeps its own date as "now"
  rather than reading its data as weeks stale.
- Errors are classified into a reached plan limit (no retry; a checkpointed run
  resumes after the reset), a refused sign-in, an unknown model, and transient
  failures, which are retried with backoff.

## Decision data

The adapter persists structured JSON when upstream state exposes a Pydantic
object or `*_structured` companion value. It never regexes decision markdown.
Since v0.5.2 each decision has one name in the state: the Research Manager's is
`investment_plan`, the Portfolio Manager's `final_trade_decision`, and its rating
`final_rating` (read with `run_rating`). Other denormalized fields remain
nullable while upstream keeps only rendered markdown. See
`KNOWN_UPSTREAM_ISSUES.md`.

## The book

The saved book is one validated `PortfolioContext` document. A run launched
against it stores a snapshot (`runs.portfolio_snapshot`), passes it to
`create_run_state`, `begin_checkpoint` and `clear_checkpoint_on_success`
(the book's fingerprint is part of upstream's checkpoint signature), and shows
the rendered context the decision agents read. `None` (no context) and a flat
book stay distinct end to end.

## Backtests

A backtest is a grid of cells; each cell is an ordinary `runs` row with
`backtest_id` set, executed by the run orchestrator one at a time from an async
sweep task. Cells use the backtest's own `results_dir` and memory log under
`results/backtests/<id>/`, mirroring upstream's `run_backtest`, and run without
checkpoints (a stopped or failed cell is re-run whole). After the cells, the
sweep settles each ticker with `settle_pending`; outcomes are read back from the
memory log (`load_entries`) and scored with upstream's `summarize`. A Stop sets a
flag for the sweep and stops the running cell between steps; a backend restart
leaves the backtest `stopped` and resumable.

## Deployment decision

The console is intentionally local-only, matching the engineering brief. The
frontend and backend have separate containers, both bound to loopback by the
provided Compose file. Redis, Celery, hosted auth, and multi-user ownership are
deferred until the operating model genuinely changes.
