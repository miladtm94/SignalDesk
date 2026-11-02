# Signal Desk web research console

Signal Desk is a single-user, local-first web layer over TradingAgents. It is a
research notebook, not an execution engine: it starts analyses and backtests,
preserves each agent section as it arrives, records the exact model
configuration, and makes past calls easier to annotate, compare and score.

## Local setup

The web console has its own dependencies so the upstream package remains
untouched. TradingAgents needs Python 3.11 or later; `make venv` refuses an
older `.venv` and says how to recreate it.

```bash
make install web-install
make web-dev
```

The single `make web-dev` process starts and supervises both the backend and
frontend. Press Ctrl+C once to stop both. Open `http://127.0.0.1:5174`. The API listens only on
`http://127.0.0.1:8765` by default. API keys can be supplied through the
existing environment variables or saved from Settings; saved values are
encrypted in the console data directory and only their final four characters
are returned.

For backend-only development, run `make web-backend`. When the Docker console
already holds the default ports, move the development pair:
`TRADING_CONSOLE_PORT=8766 TRADING_CONSOLE_FRONTEND_PORT=5175 make web-dev`
(the frontend's `/api` proxy follows the backend port).

For a containerized local deployment:

```bash
docker compose up --build web-backend web-frontend
```

Open `http://127.0.0.1:4173`.

## Running on a Claude or ChatGPT plan

Settings → Subscriptions shows whether each plan is ready, and the provider
picker lists them as "Claude plan" and "ChatGPT plan". A plan-billed run shows
"Billed to plan" instead of a dollar estimate; for the Claude plan the run also
records what its tokens would have cost at API list prices.

- **Claude Pro or Max.** Every model call runs through the Claude Code installed
  on the backend's machine (headless, with its tools, MCP servers, plugins,
  hooks and settings off); the console never reads the Claude credential.
  Sign in once in a terminal on that machine with `tradingagents login claude`
  (or `make login-claude`), then press "Check again". `TRADINGAGENTS_CLAUDE_CLI`
  names the executable when it is not on PATH; editors' bundled copies and the
  Agent SDK's (`make install-claude`) are found automatically. Install the SDK
  only through that target: on its own, `pip install ".[claude]"` lets its `mcp`
  dependency upgrade Starlette past what the console's pinned FastAPI supports.
- **In Docker.** Build with `WITH_CLAUDE_CODE=1 docker compose up --build -d
  web-backend web-frontend` to add Claude Code to the image. Then, in Settings →
  Subscriptions, press **Sign in with Claude**, approve on claude.com, and paste
  the code claude.com shows. This is Claude Code's own login running in the
  container: it keeps and refreshes the sign-in in the data volume
  (`CLAUDE_CONFIG_DIR`), so it lasts across restarts and rebuilds, and the console
  only passes on the one-time code. Press **Make Claude the default provider** to
  start every new analysis and backtest on it. Alternatively, save a `claude
  setup-token` token as `CLAUDE_CODE_OAUTH_TOKEN` in Settings → Provider
  credentials or `.env`.
- **ChatGPT Plus or Pro.** Sign in with `tradingagents login chatgpt` on the
  backend's machine.

Plan access is for your own use: Anthropic does not allow Claude plan access to
be shared or resold, so do not expose a plan-backed console to other people.

## Holdings

The Holdings page keeps one book: positions (signed quantities, negative for a
short, with an optional average price and the currency it is in), free cash
and the cash currency. A position's currency is filled in from its ticker's
exchange (NVDA in USD, NXT.AX in AUD) and can be changed; the agents read it
("average price 180.00 USD", plus a note when it differs from the cash
currency). The page shows each holding's latest close and unrealized P/L in that
currency, and exactly what the decision agents will read for any ticker.

The book is saved in the console's database and kept for every later session.
With **Use by default** on (the default for a new book), New analysis and New
backtest start on "Decide against my book"; each launch can still switch to no
portfolio context. Each console keeps its own database (the Docker console in
its data volume, `make web-dev` in `backend/data/`), so **Export** downloads the
book as a JSON file and **Import** loads one into the editor (press Save to keep
it). The same file works with the CLI's `--portfolio`.

