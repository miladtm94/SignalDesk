# Signal Desk web research console

Signal Desk is a single-user, local-first web layer over TradingAgents. It is a
research notebook, not an execution engine: it starts analyses,
preserves each agent section as it arrives, records the exact model
configuration, and makes past calls easier to annotate and compare.

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
  Agent SDK's (`make install-claude`) are found automatically.
- **In Docker.** Build with `WITH_CLAUDE_CODE=1 docker compose build web-backend`
  to add Claude Code to the image, run `claude setup-token` on a machine where you
  can sign in, and save the token in Settings → Provider credentials as
  `CLAUDE_CODE_OAUTH_TOKEN` (or put it in `.env`).
- **ChatGPT Plus or Pro.** Sign in with `tradingagents login chatgpt` on the
  backend's machine.

Plan access is for your own use: Anthropic does not allow Claude plan access to
be shared or resold, so do not expose a plan-backed console to other people.

## Holdings

The Holdings page keeps one book: positions (signed quantities, negative for a
short, with an optional average price in the instrument's quote currency), free
cash and a currency label. It shows each holding's latest close and unrealized
P/L, and exactly what the decision agents will read for any ticker.

Choosing "Decide against my book" when launching an analysis gives the book to the trader, the risk analysts and the portfolio manager; the
analysts and researchers stay position-neutral, as upstream designs them. Three
states stay distinct: no book (no portfolio context), a flat book (saved with no
positions) and a book with positions. Each run stores the exact book it was
launched with, and that snapshot is part of its checkpoint signature, so editing
the book later never changes or resumes a past run with different holdings.

## Stopping and resuming an analysis

A running analysis has a Stop button. It ends at the next agent step; with
checkpointing on, Resume continues from the last completed step instead of
starting over. Runs that failed (a provider error, a reached plan limit, a
backend restart) resume the same way.

## Runtime state

All console-owned state is redirected under `backend/data/` (or
`TRADING_CONSOLE_DATA_DIR`):

- `console.sqlite3` — runs, sections, events, decisions, usage, notes, keys
  and holdings. Columns added by a newer release are added to an
  existing database at start-up.
- `checkpoints/` — LangGraph per-ticker checkpoint databases
- `memory/` — the live TradingAgents decision memory log
- `results/` — internal JSON state logs
- `claude-code/` — Claude Code's settings in the Docker image (`CLAUDE_CONFIG_DIR`)

Completed markdown report trees are written to the repository-level
`reports/` directory (or `TRADING_CONSOLE_REPORTS_DIR`). Docker bind-mounts
that directory, so reports remain available on the host after containers stop.

The directory is git-ignored. `POST /api/runs` is rate-limited in process to
three launches per minute per client. Set
`TRADING_CONSOLE_DAILY_CAP_USD` to a positive value to turn on the optional
estimated daily spend cap; plan-billed runs count as zero toward it.

## Security boundary

This release deliberately has no login and binds to localhost. Do not expose it
to a public network: it can hold paid API credentials, run on your Claude or
ChatGPT plan, and trigger costly model calls. If the scope changes beyond a
private, single-user console, authentication, CSRF protection, a shared rate
limiter, and a real task queue become release requirements.