Choosing "Decide against my book" when launching an analysis or a backtest
gives the book to the trader, the risk analysts and the portfolio manager; the
analysts and researchers stay position-neutral, as upstream designs them. Three
states stay distinct: no book (no portfolio context), a flat book (saved with no
positions) and a book with positions. Each run stores the exact book it was
launched with, and that snapshot is part of its checkpoint signature, so editing
the book later never changes or resumes a past run with different holdings.

## Backtests

Backtests → New backtest takes up to 12 tickers, a first and last date and a
step in days. The grid stops at today (as upstream's `iter_grid` does) and is
capped at `TRADING_CONSOLE_BACKTEST_MAX_CELLS` cells (60 by default), because
every cell is a complete multi-agent run.

- Cells run one after another, ticker by ticker and date by date. Each is an
  ordinary console run, without checkpoints, whose live sections, report and
  notes open from the backtest; backtest cells stay out of the analysis history.
- All cells share a memory log of the backtest's own, so the console's live
  memory log is never written to, and a later cell learns only from the sweep's
  earlier decisions whose outcome was known by its date.
- After the last cell, every decision whose holding window has traded is
  settled: entered at the first close after its date, held for
  `holding_period_days` trading days, and scored on alpha against the
  instrument's regional benchmark. A cell too recent to score stays pending;
  "Settle" scores it once the market has traded through its window.
- Stop ends the running cell at its next step; Resume re-runs the stopped,
  failed and unstarted cells and settles again. A backtest interrupted by a
  backend restart becomes resumable.
- Scores come from upstream's `summarize` (decisions, hit rate and mean alpha
  by rating), with the overall and per-ticker figures beside them. They are
  indicative: one sampling per cell, and news and social feeds are not
  archived. It is not a portfolio simulation.

## Reading a report

Each agent's section is shown as formatted text, with the model's wording left
as it is:

- Tables render as tables, and in columns that interpret a figure
  (Interpretation, Signal, Bias…) each cell gets 🟢, 🔴 or 🟡 for bullish,
  bearish, or mixed and cautious wording.
- The decision fields (Rating, Action, Entry Price, Stop Loss, Price Target,
  Time Horizon) appear as tiles at the top of the section, and the trader's
  `FINAL TRANSACTION PROPOSAL` appears as a banner at the end.
- Longer fields (Executive Summary, Investment Thesis, Strategic Actions…) are
  labelled blocks, and headings get an emoji for their topic (📈 trend,
  ⚡ momentum, 🏦 balance sheet, 📋 summary table…).
- Debates with more than one round show a header for each turn, such as
  🐂 Bull analyst · Round 2.

The markers and emojis are reading aids picked from the words used, not extra
analysis. The Markdown download is the agents' text unchanged.

## Stopping and resuming an analysis

A running analysis has a Stop button. It ends at the next agent step; with
checkpointing on, Resume continues from the last completed step instead of
starting over. Runs that failed (a provider error, a reached plan limit, a
backend restart) resume the same way.

## Runtime state

All console-owned state is redirected under `backend/data/` (or
`TRADING_CONSOLE_DATA_DIR`):

- `console.sqlite3` — runs, sections, events, decisions, usage, notes, keys,
  holdings and backtests. Columns added by a newer release are added to an
  existing database at start-up.
- `checkpoints/` — LangGraph per-ticker checkpoint databases
- `memory/` — the live TradingAgents decision memory log
- `results/` — internal JSON state logs; `results/backtests/<id>/` holds each
  backtest's own memory log and state logs
- `claude-code/` — Claude Code's settings in the Docker image (`CLAUDE_CONFIG_DIR`)

Completed markdown report trees are written to the repository-level
`reports/` directory (or `TRADING_CONSOLE_REPORTS_DIR`). Docker bind-mounts
that directory, so reports remain available on the host after containers stop.

The directory is git-ignored. `POST /api/runs` and `POST /api/backtests` are
rate-limited in process to three launches per minute per client. Set
`TRADING_CONSOLE_DAILY_CAP_USD` to a positive value to turn on the optional
estimated daily spend cap; plan-billed runs count as zero toward it.

## Security boundary

This release deliberately has no login and binds to localhost. Do not expose it
to a public network: it can hold paid API credentials, run on your Claude or
ChatGPT plan, and trigger costly model calls. If the scope changes beyond a
private, single-user console, authentication, CSRF protection, a shared rate
limiter, and a real task queue become release requirements.
